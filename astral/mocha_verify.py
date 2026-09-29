"""Visible mocha.my confirmation window before Server 3 file download."""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import parse_qs, quote, urlparse

_CLI_FLAG = "--qp-mocha-worker"
_TIMEOUT = 180.0
_POLL_INTERVAL = 0.4
_MOCHA_VISIBLE_W = 480
_MOCHA_VISIBLE_H = 700

_TICKET_RE = re.compile(
    r"https?://(?:www\.)?mocha\.my/api/shares/([^/?#]+)/download\?([^#]*\bticket=)([^&\s#]+)",
    re.I,
)

# Mocha injects //d318g7p3azvr44.cloudfront.net/?zapgd=… (share-ad) unless suppressAds.
_INIT_JS = r"""
(() => {
  const VER = 4;
  if (window.__qpMochaVer === VER) {
    try { window.__qpMochaTick && window.__qpMochaTick(); } catch (e) {}
    return 'tick';
  }
  window.__qpMochaVer = VER;
  window.__qpFileUrl = window.__qpFileUrl || '';
  window.__qpLastClick = 0;

  const AD_RE = /(googlesyndication|doubleclick|googletagmanager|google-analytics|plausible|outbrain|taboola|popads|adnxs|adservice|bonker\.dev|hotjar|facebook\.net\/tr|cloudfront\.net\/\?zapgd|d318g7p3azvr44|zapgd=|\/api\/cats\/)/i;
  const ALLOW_IFRAME_RE = /(mocha\.my|challenges\.cloudflare\.com|turnstile)/i;

  function captureDownloadUrl(raw) {
    try {
      if (!raw || String(raw).indexOf('/api/shares/') < 0) return;
      if (String(raw).indexOf('ticket=') < 0) return;
      if (String(raw).indexOf('probe=1') >= 0) return;
      const abs = new URL(String(raw), location.origin);
      const ticket = abs.searchParams.get('ticket') || '';
      if (!ticket) return;
      const access = abs.searchParams.get('accessToken') || '';
      let out = abs.origin + abs.pathname + '?ticket=' + encodeURIComponent(ticket);
      if (access) out += '&accessToken=' + encodeURIComponent(access);
      window.__qpFileUrl = out;
      try { document.title = 'QP_TICKET:' + out; } catch (e) {}
    } catch (e) {}
  }

  function stripAds() {
    try {
      const ad = document.getElementById('share-ad');
      if (ad) ad.remove();
      document.querySelectorAll('script[src]').forEach((s) => {
        const src = s.src || '';
        if (AD_RE.test(src) || src.indexOf('cloudfront') >= 0) s.remove();
      });
      document.querySelectorAll('iframe').forEach((f) => {
        const src = f.src || f.getAttribute('src') || '';
        if (src && !ALLOW_IFRAME_RE.test(src)) {
          f.remove();
        }
      });
      document.querySelectorAll(
        '[id*="share-ad"],[id*="ad-"],[class*="adsby"],[data-ad],a[href*="zapgd"]'
      ).forEach((el) => el.remove());
    } catch (e) {}
  }

  if (!document.getElementById('__qp_mocha_style')) {
    const style = document.createElement('style');
    style.id = '__qp_mocha_style';
    style.textContent = `
      #share-ad, script[data-cfasync], footer, header,
      a[href*="cloudfront.net/?zapgd"] { display: none !important; }
      iframe:not([src*="mocha.my"]):not([src*="challenges.cloudflare"]):not([src*="turnstile"]) {
        display: none !important; pointer-events: none !important; height: 0 !important;
      }
      body { overflow-x: hidden !important; background: #0b0a0a !important; }
      #qp-mocha-banner {
        width: min(420px, 92vw); margin: 12px auto 8px; text-align: center;
        font-family: Segoe UI, system-ui, sans-serif;
      }
      #qp-mocha-banner .t { color: #ff8c1a; font-weight: 600; font-size: 18px; }
      #qp-mocha-banner .s { color: #a3a3a3; font-size: 12px; margin-top: 4px; }
    `;
    (document.head || document.documentElement).appendChild(style);
  }

  try {
    const host = document.body || document.documentElement;
    if (host && !document.getElementById('qp-mocha-banner')) {
      const b = document.createElement('div');
      b.id = 'qp-mocha-banner';
      b.innerHTML = '<div class="t">QuickPlay</div><div class="s">Tick the Cloudflare box once. Wait until it finishes — do not click again while it says Verifying.</div>';
      host.prepend(b);
    }
  } catch (e) {}

  if (!window.__qpFetchHooked) {
    window.__qpFetchHooked = true;
    const origFetch = window.fetch.bind(window);
    window.fetch = function(input, init) {
      try {
        const url = typeof input === 'string' ? input : ((input && input.url) || '');
        if (AD_RE.test(url)) {
          return Promise.resolve(new Response('{}', { status: 204 }));
        }
        captureDownloadUrl(url);
      } catch (e) {}
      return origFetch(input, init).then((res) => {
        try {
          captureDownloadUrl(res.url || '');
        } catch (e) {}
        return res;
      });
    };
  }

  if (!window.__qpCreateHooked) {
    window.__qpCreateHooked = true;
    const origCreate = document.createElement.bind(document);
    document.createElement = function(tag) {
      const el = origCreate(tag);
      if (String(tag).toLowerCase() === 'a') {
        const origClick = el.click.bind(el);
        el.click = function() {
          captureDownloadUrl(el.href || el.getAttribute('href') || '');
          return origClick();
        };
      }
      return el;
    };
  }

  if (!window.__qpClickCap) {
    window.__qpClickCap = true;
    document.addEventListener('click', (ev) => {
      try {
        const t = ev.target && ev.target.closest ? ev.target.closest('a') : null;
        if (t) captureDownloadUrl(t.href || '');
      } catch (e) {}
    }, true);
  }

  window.__qpMochaPhase = function() {
    const t = (document.body && document.body.innerText) || '';
    return {
      verifying: t.indexOf('Verifying...') >= 0,
      human: t.indexOf('Verify you are human') >= 0,
      patrol: t.indexOf('Bandwidth Patrol') >= 0,
      adHint: t.indexOf('first click opens an ad') >= 0,
    };
  };

  window.__qpClickDownload = function() {
    if (window.__qpFileUrl) return 'done';
    const phase = window.__qpMochaPhase ? window.__qpMochaPhase() : {};
    if (phase.verifying || phase.human) return 'turnstile';
    if (phase.patrol) return 'patrol';
    const now = Date.now();
    if (now - (window.__qpLastClick || 0) < 450) return 'wait';
    if (phase.adHint) {
      window.__qpAdDlStep = window.__qpAdDlStep || 0;
      if (window.__qpAdDlStep >= 2) return 'ad-done';
      if (now - (window.__qpAdDlAt || 0) < 1800) return 'ad-wait';
    }
    const scopes = [];
    document.querySelectorAll('[class*="mocha-panel"], [role="dialog"], .mocha-overlay').forEach((n) => scopes.push(n));
    scopes.push(document);

    const want = (text, aria) => {
      const hay = ((text || '') + ' ' + (aria || '')).replace(/\s+/g, ' ').trim().toLowerCase();
      if (!hay) return false;
      if (/^download(\s+file|\s+now)?$/i.test(hay)) return true;
      if (hay.indexOf('download') >= 0 && hay.indexOf('folder') < 0) return true;
      return false;
    };

    for (const root of scopes) {
      const nodes = root.querySelectorAll('button, a, [role="button"]');
      for (const el of nodes) {
        const text = (el.innerText || el.textContent || '').trim();
        const aria = (el.getAttribute('aria-label') || '').trim();
        if (!want(text, aria)) continue;
        if (el.disabled) continue;
        window.__qpLastClick = now;
        try { el.click(); } catch (e) {}
        if (phase.adHint) {
          window.__qpAdDlStep = (window.__qpAdDlStep || 0) + 1;
          window.__qpAdDlAt = now;
        }
        return 'clicked';
      }
    }
    return '';
  };

  window.__qpMochaTick = function() {
    stripAds();
    const phase = window.__qpMochaPhase ? window.__qpMochaPhase() : {};
    if (phase.verifying || phase.human || phase.patrol) return;
    try { window.__qpClickDownload(); } catch (e) {}
  };

  if (!window.__qpMochaInterval) {
    window.__qpMochaInterval = setInterval(() => window.__qpMochaTick(), 700);
  }
  window.__qpMochaTick();
  return 'booted';
})()
"""

