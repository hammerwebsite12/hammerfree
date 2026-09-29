"""WebView-based Anker gate resolver (countdown + Turnstile).

The Anker gate page (`/download/<token>`) runs a short countdown, silently
solves a Cloudflare Turnstile challenge, then auto-navigates to
`/download-file/<hash>?cf-turnstile-response=<token>`. That download-file URL
is **single-use**: the server consumes the token and 302-redirects the browser
to the real CDN file (e.g. `https://tunnel5.dlproxy.uk/download/...`).

We therefore never hand the download-file URL to the downloader (it would be
already spent, returning an "Ops matey no downloads here" HTML page). Instead we
watch the WebView's network traffic and capture the CDN URL the redirect lands
on. That CDN URL supports Range requests and is downloadable by a fresh session,
so it is what we return to the IDM downloader.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
import threading
import time
from typing import Any, Callable
from urllib.parse import urlparse

_FINAL_HOST_MARKERS = ("dlproxy.uk", "datanodes.to", "trashbytes")
_ARCHIVE_SUFFIXES = (".zip", ".7z", ".rar", ".tar", ".gz", ".001")
_POLL_INTERVAL = 0.25
_GATE_CLI_FLAG = "--qp-gate-worker"
_active_gate_window: Any | None = None
_active_gate_lock = threading.Lock()

# Detect the "expired / invalid download link" page so we can retry with a
# freshly minted gate URL instead of waiting for the whole timeout.
_GATE_ERROR_JS = """
(() => {
  try {
    const text = (document.body && document.body.innerText) ? document.body.innerText : '';
    const title = document.title || '';
    const blob = (title + ' ' + text).toLowerCase();
    if (blob.includes('download unavailable') || blob.includes('expired download link')
        || blob.includes('invalid or expired download link')
        || blob.includes('no downloads here')) {
      return 'expired';
    }
  } catch (e) {}
  return '';
})()
"""

# Click-only fallback: the gate page normally finishes its own countdown and
# auto-navigates to the download-file hop (which redirects to the CDN). We must
# NOT call Turnstile's execute() ourselves — the page reads the silently issued
# token via awaitTurnstileToken(), and calling execute() on the already-rendered
# widget breaks it ("execute() on a widget that is already executing"). So we
# only click the revealed "Download Now" button as a safety net if the page
# stalls before auto-navigating.
_GATE_CLICK_JS = """
(() => {
  try {
    const root = document.querySelector('.download-page');
    if (!root || !root._x_dataStack || !root._x_dataStack.length) return '';
    const data = root._x_dataStack[0];
    if (data.showButton && !data.downloadClicked
        && typeof data.handleDownloadClick === 'function') {
      try { data.handleDownloadClick(); } catch (e) {}
      return 'clicked';
    }
    return '';
  } catch (e) {
    return '';
  }
})()
"""

# Shared Alpine gate-state reader (also used by the in-page poll timer below).
_GATE_READ_STATUS_FN = """
window.__qpReadGateStatus = function() {
  try {
    const root = document.querySelector('.download-page');
    if (!root || !root._x_dataStack || !root._x_dataStack.length) return '';
    const d = root._x_dataStack[0];
    const totalWait = Math.max(0, parseInt(d.totalWait, 10) || 0);
    let countdown = Math.max(0, parseInt(d.countdown, 10) || 0);
    if (!countdown && totalWait && d.progressValue != null) {
      const done = Math.min(100, Number(d.progressValue) || 0);
      countdown = Math.max(0, Math.ceil(totalWait * (1 - done / 100)));
    }
    if (!countdown) {
      const el = root.querySelector('[x-show="countdown > 0"][x-text="countdown"]')
        || root.querySelector('[x-text="countdown"]');
      if (el) {
        const parsed = parseInt((el.textContent || '').trim(), 10);
        if (!isNaN(parsed) && parsed > 0) countdown = parsed;
      }
    }
    const state = String(d.state || '');
    const needsChallenge = !!d.needsChallenge;
    const showButton = !!d.showButton;
    const hasToken = !!d.hasToken;
    const awaitingChallenge = !!d.awaitingChallenge;
    const downloadClicked = !!d.downloadClicked || state === 'downloading';
    let phase = 'waiting';
    let message = 'Preparing download verification...';
    if (countdown > 0) {
      phase = 'countdown';
      message = 'Preparing download — ' + countdown + 's';
    } else if (downloadClicked || state === 'downloading') {
      phase = 'handoff';
      message = 'Passing to downloader...';
    } else if (showButton || state === 'ready') {
      phase = 'link_ready';
      message = 'Download link ready — starting...';
    } else if (hasToken && needsChallenge) {
      phase = 'turnstile_passed';
      message = 'Security check passed — preparing link...';
    } else if (hasToken) {
      phase = 'turnstile_passed';
      message = 'Security check passed';
    } else if (needsChallenge && awaitingChallenge) {
      phase = 'turnstile_interactive';
      message = 'Security check — click required';
    } else if (needsChallenge) {
      const pageText = (document.body && document.body.innerText) || '';
      if (pageText.indexOf('Verify you are human') >= 0) {
        phase = 'turnstile_interactive';
        message = 'Security check — click required';
      } else {
        phase = 'turnstile';
        message = 'Security check — please wait';
      }
    } else if (state === 'processing') {
      phase = 'processing';
      message = 'Processing download request...';
    } else if (state === 'loading') {
      phase = 'waiting';
      message = 'Preparing download verification...';
    }
    return JSON.stringify({ countdown: countdown, phase: phase, message: message });
  } catch (e) {
    return '';
  }
};
"""

# Auto-nudge Cloudflare Turnstile when it stalls on "Verifying..." (common on cloud
# VMs / AppOnFly). Silent widgets must not call execute(); interactive ones may.
_GATE_TURNSTILE_NUDGE_FN = """
window.__qpNudgeTurnstile = function() {
  try {
    const now = Date.now();
    if (window.__qpLastTurnstileNudge && (now - window.__qpLastTurnstileNudge) < 2500) {
      return '';
    }
    window.__qpLastTurnstileNudge = now;

    const root = document.querySelector('.download-page');
    const d = root && root._x_dataStack && root._x_dataStack.length ? root._x_dataStack[0] : null;
    if (d && (d.hasToken || d.showButton)) return 'done';

    if (d && d.needsChallenge && !d.hasToken && window.agTurnstile
        && typeof window.agTurnstile.execute === 'function') {
      const pageText = (document.body && document.body.innerText) || '';
      const interactive = !!d.awaitingChallenge
        || pageText.indexOf('Verify you are human') >= 0;
      if (interactive) {
        try {
          window.agTurnstile.execute();
          return 'execute';
        } catch (e) {}
      }
    }

    const clickTarget = (el) => {
      if (!el) return false;
      try {
        el.scrollIntoView({ block: 'center', inline: 'center' });
      } catch (e) {}
      const rect = el.getBoundingClientRect();
      if (!rect || rect.width < 2 || rect.height < 2) return false;
      const x = rect.left + rect.width / 2;
      const y = rect.top + rect.height / 2;
      for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
        el.dispatchEvent(new MouseEvent(type, {
          bubbles: true,
          cancelable: true,
          view: window,
          clientX: x,
          clientY: y,
        }));
      }
      try { el.click(); } catch (e) {}
      return true;
    };

    const selectors = [
      '.cf-turnstile',
      '.ag-turnstile',
      'iframe[src*="challenges.cloudflare.com"]',
      'iframe[src*="turnstile"]',
      '[data-sitekey]',
    ];
    for (let i = 0; i < selectors.length; i++) {
      const el = document.querySelector(selectors[i]);
      if (clickTarget(el)) return 'clicked:' + selectors[i];
    }

    const labels = document.querySelectorAll('span, div, label, p, button');
    for (let i = 0; i < labels.length; i++) {
      const el = labels[i];
      const text = (el.textContent || '').trim();
      if (text !== 'Verifying...' && text !== 'Verify you are human') continue;
      let host = el.closest('.cf-turnstile, .ag-turnstile, [data-sitekey]');
      if (!host) {
        let node = el.parentElement;
        for (let depth = 0; depth < 10 && node; depth++) {
          if (node.querySelector && node.querySelector('iframe')) {
            host = node;
            break;
          }
          node = node.parentElement;
        }
      }
      if (clickTarget(host || el.parentElement)) return 'clicked:verifying';
    }

    return '';
  } catch (e) {
    return '';
  }
};
"""

# Poll gate state inside the WebView JS thread, mirror into document.title, and let
# Python read it from network event handlers (main GUI thread). pywebview js_api on
# a second hidden window is unreliable, and HTTPS gate pages block fetch() to localhost.
_GATE_START_PROGRESS_POLL_JS = """
(() => {
  try {
""" + _GATE_READ_STATUS_FN + _GATE_TURNSTILE_NUDGE_FN + """
    if (window.__qpGateTitleTimer) return 'already';
    if (!window.__qpPing) window.__qpPing = new Image();
    const tick = () => {
      try {
        if (window.__qpApplyGateStealth) window.__qpApplyGateStealth();
        const raw = window.__qpReadGateStatus();
        if (!raw) return;
        document.title = 'QP|' + raw;
        window.__qpPing.src = '/favicon.ico?qp=' + Date.now();
        const data = JSON.parse(raw);
        if (data.phase === 'turnstile' || data.phase === 'turnstile_interactive') {
          window.__qpNudgeTurnstile && window.__qpNudgeTurnstile();
        }
      } catch (e) {}
    };
    tick();
    window.__qpGateTitleTimer = setInterval(tick, 250);
    return 'started';
  } catch (e) {
    return '';
  }
})()
"""

_GATE_READ_TITLE_JS = """
(() => {
  try {
    const t = document.title || '';
    return t.indexOf('QP|') === 0 ? t.slice(3) : '';
  } catch (e) {
    return '';
  }
})()
"""

_gate_sync_state = {"last_at": 0.0, "last_key": None}


def _publish_gate_progress(countdown: int, message: str, phase: str) -> None:
    from anker.gate_bridge import report_gate_progress

    report_gate_progress(countdown, message, phase)
    if os.environ.get("QP_GATE_WORKER") != "1":
        return
    try:
        import json
        import sys

        payload = {
            "countdown": max(0, int(countdown)),
            "status_message": (message or "").strip() or "Preparing download verification...",
            "message": (message or "").strip() or "Preparing download verification...",
            "phase": (phase or "waiting").strip(),
        }
        sys.stderr.write("QP_PROGRESS|" + json.dumps(payload, ensure_ascii=False) + "\n")
        sys.stderr.flush()
    except Exception:
        pass


def _sync_gate_progress_from_title(window) -> None:
    """Read mirrored gate JSON from document.title (call from GUI thread only)."""
    try:
        from anker.gate_native_click import process_pending_turnstile_click

        process_pending_turnstile_click(window)
    except Exception:
        pass
    now = time.time()
    if now - _gate_sync_state["last_at"] < 0.2:
        return
    _gate_sync_state["last_at"] = now
    try:
        raw = window.evaluate_js(_GATE_READ_TITLE_JS)
    except Exception:
        return
    if not isinstance(raw, str) or not raw.startswith("{"):
        return
    try:
        import json

        from anker.gate_bridge import report_gate_progress

        data = json.loads(raw)
        if not isinstance(data, dict):
            return
        countdown = max(0, int(data.get("countdown") or 0))
        message = str(data.get("message") or "Preparing download verification...").strip()
        phase = str(data.get("phase") or "waiting").strip()
        prefs = _read_gate_window_prefs()
        poll_state = getattr(window, "_qp_poll_state", None)
        if isinstance(poll_state, dict):
            poll_state["phase"] = phase
        if phase in ("turnstile", "turnstile_interactive"):
            try:
                window.evaluate_js(
                    "window.__qpNudgeTurnstile && window.__qpNudgeTurnstile()"
                )
            except Exception:
                pass
            if phase in ("turnstile", "turnstile_interactive"):
                _maybe_present_for_phase(window, phase, prefs)
            try:
                from anker.gate_native_click import (
                    native_nudge_turnstile,
                    process_pending_turnstile_click,
                    request_native_turnstile_click,
                )

                request_native_turnstile_click(window, phase=phase, force=True)
                process_pending_turnstile_click(window)
            except Exception:
                pass
        key = (countdown, message, phase)
        if _gate_sync_state.get("last_key") == key:
            return
        _gate_sync_state["last_key"] = key
        _publish_gate_progress(countdown, message, phase)
    except Exception:
        pass

_GATE_STOP_PROGRESS_POLL_JS = """
(() => {
  try {
    if (window.__qpGateTitleTimer) {
      clearInterval(window.__qpGateTitleTimer);
      window.__qpGateTitleTimer = null;
    }
  } catch (e) {}
  return '';
})()
"""

# Seconds to wait before the download-button click fallback kicks in.
_CLICK_FALLBACK_DELAY = 12.0
# Start auto-nudging Turnstile soon after the countdown finishes.
_TURNSTILE_NUDGE_START = 1.0
# Hidden mode: seconds of failed off-screen auto-clicks before showing the panel.
_HIDDEN_REVEAL_AFTER = 7.0

# Keep the verification WebView hidden by default (off-screen).
# Settings: hidden (default) or full debug page fallback.
_GATE_WINDOW_HIDDEN = True


def _read_gate_window_prefs() -> dict[str, str | bool]:
    try:
        from backend.server import get_settings

        settings = get_settings()
        mode = settings.verification_window_mode
    except Exception:
        mode = "hidden"
    return {
        "mode": mode,
        "show": mode != "hidden",
        "full": mode == "full",
        "hidden": mode == "hidden",
    }


# Optimized popup — sized to the Cloudflare Turnstile row only.
_GATE_WIDGET_WIDTH = 360
_GATE_WIDGET_HEIGHT = 88
# Window chrome (title bar + borders) added around the measured widget rect.
_GATE_WIDGET_PAD_W = 22
_GATE_WIDGET_PAD_H = 52
_GATE_LOAD_WIDTH = 520
_GATE_LOAD_HEIGHT = 620
_GATE_FULL_WIDTH = 520
_GATE_FULL_HEIGHT = 720
_GATE_OFFSCREEN_X = -2400
_GATE_OFFSCREEN_Y = -2400
_GATE_WIDGET_BG = "#2a3142"

# Injected as early as possible so users never see the upstream site chrome.
# The gate URL must still load (Turnstile token + redirect), but only the widget
# is made visible — everything else stays hidden for the lifetime of the page.
_GATE_WIDGET_STEALTH_JS = """
(() => {
  try {
    const keep = (el) => {
      if (!el) return false;
      if (el.closest && el.closest(
        '.cf-turnstile, .ag-turnstile, [data-sitekey], iframe[src*="turnstile"], iframe[src*="cloudflare"]'
      )) return true;
      const tag = (el.tagName || '').toLowerCase();
      return tag === 'html' || tag === 'head' || tag === 'body'
        || tag === 'script' || tag === 'style' || tag === 'link' || tag === 'meta';
    };
    const css = `
      html, body {
        background: #2a3142 !important;
        margin: 0 !important;
        padding: 6px !important;
        overflow: hidden !important;
        width: 100% !important;
        height: 100% !important;
        min-height: 0 !important;
      }
      ::-webkit-scrollbar { display: none !important; width: 0 !important; height: 0 !important; }
      header, footer, nav, picture, img, svg, video, canvas,
      .download-bg, .parallax-container, .parallax-bg, .smoke-top,
      .grid-reveal, .download-btn-reveal, #qp-gate-banner,
      [role="progressbar"], [role="banner"], [role="contentinfo"] {
        display: none !important;
        visibility: hidden !important;
      }
      .download-page {
        background: transparent !important;
        margin: 0 !important;
        padding: 0 !important;
        min-height: 0 !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
        overflow: hidden !important;
      }
      .download-page > *:not(.custom-container):not([data-sitekey]):not(.cf-turnstile):not(.ag-turnstile) {
        display: none !important;
      }
      .download-page .custom-container {
        background: transparent !important;
        box-shadow: none !important;
        border: none !important;
        margin: 0 !important;
        padding: 0 !important;
        width: auto !important;
        max-width: 100% !important;
        display: flex !important;
        align-items: center !important;
        justify-content: center !important;
      }
      .cf-turnstile, .ag-turnstile, [data-sitekey],
      iframe[src*="turnstile"], iframe[src*="cloudflare"] {
        display: block !important;
        visibility: visible !important;
        opacity: 1 !important;
        margin: 0 auto !important;
      }
    `;
    let style = document.getElementById('qp-gate-stealth-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'qp-gate-stealth-style';
      (document.head || document.documentElement).appendChild(style);
    }
    style.textContent = css;
    document.title = 'QuickPlay — Verification';
    const scrub = () => {
      document.querySelectorAll(
        'p, span, h1, h2, h3, h4, label, small, a, button'
      ).forEach((el) => {
        if (keep(el)) return;
        const text = (el.textContent || '').trim().toLowerCase();
        if (text.includes('ankergames') || text.includes('matey') || text.includes('loot')
            || text.includes('booty') || text.includes('support free')) {
          el.style.setProperty('display', 'none', 'important');
        }
      });
    };
    scrub();
    window.__qpApplyGateStealth = scrub;
    return 'stealth';
  } catch (e) {
    return '';
  }
})()
"""

# Report whether the challenge actually needs a human click, plus the widget
# rect so the popup can be sized to the Cloudflare box instead of guessing.
_GATE_CHALLENGE_STATE_JS = """
(() => {
  try {
    const el = document.querySelector(
      'iframe[src*="challenges.cloudflare.com"], iframe[src*="turnstile"],'
      + ' .cf-turnstile, .ag-turnstile, [data-sitekey]'
    );
    let w = 0;
    let h = 0;
    let visible = false;
    if (el) {
      const r = el.getBoundingClientRect();
      if (r && r.width > 20 && r.height > 20) {
        w = Math.ceil(r.width);
        h = Math.ceil(r.height);
        visible = true;
      }
    }
    const text = (document.body && document.body.innerText) || '';
    let interactive = text.indexOf('Verify you are human') >= 0;
    const root = document.querySelector('.download-page');
    const d = root && root._x_dataStack && root._x_dataStack.length ? root._x_dataStack[0] : null;
    let settled = false;
    if (d) {
      settled = !!(d.hasToken || d.showButton || d.downloadClicked || d.state === 'ready');
      if (!settled && (d.awaitingChallenge || (d.needsChallenge && visible))) {
        interactive = true;
      }
    }
    if (!settled && visible && !interactive) {
      const root = document.querySelector('.download-page');
      const st = root && root._x_dataStack && root._x_dataStack.length ? root._x_dataStack[0] : null;
      if (st && st.needsChallenge) interactive = true;
    }
    return JSON.stringify({
      interactive: interactive,
      visible: visible,
      settled: settled,
      w: w,
      h: h,
    });
  } catch (e) {
    return '';
  }
})()
"""

_GATE_TURNSTILE_READY_JS = """
(() => {
  try {
    const el = document.querySelector(
      'iframe[src*="turnstile"], iframe[src*="cloudflare"], .cf-turnstile, .ag-turnstile, [data-sitekey]'
    );
    if (!el) return false;
    const r = el.getBoundingClientRect();
    return !!(r && r.width > 20 && r.height > 20);
  } catch (e) {
    return false;
  }
})()
"""


_GATE_WIDGET_ONLY_JS = """
(() => {
  try {
    if (window.__qpApplyGateStealth) window.__qpApplyGateStealth();
    const widget = document.querySelector(
      '.cf-turnstile, .ag-turnstile, [data-sitekey], iframe[src*="turnstile"]'
    );
    if (widget) {
      try { widget.scrollIntoView({ block: 'center', inline: 'center' }); } catch (e) {}
    }
    return 'widget';
  } catch (e) {
    return '';
  }
})()
"""

_GATE_COMPACT_VIEW_JS = """
(() => {
  try {
    const css = `
      .download-page picture, .download-page .parallax-container,
      .download-page .grid-reveal, .download-bg, .smoke-top,
      .download-page .custom-container:last-of-type {
        display: none !important;
      }
    `;
    let style = document.getElementById('qp-gate-compact-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'qp-gate-compact-style';
      document.head.appendChild(style);
    }
    style.textContent = css;
    return 'compact';
  } catch (e) {
    return '';
  }
})()
"""

# Strip upstream site chrome in the verification WebView — users only see
# QuickPlay branding plus countdown / Turnstile / download button.
_GATE_MASK_JS = """
(() => {
  try {
    const css = `
      html, body {
        background: #0b0a0a !important;
        color: #f5f5f5 !important;
        overflow-x: hidden !important;
      }
      header, footer, nav, [role="banner"], [role="contentinfo"] {
        display: none !important;
      }
      a[href*="ankergames"], img[src*="ankergames"], link[href*="ankergames"] {
        display: none !important;
      }
      .download-bg, .parallax-container, .parallax-bg, .smoke-top,
      .download-page picture, .download-page .parallax-container {
        display: none !important;
      }
      .download-page {
        background: #0b0a0a !important;
        min-height: 100vh !important;
        display: flex !important;
        flex-direction: column !important;
        align-items: center !important;
        justify-content: center !important;
        padding: 24px 16px !important;
      }
      .download-page > .smoke-top { display: none !important; }
      #qp-gate-banner {
        width: min(420px, 100%);
        text-align: center;
        margin: 0 auto 20px;
        padding: 0 8px;
      }
      #qp-gate-banner .qp-title {
        font: 600 22px/1.2 Segoe UI, system-ui, sans-serif;
        color: #ff8c1a;
        letter-spacing: 0.02em;
        margin: 0 0 6px;
      }
      #qp-gate-banner .qp-sub {
        font: 400 13px/1.4 Segoe UI, system-ui, sans-serif;
        color: #a3a3a3;
        margin: 0;
      }
      .download-page .custom-container,
      .download-page .grid-reveal,
      .download-page [x-show="needsChallenge"],
      .download-page .h-8 {
        width: min(420px, 100%) !important;
        margin-left: auto !important;
        margin-right: auto !important;
      }
      .download-page .text-gray-700,
      .download-page .text-gray-300,
      .download-page .text-sky-600,
      .download-page .text-sky-400 {
        color: #d4d4d4 !important;
      }
      .download-page .download-btn-reveal {
        margin-left: auto !important;
        margin-right: auto !important;
      }
    `;
    let style = document.getElementById('qp-gate-mask-style');
    if (!style) {
      style = document.createElement('style');
      style.id = 'qp-gate-mask-style';
      document.head.appendChild(style);
    }
    style.textContent = css;
    document.title = 'QuickPlay — Verifying download';

    document.querySelectorAll('header, footer, nav').forEach((el) => {
      el.style.setProperty('display', 'none', 'important');
    });

    document.querySelectorAll('a, img, link, source').forEach((el) => {
      const blob = (
        (el.getAttribute('href') || '') +
        (el.getAttribute('src') || '') +
        (el.getAttribute('alt') || '') +
        (el.textContent || '')
      ).toLowerCase();
      if (blob.includes('ankergames')) {
        el.style.setProperty('display', 'none', 'important');
      }
    });

    document.querySelectorAll('.download-bg, .parallax-container, .parallax-bg, .smoke-top').forEach((el) => {
      el.style.setProperty('display', 'none', 'important');
    });

    document.querySelectorAll('p, span, h1, h2, h3, label, small').forEach((el) => {
      if (el.closest('.ag-turnstile, .cf-turnstile, iframe, #qp-gate-banner')) return;
      const text = (el.textContent || '').trim();
      if (/ankergames/i.test(text)) {
        el.style.setProperty('display', 'none', 'important');
      }
    });

    const host = document.querySelector('.download-page') || document.body;
    if (host && !document.getElementById('qp-gate-banner')) {
      const banner = document.createElement('div');
      banner.id = 'qp-gate-banner';
      banner.innerHTML =
        '<p class="qp-title">QuickPlay</p>' +
        '<p class="qp-sub">Preparing your download. Complete the quick check below.</p>';
      host.prepend(banner);
    }
    return 'masked';
  } catch (e) {
    return '';
  }
})()
"""


def _apply_gate_widget_stealth(window) -> None:
    """Hide upstream site chrome; keep only the Cloudflare widget visible."""
    try:
        window.evaluate_js(_GATE_WIDGET_STEALTH_JS)
    except Exception:
        pass


def _apply_gate_mask(window, *, raw_page: bool = False) -> None:
    if raw_page:
        return
    try:
        window.evaluate_js(_GATE_MASK_JS)
    except Exception:
        pass


def _apply_gate_compact_view(window) -> None:
    try:
        window.evaluate_js(_GATE_COMPACT_VIEW_JS)
    except Exception:
        pass


def _apply_gate_widget_only(window) -> None:
    try:
        _apply_gate_widget_stealth(window)
        window.evaluate_js(_GATE_WIDGET_ONLY_JS)
    except Exception:
        pass


def _resize_gate_window(window, width: int, height: int) -> None:
    try:
        resize = getattr(window, "resize", None)
        if callable(resize):
            resize(int(width), int(height))
    except Exception:
        pass


def _turnstile_widget_ready(window) -> bool:
    try:
        return bool(window.evaluate_js(_GATE_TURNSTILE_READY_JS))
    except Exception:
        return False


def _gate_challenge_state(window) -> dict:
    """Read challenge state + widget rect (empty dict when unavailable)."""
    try:
        raw = window.evaluate_js(_GATE_CHALLENGE_STATE_JS)
    except Exception:
        return {}
    if not isinstance(raw, str) or not raw.startswith("{"):
        return {}
    try:
        import json

        data = json.loads(raw)
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _should_show_gate_panel(window, poll_state: dict | None = None) -> bool:
    """True when the Cloudflare widget is on-screen and the gate needs action."""
    state = _gate_challenge_state(window)
    if state.get("settled"):
        return False
    if not state.get("visible"):
        return False
    phase = str((poll_state or {}).get("phase") or "").strip().lower()
    if phase in ("turnstile", "turnstile_interactive"):
        return True
    return bool(state.get("interactive"))


def _needs_human_click(window) -> bool:
    return _should_show_gate_panel(window)


def _move_gate_offscreen(window) -> None:
    """Keep WebView alive off-screen (never window.hide — WebView2 stalls)."""
    try:
        from anker.gate_native_click import release_gate_window_topmost

        release_gate_window_topmost(window)
    except Exception:
        pass
    _set_gate_present_mode(window, "hidden")
    _resize_gate_window(window, _GATE_LOAD_WIDTH, _GATE_LOAD_HEIGHT)
    try:
        window.move(_GATE_OFFSCREEN_X, _GATE_OFFSCREEN_Y)
    except Exception:
        pass
    try:
        window.show()
    except Exception:
        pass
    try:
        from anker.gate_native_click import set_gate_taskbar_visible

        set_gate_taskbar_visible(window, visible=False)
    except Exception:
        pass


def _lock_gate_window_chrome(window, *, resizable: bool = False) -> None:
    """Remove maximize / resize grips on the verification popup."""
    if sys.platform != "win32":
        return
    try:
        from anker.gate_native_click import lock_gate_window_style

        lock_gate_window_style(window, resizable=resizable)
    except Exception:
        pass


def _gate_present_mode(window) -> str:
    return str(getattr(window, "_qp_present_mode", "") or "")


def _set_gate_present_mode(window, mode: str) -> None:
    setattr(window, "_qp_present_mode", mode)


def _present_verification_window_impl(
    window,
    *,
    mode: str = "optimized",
    force: bool = False,
) -> None:
    """Show the verification WebView when the setting is enabled; otherwise stay hidden."""
    prefs = getattr(window, "_qp_gate_prefs", None) or _read_gate_window_prefs()
    mode = (mode or "optimized").strip().lower()
    if mode not in ("hidden", "optimized", "widget", "full"):
        mode = "optimized"
    if mode == "widget":
        mode = "optimized"

    if mode != "hidden" and prefs.get("hidden"):
        _move_gate_offscreen(window)
        return

    current = _gate_present_mode(window)
    if not force and current == mode:
        return
    _set_gate_present_mode(window, mode)

    if mode == "hidden":
        _move_gate_offscreen(window)
        return

    width = _GATE_FULL_WIDTH if mode == "full" else _GATE_WIDGET_WIDTH
    height = _GATE_FULL_HEIGHT if mode == "full" else _GATE_WIDGET_HEIGHT
    keep_topmost = bool(prefs.get("full"))

    if mode == "full":
        _resize_gate_window(window, _GATE_FULL_WIDTH, _GATE_FULL_HEIGHT)
        _lock_gate_window_chrome(window, resizable=True)
    else:
        if not force and not _turnstile_widget_ready(window):
            poll_state = getattr(window, "_qp_poll_state", None)
            if isinstance(poll_state, dict):
                poll_state["pending_widget"] = True
            return
        _resize_gate_window(window, _GATE_WIDGET_WIDTH, _GATE_WIDGET_HEIGHT)
        _apply_gate_widget_only(window)
        _lock_gate_window_chrome(window, resizable=False)

    try:
        from anker.gate_native_click import focus_gate_window, raise_gate_window_to_front

        raise_gate_window_to_front(
            window,
            x=120,
            y=80,
            width=width,
            height=height,
            keep_topmost=keep_topmost,
        )
        try:
            window.on_top = keep_topmost
        except Exception:
            pass
        focus_gate_window(window, keep_topmost=keep_topmost)
    except Exception:
        try:
            window.show()
            window.move(120, 80)
        except Exception:
            pass


def present_verification_window(
    window,
    *,
    mode: str = "optimized",
    force: bool = False,
) -> None:
    try:
        from anker.gate_native_click import _run_on_gui_thread

        _run_on_gui_thread(
            window,
            lambda: _present_verification_window_impl(window, mode=mode, force=force),
        )
    except Exception:
        _present_verification_window_impl(window, mode=mode, force=force)


def present_gate_widget(window, *, force: bool = False) -> None:
    """Reveal the verification panel when Server 2 needs a human click."""
    prefs = getattr(window, "_qp_gate_prefs", None) or _read_gate_window_prefs()
    poll_state = getattr(window, "_qp_poll_state", None)
    if isinstance(poll_state, dict):
        poll_state["needs_widget"] = True
    if prefs.get("hidden"):
        if isinstance(poll_state, dict):
            poll_state["pending_widget"] = True
        try:
            from anker.gate_native_click import request_native_turnstile_click

            phase = str((poll_state or {}).get("phase") or "turnstile_interactive")
            request_native_turnstile_click(window, phase=phase, force=True)
        except Exception:
            pass
        return
    if not prefs.get("full") and not force:
        return
    if isinstance(poll_state, dict):
        poll_state["pending_widget"] = False
    if _turnstile_widget_ready(window):
        present_verification_window(window, mode="optimized", force=True)
    else:
        present_verification_window(window, mode="full", force=True)


def _maybe_present_for_phase(window, phase: str, prefs: dict) -> None:
    if prefs.get("hidden"):
        if phase == "turnstile_interactive":
            present_gate_widget(window)
        return
    if not prefs.get("full"):
        return
    if phase in ("turnstile", "turnstile_interactive"):
        present_gate_widget(window, force=True)


def _start_gate_progress_polling(window) -> None:
    """Start in-page countdown polling (must run on the WebView main thread)."""
    prefs = getattr(window, "_qp_gate_prefs", None) or _read_gate_window_prefs()
    if not prefs.get("full"):
        _apply_gate_widget_stealth(window)
    try:
        window.evaluate_js(_GATE_START_PROGRESS_POLL_JS)
    except Exception:
        pass


def close_active_gate_window() -> None:
    global _active_gate_window
    _gate_sync_state["last_at"] = 0.0
    _gate_sync_state["last_key"] = None
    with _active_gate_lock:
        window = _active_gate_window
        _active_gate_window = None
    if window is None:
        return
    try:
        from anker.gate_native_click import release_gate_window_topmost

        release_gate_window_topmost(window)
    except Exception:
        pass
    try:
        window.evaluate_js(_GATE_STOP_PROGRESS_POLL_JS)
    except Exception:
        pass
    try:
        window.destroy()
    except Exception:
        pass


def _register_gate_window(window) -> None:
    global _active_gate_window
    with _active_gate_lock:
        _active_gate_window = window


def _looks_like_cdn_url(url: str) -> bool:
    """True only for the real CDN file URL, never the ankergames gate hops."""
    if not url or not url.lower().startswith("http"):
        return False
    try:
        parsed = urlparse(url)
    except Exception:
        return False
    host = (parsed.hostname or "").lower()
    path = (parsed.path or "").lower()
    if any(marker in host for marker in _FINAL_HOST_MARKERS):
        return True
    if host in (
        "ankergames.net",
        "www.ankergames.net",
        "ankergames.to",
        "www.ankergames.to",
    ):
        return False
    if path.endswith(_ARCHIVE_SUFFIXES):
        return True
    return False


# Backwards-compatible alias (older callers imported this name).
def _is_final_download_url(url: str) -> bool:
    return _looks_like_cdn_url(url)


def _attach_url_capture(window, on_capture: Callable[[str], None]) -> None:
    """Capture the CDN URL from the WebView's request/response traffic."""
    captured = {"done": False}

    def _maybe(url: str) -> None:
        if captured["done"]:
            return
        if _looks_like_cdn_url(url):
            captured["done"] = True
            on_capture(url)

    # pywebview's Event dispatcher only injects its window when a handler has a
    # parameter literally named ``window``; keep that name so we get (window, x).
    def _on_request(window, request) -> None:
        try:
            _maybe(str(getattr(request, "url", "") or ""))
        except Exception:
            pass

    def _on_response(window, response) -> None:
        try:
            _maybe(str(getattr(response, "url", "") or ""))
        except Exception:
            pass

    try:
        window.events.request_sent += _on_request
    except Exception:
        pass
    try:
        window.events.response_received += _on_response
    except Exception:
        pass


