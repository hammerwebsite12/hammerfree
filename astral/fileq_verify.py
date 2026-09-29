"""fileq.net gate — countdown + download button (Server 3 fileq mirrors)."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import threading
import time
from typing import Callable
from urllib.parse import urlparse

_CLI_FLAG = "--qp-fileq-worker"
_TIMEOUT = 180.0
_POLL_INTERVAL = 0.45

_ARCHIVE_RE = re.compile(
    r"https?://(?!fileq\.net)[^\s\"'<>]+\.(?:7z|zip|rar|tar|gz|001)(?:\?[^\s\"'<>]*)?",
    re.I,
)


def is_fileq_worker_argv(argv: list[str] | None = None) -> bool:
    argv = argv if argv is not None else sys.argv
    return len(argv) >= 3 and argv[1] == _CLI_FLAG


def _worker_cmd(gate_url: str) -> list[str]:
    if getattr(sys, "frozen", False):
        return [sys.executable, _CLI_FLAG, gate_url]
    main_py = os.path.join(os.path.dirname(os.path.dirname(__file__)), "main.py")
    return [sys.executable, main_py, _CLI_FLAG, gate_url]


def _looks_like_file_url(url: str) -> bool:
    raw = (url or "").strip()
    if not raw.startswith("http"):
        return False
    host = (urlparse(raw).hostname or "").lower()
    if host in ("fileq.net", "www.fileq.net"):
        return False
    if _ARCHIVE_RE.search(raw):
        return True
    path = (urlparse(raw).path or "").lower()
    return path.endswith((".7z", ".zip", ".rar", ".tar", ".gz", ".001"))


_INIT_JS = r"""
(() => {
  const VER = 1;
  if (window.__qpFqVer === VER) {
    try { window.__qpFileQTick && window.__qpFileQTick(); } catch (e) {}
    return 'tick';
  }
  window.__qpFqVer = VER;
  window.__qpFileUrl = window.__qpFileUrl || '';
  window.__qpFileQClicked = false;

  function capture(raw) {
    try {
      const u = String(raw || '').trim();
      if (!u.startsWith('http')) return;
      if (u.indexOf('fileq.net') >= 0 && u.indexOf('.html') >= 0) return;
      if (/\.(7z|zip|rar|tar|gz|001)(\?|#|$)/i.test(u)) {
        window.__qpFileUrl = u;
        try { document.title = 'QP_FILE:' + u; } catch (e) {}
      }
    } catch (e) {}
  }

  if (!window.__qpFqFetch) {
    window.__qpFqFetch = true;
    const orig = window.fetch.bind(window);
    window.fetch = function(input, init) {
      try {
        const url = typeof input === 'string' ? input : ((input && input.url) || '');
        capture(url);
      } catch (e) {}
      return orig(input, init).then((res) => {
        try { capture(res.url || ''); } catch (e) {}
        return res;
      });
    };
  }

  window.__qpFileQTick = function() {
    if (window.__qpFileUrl) return 'done';
    const btn = document.getElementById('downloadbtn');
    if (!btn) return 'no-btn';
    const sec = document.getElementById('seconds');
    const remaining = sec ? parseInt((sec.textContent || '0').trim(), 10) : 0;
    const disabled = btn.disabled || btn.hasAttribute('disabled');
    if (disabled && remaining > 0) return 'countdown:' + remaining;
    if (!window.__qpFileQClicked) {
      window.__qpFileQClicked = true;
      try { btn.removeAttribute('disabled'); btn.disabled = false; } catch (e) {}
      try { btn.click(); } catch (e) {}
      try {
        const form = btn.closest('form') || document.forms.F1 || document.forms[0];
        if (form && typeof form.submit === 'function') form.submit();
      } catch (e) {}
      return 'clicked';
    }
    return 'wait';
  };

  window.__qpFileQTick();
  if (!window.__qpFqInterval) {
    window.__qpFqInterval = setInterval(() => window.__qpFileQTick(), 800);
  }
  return 'booted';
})()
"""


def _attach_fileq_capture(window, on_url: Callable[[str], None]) -> None:
    captured = {"done": False}

    def _emit(url: str) -> None:
        if captured["done"]:
            return
        if _looks_like_file_url(url):
            captured["done"] = True
            on_url(url.strip())

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
        if isinstance(title, str) and title.startswith("QP_FILE:"):
            _emit(title[8:].strip())
        try:
            js_url = window.evaluate_js("window.__qpFileUrl || ''") or ""
        except Exception:
            js_url = ""
        if isinstance(js_url, str):
            _emit(js_url)

    try:
        window.events.request_sent += _on_request
        window.events.response_received += _on_response
        window.events.request_sent += _on_traffic
        window.events.response_received += _on_traffic
    except Exception:
        pass


def resolve_fileq_file_url(gate_url: str, *, timeout: float = _TIMEOUT) -> str:
    gate_url = (gate_url or "").strip()
    if not gate_url.startswith("https://fileq.net/"):
        raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

    cmd = _worker_cmd(gate_url)
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
        out, _err = proc.communicate(timeout=timeout + 20)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise RuntimeError("Timed out waiting for file host. Subukan ulit.")

    for line in (out or "").splitlines():
        line = line.strip()
        if line.startswith("QP_OK "):
            url = line[6:].strip()
            if _looks_like_file_url(url):
                return url
    raise RuntimeError("Download confirmation was cancelled. Subukan ulit.")


def run_fileq_worker_cli(gate_url: str) -> int:
    import webview

    from astral.mocha_verify import (
        _apply_mocha_window_present,
        _attach_mocha_gui_turnstile,
        _read_mocha_window_prefs,
    )

    gate_url = (gate_url or "").strip()
    prefs = _read_mocha_window_prefs()
    found: dict[str, str] = {}
    stop = threading.Event()

    def _on_url(url: str) -> None:
        if url and not found.get("url"):
            found["url"] = url
            try:
                window.destroy()
            except Exception:
                pass

    window = webview.create_window(
        "QuickPlay — preparing download",
        gate_url,
        width=520,
        height=640,
        resizable=True,
        background_color="#0b0a0a",
    )
    window._qp_mocha_prefs = prefs

    _attach_fileq_capture(window, _on_url)
    _attach_mocha_gui_turnstile(window)

    def _on_loaded() -> None:
        try:
            window.evaluate_js(_INIT_JS)
        except Exception:
            pass
        _apply_mocha_window_present(window, visible=bool(prefs.get("full")))

    try:
        window.events.loaded += _on_loaded
    except Exception:
        pass

    def _poll() -> None:
        deadline = time.time() + _TIMEOUT
        while time.time() < deadline and not stop.is_set():
            time.sleep(_POLL_INTERVAL)
            if found.get("url"):
                break
            try:
                window.evaluate_js(_INIT_JS)
                url = window.evaluate_js("window.__qpFileUrl || ''") or ""
            except Exception:
                continue
            if isinstance(url, str) and _looks_like_file_url(url):
                found["url"] = url
                break
        stop.set()
        try:
            window.destroy()
        except Exception:
            pass

    def _start() -> None:
        threading.Thread(target=_poll, daemon=True).start()

    try:
        webview.start(_start, window)
    except Exception:
        return 2

    final = (found.get("url") or "").strip()
    if _looks_like_file_url(final):
        print(f"QP_OK {final}", flush=True)
        return 0
    return 2