_CLICK_JS = "window.__qpClickDownload ? window.__qpClickDownload() : ''"

_STATE_JS = """
(() => {
  try {
    if (window.__qpMochaPhase) return JSON.stringify(window.__qpMochaPhase());
  } catch (e) {}
  return '{}';
})()
"""


def is_mocha_worker_argv(argv: list[str] | None = None) -> bool:
    argv = argv if argv is not None else sys.argv
    return len(argv) >= 3 and argv[1] == _CLI_FLAG


def _worker_cmd(share_token: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, _CLI_FLAG, share_token]
    main_py = os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py")
    return [sys.executable, main_py, _CLI_FLAG, share_token]


def _normalize_ticket_url(url: str, share_token: str) -> str:
    raw = (url or "").strip()
    if not raw:
        return ""
    if raw.startswith("/"):
        raw = "https://mocha.my" + raw
    m = _TICKET_RE.search(raw)
    if not m:
        try:
            p = urlparse(raw)
            if "mocha.my" not in (p.hostname or "").lower():
                return ""
            if f"/api/shares/{share_token}/download" not in p.path:
                return ""
            ticket = (parse_qs(p.query).get("ticket") or [""])[0]
            if not ticket:
                return ""
            access = (parse_qs(p.query).get("accessToken") or [""])[0]
            out = f"https://mocha.my/api/shares/{share_token}/download?ticket={quote(ticket, safe='')}"
            if access:
                out += f"&accessToken={quote(access, safe='')}"
            return out
        except Exception:
            return ""
    token, _, ticket = m.group(1), m.group(2), m.group(3)
    if token != share_token:
        return ""
    try:
        p = urlparse(raw)
        access = (parse_qs(p.query).get("accessToken") or [""])[0]
    except Exception:
        access = ""
    out = f"https://mocha.my/api/shares/{share_token}/download?ticket={quote(ticket, safe='')}"
    if access:
        out += f"&accessToken={quote(access, safe='')}"
    return out