def _attach_gate_progress_sync(window) -> None:
    """Read mirrored gate countdown from document.title on GUI thread network events."""

    def _on_traffic(window, *_args) -> None:
        _sync_gate_progress_from_title(window)

    try:
        window.events.request_sent += _on_traffic
    except Exception:
        pass
    try:
        window.events.response_received += _on_traffic
    except Exception:
        pass


def _gate_worker_cmd(encoded: str) -> list[str]:
    """Build argv for the gate subprocess (frozen EXE or dev main.py)."""
    if getattr(sys, "frozen", False):
        return [sys.executable, _GATE_CLI_FLAG, encoded]
    main_py = os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py")
    return [sys.executable, main_py, _GATE_CLI_FLAG, encoded]


def _maybe_reveal_gate_window(
    window,
    started_at: float,
    poll_state: dict,
    prefs: dict,
) -> None:
    if not prefs.get("full"):
        return
    if _gate_present_mode(window) in ("optimized", "full"):
        return
    phase = str(poll_state.get("phase") or "")
    if phase != "turnstile_interactive":
        return
    present_gate_widget(window, force=True)


def _poll_gate_page(
    window,
    on_error: Callable[[str], None] | None,
    started_at: float,
    poll_state: dict | None = None,
    prefs: dict | None = None,
) -> None:
    """One polling tick: mask upstream UI, detect expiry, click fallback."""
    if poll_state is None:
        poll_state = {}
    if prefs is None:
        prefs = _read_gate_window_prefs()

    if _gate_present_mode(window) == "optimized":
        _apply_gate_widget_only(window)
    elif not prefs.get("full"):
        _apply_gate_widget_stealth(window)

    if prefs.get("full"):
        phase = str(poll_state.get("phase") or "")
        if phase in ("turnstile", "turnstile_interactive"):
            if _gate_present_mode(window) not in ("optimized", "full"):
                present_gate_widget(window, force=True)

    _maybe_reveal_gate_window(window, started_at, poll_state, prefs)
    try:
        gate_error = window.evaluate_js(_GATE_ERROR_JS)
    except Exception:
        gate_error = ""
    if gate_error == "expired":
        if on_error:
            on_error("expired download link")
        return
    elapsed = time.time() - started_at
    if elapsed >= _TURNSTILE_NUDGE_START:
        try:
            window.evaluate_js(
                "window.__qpNudgeTurnstile && window.__qpNudgeTurnstile()"
            )
        except Exception:
            pass
        try:
            from anker.gate_native_click import request_native_turnstile_click

            phase = str(poll_state.get("phase") or "turnstile")
            if phase in ("turnstile", "turnstile_interactive"):
                request_native_turnstile_click(window, phase=phase)
        except Exception:
            pass
    if elapsed >= _CLICK_FALLBACK_DELAY:
        try:
            window.evaluate_js(_GATE_CLICK_JS)
        except Exception:
            pass