def _attach_mocha_url_capture(
    window,
    share_token: str,
    on_url: Callable[[str], None],
) -> None:
    captured = {"done": False}

    def _emit(url: str) -> None:
        if captured["done"]:
            return
        final = _normalize_ticket_url(url, share_token)
        if final.startswith("http"):
            captured["done"] = True
            on_url(final)

    def _on_request(window, request) -> None:
        try:
            _emit(str(getattr(request, "url", "") or ""))
        except Exception:
            pass

    def _on_response(window, response) -> None:
        try:
            _emit(str(getattr(response, "url", "") or ""))
        except Exception:
            pass

    def _on_traffic(window, *_args) -> None:
        try:
            title = window.evaluate_js("document.title || ''") or ""
        except Exception:
            title = ""
        if isinstance(title, str) and title.startswith("QP_TICKET:"):
            _emit(title[10:].strip())
        try:
            cur = window.get_current_url() or ""
        except Exception:
            cur = ""
        if cur:
            _emit(cur)

    try:
        window.events.request_sent += _on_request
    except Exception:
        pass
    try:
        window.events.response_received += _on_response
    except Exception:
        pass
    try:
        window.events.request_sent += _on_traffic
        window.events.response_received += _on_traffic
    except Exception:
        pass


def _read_mocha_window_prefs() -> dict[str, bool]:
    try:
        from settings_manager import SettingsManager

        sm = SettingsManager()
        full = bool(sm.server3_verification_window_full)
    except Exception:
        full = False
    return {"full": full, "hidden": not full}


def _apply_mocha_window_present(window, *, visible: bool) -> None:
    """Off-screen + no taskbar when hidden (same approach as Server 2 gate)."""
    try:
        if visible:
            from anker.gate_native_click import focus_gate_window, raise_gate_window_to_front

            raise_gate_window_to_front(
                window,
                x=120,
                y=80,
                width=_MOCHA_VISIBLE_W,
                height=_MOCHA_VISIBLE_H,
                keep_topmost=False,
            )
            focus_gate_window(window, keep_topmost=False)
        else:
            from anker.gate_native_click import hide_gate_window_stealth

            hide_gate_window_stealth(window)
    except Exception:
        try:
            if visible:
                window.move(120, 80)
                window.show()
            else:
                window.move(-2400, -2400)
        except Exception:
            pass


def _attach_mocha_gui_turnstile(window) -> None:
    """Run pending OS Turnstile clicks on the WebView GUI thread (required on WinForms)."""

    def _on_traffic(window, *_args) -> None:
        try:
            from anker.gate_native_click import process_pending_turnstile_click

            process_pending_turnstile_click(window)
        except Exception:
            pass

    try:
        window.events.request_sent += _on_traffic
    except Exception:
        pass
    try:
        window.events.response_received += _on_traffic
    except Exception:
        pass


def _read_mocha_phase(window) -> dict:
    try:
        raw = window.evaluate_js(_STATE_JS)
    except Exception:
        return {}
    if not isinstance(raw, str) or not raw.startswith("{"):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def resolve_mocha_file_url(share_token: str, *, timeout: float = _TIMEOUT) -> str:
    """Block until the user confirms the mocha share and a ticketed URL is ready."""
    token = (share_token or "").strip()
    if not token or "/" in token or ".." in token:
        raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

    cmd = _worker_cmd(token)
    try:
        proc = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
    except OSError as exc:
        raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.") from exc

    try:
        out, _err = proc.communicate(timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("Timed out waiting for download confirmation. Subukan ulit.")

    url = ""
    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("QP_OK "):
            url = line[6:].strip()
    if url.startswith("http"):
        return url
    raise RuntimeError("Download confirmation was cancelled. Subukan ulit.")


def _poll_mocha_window(window, share_token: str, found: dict, stop: threading.Event) -> None:
    try:
        from anker.gate_native_click import native_nudge_turnstile
    except Exception:
        native_nudge_turnstile = None  # type: ignore[assignment,misc]

    deadline = time.time() + _TIMEOUT
    turnstile_nudged = False
    initial_download_nudged = False
    last_verifying_at = 0.0
    verifying_since: float | None = None
    reloaded_stuck = False
    share_url = f"https://mocha.my/share/{share_token}"

    while time.time() < deadline and not stop.is_set():
        time.sleep(_POLL_INTERVAL)
        if found.get("url"):
            break
        try:
            window.evaluate_js(_INIT_JS)
            phase = _read_mocha_phase(window)
            if not phase.get("verifying") and not phase.get("human") and not phase.get("patrol"):
                window.evaluate_js(_CLICK_JS)
            url = window.evaluate_js("window.__qpFileUrl || ''") or ""
        except Exception:
            continue
        if isinstance(url, str) and url.startswith("http") and "ticket=" in url:
            final = _normalize_ticket_url(url, share_token)
            if final:
                found["url"] = final
                break

        if phase.get("verifying"):
            last_verifying_at = time.time()
            if verifying_since is None:
                verifying_since = time.time()
            elif (
                not reloaded_stuck
                and time.time() - verifying_since > 28.0
            ):
                reloaded_stuck = True
                verifying_since = None
                turnstile_nudged = False
                try:
                    window.load_url(share_url)
                except Exception:
                    pass
            continue
        verifying_since = None

        if phase.get("human") and not turnstile_nudged and native_nudge_turnstile is not None:
            try:
                native_nudge_turnstile(window, phase="turnstile_interactive")
            except Exception:
                pass
            turnstile_nudged = True
            continue

        prefs = getattr(window, "_qp_mocha_prefs", None) or _read_mocha_window_prefs()
        if prefs.get("full") and (
            phase.get("patrol") or phase.get("human") or phase.get("verifying")
        ):
            _apply_mocha_window_present(window, visible=True)

        if (
            not initial_download_nudged
            and not phase.get("patrol")
            and not phase.get("human")
            and time.time() - last_verifying_at > 2.0
        ):
            try:
                window.evaluate_js(_CLICK_JS)
            except Exception:
                pass
            initial_download_nudged = True

    stop.set()
    try:
        window.destroy()
    except Exception:
        pass


def run_mocha_worker_cli(share_token: str) -> int:
    import webview

    token = (share_token or "").strip()
    share_url = f"https://mocha.my/share/{token}"
    prefs = _read_mocha_window_prefs()
    found: dict[str, str] = {}
    stop = threading.Event()

    def _on_ticket(url: str) -> None:
        if url and not found.get("url"):
            found["url"] = url
            try:
                window.destroy()
            except Exception:
                pass

    window = webview.create_window(
        "QuickPlay — confirm download",
        share_url,
        width=_MOCHA_VISIBLE_W,
        height=_MOCHA_VISIBLE_H,
        resizable=True,
        background_color="#0b0a0a",
    )
    window._qp_mocha_prefs = prefs

    _attach_mocha_url_capture(window, token, _on_ticket)
    _attach_mocha_gui_turnstile(window)

    def _inject() -> None:
        try:
            window.evaluate_js(_INIT_JS)
            window.evaluate_js(_CLICK_JS)
        except Exception:
            pass

    def _on_loaded() -> None:
        _inject()
        _apply_mocha_window_present(window, visible=bool(prefs.get("full")))

    try:
        window.events.loaded += _on_loaded
    except Exception:
        pass

    def _start() -> None:
        threading.Thread(
            target=_poll_mocha_window,
            args=(window, token, found, stop),
            daemon=True,
        ).start()

    try:
        webview.start(_start, window)
    except Exception:
        return 2

    final = (found.get("url") or "").strip()
    if final.startswith("http"):
        print(f"QP_OK {final}", flush=True)
        return 0
    return 2