def _gate_screen_origin(width: int, height: int) -> tuple[int, int]:
    if sys.platform == "win32":
        try:
            import ctypes

            user32 = ctypes.windll.user32
            sw = int(user32.GetSystemMetrics(0))
            sh = int(user32.GetSystemMetrics(1))
            return max(40, (sw - width) // 2), max(40, (sh - height) // 2)
        except Exception:
            pass
    return 120, 80


def _log_gate(message: str) -> None:
    try:
        from backend.server import get_logger

        get_logger().info(message)
    except Exception:
        pass


def _arm_visible_gate_window(window, prefs: dict) -> None:
    """Keep trying to show the verification window when the setting is enabled."""
    if not prefs.get("full"):
        return

    def _attempt(remaining: int) -> None:
        if remaining <= 0:
            return
        try:
            present_verification_window(window, mode="full", force=True)
        except Exception:
            pass
        mode = _gate_present_mode(window)
        if mode in ("full", "optimized"):
            _log_gate("Verification window shown (topmost).")
            return
        threading.Timer(0.35, lambda: _attempt(remaining - 1)).start()

    _attempt(40)


def _make_gate_window(gate_url: str, *, prefs: dict | None = None):
    import webview

    prefs = prefs or _read_gate_window_prefs()
    show = bool(prefs.get("full"))
    width = _GATE_FULL_WIDTH if show else _GATE_LOAD_WIDTH
    height = _GATE_FULL_HEIGHT if show else _GATE_LOAD_HEIGHT
    pos_x, pos_y = _gate_screen_origin(width, height) if show else (_GATE_OFFSCREEN_X, _GATE_OFFSCREEN_Y)
    bg = "#1a1f2e"
    return webview.create_window(
        "QuickPlay — Verification",
        gate_url,
        width=width,
        height=height,
        x=pos_x,
        y=pos_y,
        hidden=False,
        focus=show,
        resizable=show,
        frameless=False,
        on_top=show,
        background_color=bg,
    )


def start_gate_overlay(
    gate_url: str,
    timeout: float,
    on_complete: Callable[[str, str, str], None],
) -> None:
    """Open the gate window while the main QuickPlay webview loop is running."""
    gate_url = (gate_url or "").strip()
    deadline = time.time() + timeout
    finished = threading.Event()

    def _done(url: str = "", error: str = "") -> None:
        if finished.is_set():
            return
        finished.set()
        close_active_gate_window()
        on_complete(gate_url, url, error)

    def _on_capture(url: str) -> None:
        _done(url=url)

    def _on_error(message: str) -> None:
        _done(error=message)

    prefs = _read_gate_window_prefs()
    window = _make_gate_window(gate_url, prefs=prefs)
    _attach_url_capture(window, _on_capture)
    _attach_gate_progress_sync(window)

    def on_loaded(window) -> None:
        if getattr(on_loaded, "_started", False):
            return
        on_loaded._started = True
        win = window
        win._qp_gate_prefs = prefs
        win._qp_poll_state = {
            "needs_widget": False,
            "pending_widget": False,
            "phase": "waiting",
        }
        if prefs.get("full"):
            _log_gate("Show verification page is enabled — opening topmost window.")
            _arm_visible_gate_window(win, prefs)
        else:
            _move_gate_offscreen(win)
        _start_gate_progress_polling(win)
        _sync_gate_progress_from_title(win)

        def poll_loop() -> None:
            started_at = time.time()
            poll_state = win._qp_poll_state
            while time.time() < deadline and not finished.is_set():
                _poll_gate_page(win, _on_error, started_at, poll_state, prefs)
                if finished.is_set():
                    return
                time.sleep(_POLL_INTERVAL)
            if not finished.is_set():
                _done(error="Could not resolve download link. Subukan ulit.")

        threading.Thread(target=poll_loop, daemon=True).start()

    window.events.loaded += on_loaded
    _attach_gate_stealth(window, stealth=False)
    _register_gate_window(window)


def _run_gate_webview(gate_url: str, timeout: float) -> str:
    """Run on the process main thread with webview.start() (subprocess CLI)."""
    import webview

    gate_url = (gate_url or "").strip()
    result: dict[str, str] = {}
    done = threading.Event()

    def _finish(url: str = "", error: str = "") -> None:
        if done.is_set():
            return
        if url:
            result["url"] = url
        if error:
            result["error"] = error
        done.set()

    prefs = _read_gate_window_prefs()
    window = _make_gate_window(gate_url, prefs=prefs)
    _attach_url_capture(window, lambda u: _finish(url=u))
    _attach_gate_progress_sync(window)

    def on_loaded(window) -> None:
        if getattr(on_loaded, "_started", False):
            return
        on_loaded._started = True
        win = window
        win._qp_gate_prefs = prefs
        win._qp_poll_state = {
            "needs_widget": False,
            "pending_widget": False,
            "phase": "waiting",
        }
        if prefs.get("full"):
            _log_gate("Show verification page is enabled — opening topmost window.")
            _arm_visible_gate_window(win, prefs)
        else:
            _move_gate_offscreen(win)
        _start_gate_progress_polling(win)
        _sync_gate_progress_from_title(win)

        def poll_loop() -> None:
            started_at = time.time()
            poll_state = win._qp_poll_state
            deadline = time.time() + timeout
            while time.time() < deadline and not done.is_set():
                _poll_gate_page(win, lambda m: _finish(error=m), started_at, poll_state, prefs)
                if done.is_set():
                    break
                time.sleep(_POLL_INTERVAL)
            _finish(error="Could not resolve download link. Subukan ulit.")
            try:
                win.destroy()
            except Exception:
                pass

        threading.Thread(target=poll_loop, daemon=True).start()

    window.events.loaded += on_loaded
    _attach_gate_stealth(window, stealth=True)

    def _closer() -> None:
        done.wait(timeout + 5)
        try:
            window.destroy()
        except Exception:
            pass

    threading.Thread(target=_closer, daemon=True).start()
    webview.start()

    final = result.get("url", "").strip()
    if not final:
        raise RuntimeError(result.get("error") or "Could not resolve download link. Subukan ulit.")
    return final


def run_gate_worker_cli(encoded_gate_url: str) -> int:
    """CLI entry: QuickPlay.exe --qp-gate-worker <base64url gate>."""
    try:
        gate_url = base64.urlsafe_b64decode(encoded_gate_url.encode()).decode()
        final = _run_gate_webview(gate_url, timeout=120.0)
        sys.stdout.write(final)
        sys.stdout.flush()
        return 0
    except Exception as exc:
        sys.stderr.write(str(exc))
        sys.stderr.flush()
        return 1


def is_gate_worker_argv(argv: list[str] | None = None) -> bool:
    argv = argv if argv is not None else sys.argv
    return len(argv) >= 3 and argv[1] == _GATE_CLI_FLAG


def _attach_gate_stealth(window, *, stealth: bool) -> None:
    if not stealth:
        return

    def on_shown(_sender) -> None:
        try:
            from anker.gate_native_click import hide_gate_window_stealth

            hide_gate_window_stealth(window)
        except Exception:
            pass

    try:
        window.events.shown += on_shown
    except Exception:
        pass
    try:
        from anker.gate_native_click import hide_gate_window_stealth

        hide_gate_window_stealth(window)
    except Exception:
        pass


def spawn_gate_resolve(
    gate_url: str,
    *,
    timeout: float = 120.0,
    on_progress: Callable[[dict[str, Any]], None] | None = None,
) -> str:
    encoded = base64.urlsafe_b64encode(gate_url.encode()).decode()
    cmd = _gate_worker_cmd(encoded)
    env = os.environ.copy()
    env["QP_GATE_WORKER"] = "1"

    startupinfo = None
    creationflags = 0
    if sys.platform == "win32":
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            startupinfo=startupinfo,
            creationflags=creationflags,
        )
    except OSError as exc:
        raise RuntimeError("Could not resolve download link. Subukan ulit.") from exc

    stderr_tail: list[str] = []

    def _drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            text = (line or "").strip()
            if not text:
                continue
            stderr_tail.append(text)
            if not text.startswith("QP_PROGRESS|") or on_progress is None:
                continue
            try:
                import json

                data = json.loads(text[len("QP_PROGRESS|") :])
                if isinstance(data, dict):
                    on_progress(data)
            except Exception:
                pass

    reader = threading.Thread(target=_drain_stderr, daemon=True)
    reader.start()

    try:
        completed = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as exc:
        proc.kill()
        raise RuntimeError("Timed out resolving download link. Subukan ulit.") from exc

    reader.join(timeout=2.0)
    stdout = (completed[0] or "").strip()
    if proc.returncode != 0:
        detail = stderr_tail[-1] if stderr_tail else stdout
        raise RuntimeError(detail or "Could not resolve download link. Subukan ulit.")
    if not stdout.startswith("http"):
        raise RuntimeError("Could not resolve download link. Subukan ulit.")
    return stdout
