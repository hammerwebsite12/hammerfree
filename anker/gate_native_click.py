"""Native OS mouse click for Cloudflare Turnstile (iframe cannot be clicked via JS)."""

from __future__ import annotations

import json
import sys
import threading
import time
from typing import Any, Callable

_last_click_at = 0.0
_click_lock = threading.Lock()

_GATE_TURNSTILE_SCAN_JS = """
(() => {
  try {
    const pageText = (document.body && document.body.innerText) || '';
    const interactive = pageText.indexOf('Verify you are human') >= 0;
    const verifying = pageText.indexOf('Verifying...') >= 0;
    const points = [];
    const seen = new Set();
    const addPoint = (cx, cy) => {
      const key = cx + ',' + cy;
      if (seen.has(key)) return;
      seen.add(key);
      points.push({ cx: Math.round(cx), cy: Math.round(cy) });
    };
    const scan = (el) => {
      if (!el) return;
      const r = el.getBoundingClientRect();
      if (!r || r.width < 8 || r.height < 8) return;
      const xs = interactive
        ? [0.08, 0.10, 0.12, 0.14, 0.16, 0.18, 0.22, 0.28]
        : [0.12, 0.18, 0.24];
      const ys = [0.35, 0.50, 0.65];
      for (let i = 0; i < xs.length; i++) {
        for (let j = 0; j < ys.length; j++) {
          addPoint(r.left + r.width * xs[i], r.top + r.height * ys[j]);
        }
      }
    };
    const selectors = [
      'iframe[src*="challenges.cloudflare.com"]',
      'iframe[src*="turnstile"]',
      '.cf-turnstile iframe',
      '.ag-turnstile iframe',
      '.cf-turnstile',
      '.ag-turnstile',
      '[data-sitekey]',
    ];
    for (let i = 0; i < selectors.length; i++) {
      scan(document.querySelector(selectors[i]));
    }
    if (!points.length) {
      const frames = document.querySelectorAll('iframe');
      for (let k = 0; k < frames.length; k++) scan(frames[k]);
    }
    if (!points.length) return '';
    return JSON.stringify({
      points: points.slice(0, 24),
      interactive: interactive,
      verifying: verifying,
    });
  } catch (e) {
    return '';
  }
})()
"""

_GATE_TURNSTILE_DONE_JS = """
(() => {
  try {
    const root = document.querySelector('.download-page');
    const d = root && root._x_dataStack && root._x_dataStack.length ? root._x_dataStack[0] : null;
    if (!d) return false;
    return !!(d.hasToken || d.showButton || d.downloadClicked || d.state === 'ready');
  } catch (e) {
    return false;
  }
})()
"""

_GATE_TURNSTILE_EXECUTE_JS = """
(() => {
  try {
    if (window.agTurnstile && typeof window.agTurnstile.execute === 'function') {
      window.agTurnstile.execute();
      return 'execute';
    }
  } catch (e) {}
  return '';
})()
"""


def _window_gui(window: Any) -> Any | None:
    return getattr(window, "gui", None)


def _dpi_scale(window: Any) -> float:
    gui = _window_gui(window)
    if gui is None:
        return 1.0
    try:
        scale = float(getattr(gui, "_scale", 1.0) or 1.0)
        return scale if scale > 0 else 1.0
    except (TypeError, ValueError):
        return 1.0


def _form_handle(window: Any) -> int:
    gui = _window_gui(window)
    if gui is None:
        return 0
    handle = getattr(gui, "Handle", None)
    if not handle:
        return 0
    try:
        return int(handle.ToInt32())
    except Exception:
        try:
            return int(handle)
        except (TypeError, ValueError):
            return 0


def _webview_handle(window: Any) -> int:
    gui = _window_gui(window)
    if gui is None:
        return 0
    webview = getattr(gui, "webview", None)
    if webview is None:
        return _form_handle(window)
    handle = getattr(webview, "Handle", None)
    if not handle:
        return _form_handle(window)
    try:
        return int(handle.ToInt32())
    except Exception:
        try:
            return int(handle)
        except (TypeError, ValueError):
            return _form_handle(window)


def _main_window() -> Any | None:
    try:
        import webview

        if webview.windows:
            return webview.windows[0]
    except Exception:
        pass
    return None


def _main_window_gui() -> Any | None:
    main = _main_window()
    if main is None:
        return None
    return _window_gui(main)


def _resolve_window_hwnd(window: Any) -> int:
    hwnd = _form_handle(window)
    if hwnd:
        return hwnd
    if sys.platform != "win32":
        return 0
    try:
        import ctypes

        user32 = ctypes.windll.user32
        matches: list[int] = []

        @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
        def _callback(hwnd, _lparam):
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if "Verification" in buf.value:
                matches.append(int(hwnd))
            return True

        user32.EnumWindows(_callback, 0)
        return matches[-1] if matches else 0
    except Exception:
        return 0


def _run_on_gui_thread(window: Any, fn: Callable[[], Any]) -> Any:
    gui = _main_window_gui() or _window_gui(window)
    if gui is None:
        return fn()
    try:
        invoke_required = bool(getattr(gui, "InvokeRequired", False))
    except Exception:
        invoke_required = True
    if not invoke_required:
        return fn()
    result: list[Any] = [None]
    error: list[BaseException | None] = [None]

    def wrapper() -> None:
        try:
            result[0] = fn()
        except BaseException as exc:  # noqa: BLE001
            error[0] = exc

    try:
        from System import Func, Type

        gui.Invoke(Func[Type](wrapper))
    except Exception:
        return fn()
    if error[0] is not None:
        raise error[0]
    return result[0]


def hide_gate_window_stealth(window: Any) -> None:
    """Remove gate WebView from taskbar and keep it off-screen."""

    def _apply() -> None:
        release_gate_window_topmost(window)
        gui = _window_gui(window)
        if gui is not None:
            try:
                gui.ShowInTaskbar = False
            except Exception:
                pass
        set_gate_taskbar_visible(window, visible=False)
        try:
            from anker.gate_resolver_worker import _GATE_OFFSCREEN_X, _GATE_OFFSCREEN_Y

            window.move(_GATE_OFFSCREEN_X, _GATE_OFFSCREEN_Y)
        except Exception:
            try:
                window.move(-2400, -2400)
            except Exception:
                pass

    _run_on_gui_thread(window, _apply)


def release_gate_window_topmost(window: Any) -> None:
    """Drop HWND_TOPMOST before hiding or destroying the gate window."""

    def _apply() -> None:
        setattr(window, "_qp_topmost", False)
        if sys.platform != "win32":
            return
        try:
            import ctypes

            hwnd = _resolve_window_hwnd(window)
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            user32.SetWindowPos(hwnd, -2, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        except Exception:
            pass

    _run_on_gui_thread(window, _apply)


def raise_gate_window_to_front(
    window: Any,
    *,
    x: int,
    y: int,
    width: int = 0,
    height: int = 0,
    keep_topmost: bool = True,
) -> None:
    """Move the gate window on-screen; optionally keep it HWND_TOPMOST."""

    def _apply() -> None:
        try:
            window.show()
        except Exception:
            pass

        sw = sh = 0
        if sys.platform == "win32":
            import ctypes

            user32 = ctypes.windll.user32
            sw = int(user32.GetSystemMetrics(0))
            sh = int(user32.GetSystemMetrics(1))

        if width > 0 and height > 0 and sw > 0 and sh > 0:
            pos_x = max(40, (sw - int(width)) // 2)
            pos_y = max(40, (sh - int(height)) // 2)
        else:
            pos_x, pos_y = int(x), int(y)

        try:
            window.move(pos_x, pos_y)
        except Exception:
            pass
        try:
            gui = _window_gui(window)
            if gui is not None:
                gui.ShowInTaskbar = True
        except Exception:
            pass
        set_gate_taskbar_visible(window, visible=True)

        if sys.platform != "win32":
            setattr(window, "_qp_topmost", bool(keep_topmost))
            return

        try:
            import ctypes

            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            hwnd = _resolve_window_hwnd(window)
            if not hwnd:
                return

            try:
                pid = ctypes.c_ulong()
                user32.AllowSetForegroundWindow(-1)
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                if pid.value:
                    user32.AllowSetForegroundWindow(pid.value)
            except Exception:
                pass

            user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            show_flags = 0x0040  # SWP_SHOWWINDOW
            z_order = -1 if keep_topmost else 0
            if width > 0 and height > 0:
                user32.SetWindowPos(
                    hwnd, z_order, pos_x, pos_y, int(width), int(height), show_flags
                )
            else:
                user32.SetWindowPos(
                    hwnd,
                    z_order,
                    pos_x,
                    pos_y,
                    0,
                    0,
                    show_flags | 0x0001,
                )

            fg = user32.GetForegroundWindow()
            fg_pid = ctypes.c_ulong()
            fg_thread = user32.GetWindowThreadProcessId(fg, ctypes.byref(fg_pid))
            cur_thread = kernel32.GetCurrentThreadId()
            attached = False
            if fg_thread and fg_thread != cur_thread:
                attached = bool(user32.AttachThreadInput(cur_thread, fg_thread, True))
            try:
                user32.BringWindowToTop(hwnd)
                user32.SetForegroundWindow(hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(cur_thread, fg_thread, False)

            if keep_topmost:
                user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002 | 0x0040)
        except Exception:
            pass

        try:
            gui = _window_gui(window)
            if gui is not None:
                gui.BringToFront()
                gui.Activate()
        except Exception:
            pass

        setattr(window, "_qp_topmost", bool(keep_topmost))

    _run_on_gui_thread(window, _apply)


def set_gate_taskbar_visible(window: Any, *, visible: bool) -> None:
    if sys.platform != "win32":
        return

    def _apply() -> None:
        try:
            import ctypes

            hwnd = _form_handle(window)
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            gwl_exstyle = -20
            ws_ex_toolwindow = 0x00000080
            ws_ex_appwindow = 0x00040000
            ex = user32.GetWindowLongW(hwnd, gwl_exstyle)
            if visible:
                ex = (ex | ws_ex_appwindow) & ~ws_ex_toolwindow
            else:
                ex = (ex | ws_ex_toolwindow) & ~ws_ex_appwindow
            user32.SetWindowLongW(hwnd, gwl_exstyle, ex)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    _run_on_gui_thread(window, _apply)


def lock_gate_window_style(window: Any, *, resizable: bool = False) -> None:
    if sys.platform != "win32":
        return

    def _apply() -> None:
        try:
            import ctypes

            hwnd = _form_handle(window)
            if not hwnd:
                return
            user32 = ctypes.windll.user32
            gwl_style = -16
            ws_maximizebox = 0x00010000
            ws_thickframe = 0x00040000
            style = user32.GetWindowLongW(hwnd, gwl_style)
            style &= ~ws_maximizebox
            if not resizable:
                style &= ~ws_thickframe
            user32.SetWindowLongW(hwnd, gwl_style, style)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, 0x0027)
        except Exception:
            pass

    _run_on_gui_thread(window, _apply)


def focus_gate_window(window: Any, *, keep_topmost: bool = True) -> None:
    def _apply() -> None:
        try:
            from anker.gate_resolver_worker import (
                _GATE_FULL_HEIGHT,
                _GATE_FULL_WIDTH,
                _gate_present_mode,
            )

            mode = _gate_present_mode(window)
            if mode == "full":
                width, height = _GATE_FULL_WIDTH, _GATE_FULL_HEIGHT
            else:
                width, height = 360, 140
            raise_gate_window_to_front(
                window,
                x=120,
                y=80,
                width=width,
                height=height,
                keep_topmost=keep_topmost,
            )
        except Exception:
            try:
                window.show()
            except Exception:
                pass

    _run_on_gui_thread(window, _apply)


def _client_origin_screen(hwnd: int) -> tuple[int, int]:
    if sys.platform != "win32" or hwnd <= 0:
        return 0, 0
    try:
        import ctypes

        class POINT(ctypes.Structure):
            _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]

        pt = POINT(0, 0)
        ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(pt))
        return int(pt.x), int(pt.y)
    except Exception:
        return 0, 0


def _send_message_click(hwnd: int, cx: int, cy: int) -> bool:
    if sys.platform != "win32" or hwnd <= 0:
        return False
    try:
        import ctypes

        user32 = ctypes.windll.user32
        lparam = (int(cy) << 16) | (int(cx) & 0xFFFF)
        user32.SendMessageW(hwnd, 0x0200, 0, lparam)  # WM_MOUSEMOVE
        user32.SendMessageW(hwnd, 0x0201, 1, lparam)  # WM_LBUTTONDOWN
        time.sleep(0.03)
        user32.SendMessageW(hwnd, 0x0202, 0, lparam)  # WM_LBUTTONUP
        return True
    except Exception:
        return False


def _enum_child_hwnds(parent: int) -> list[int]:
    if sys.platform != "win32" or parent <= 0:
        return []
    try:
        import ctypes

        hwnds: list[int] = []

        def _callback(hwnd, _lparam):
            hwnds.append(int(hwnd))
            return True

        cb = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)(_callback)
        ctypes.windll.user32.EnumChildWindows(parent, cb, 0)
        return hwnds
    except Exception:
        return []


def _sendinput_click(screen_x: int, screen_y: int) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        width = max(1, int(user32.GetSystemMetrics(0)))
        height = max(1, int(user32.GetSystemMetrics(1)))

        class MOUSEINPUT(ctypes.Structure):
            _fields_ = [
                ("dx", wintypes.LONG),
                ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD),
                ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.c_ulonglong),
            ]

        class INPUT(ctypes.Structure):
            _fields_ = [("type", wintypes.DWORD), ("mi", MOUSEINPUT)]

        nx = int(screen_x * 65535 / max(1, width - 1))
        ny = int(screen_y * 65535 / max(1, height - 1))
        flags = 0x0001 | 0x8000

        def make_input(flag: int) -> INPUT:
            inp = INPUT()
            inp.type = 0
            inp.mi = MOUSEINPUT(nx, ny, 0, flag, 0, 0)
            return inp

        inputs = (INPUT * 3)(
            make_input(flags),
            make_input(flags | 0x0002),
            make_input(flags | 0x0004),
        )
        sent = user32.SendInput(3, ctypes.byref(inputs), ctypes.sizeof(INPUT))
        return int(sent) == 3
    except Exception:
        return False


def _turnstile_passed(window: Any) -> bool:
    try:
        return bool(window.evaluate_js(_GATE_TURNSTILE_DONE_JS))
    except Exception:
        return False


def _prepare_window_for_click(window: Any) -> None:
    try:
        from anker.gate_resolver_worker import (
            _gate_present_mode,
            _read_gate_window_prefs,
            present_gate_widget,
            present_verification_window,
        )

        revealed = _gate_present_mode(window) in ("optimized", "full")
        if revealed:
            focus_gate_window(window)
            time.sleep(0.2)
            return

        prefs = getattr(window, "_qp_gate_prefs", None) or _read_gate_window_prefs()
        if prefs.get("hidden"):
            return
        present_gate_widget(window, force=True)
        focus_gate_window(window)
        time.sleep(0.2)
    except Exception:
        focus_gate_window(window)
        time.sleep(0.12)


def _scan_click_points(window: Any) -> dict[str, Any]:
    try:
        raw = window.evaluate_js(_GATE_TURNSTILE_SCAN_JS)
    except Exception:
        return {}
    if not isinstance(raw, str) or not raw.startswith("{"):
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _click_at_client_point(
    window: Any,
    *,
    cx: int,
    cy: int,
    form_hwnd: int,
    webview_hwnd: int,
    origin_x: int,
    origin_y: int,
    scale: float,
) -> bool:
    px = int(cx * scale)
    py = int(cy * scale)
    screen_x = origin_x + px
    screen_y = origin_y + py

    targets = [webview_hwnd, form_hwnd]
    targets.extend(_enum_child_hwnds(webview_hwnd))
    for hwnd in targets:
        if hwnd > 0:
            _send_message_click(hwnd, px, py)
    return _sendinput_click(screen_x, screen_y)


def _click_turnstile_on_gui_thread(window: Any, *, phase: str) -> bool:
    phase = (phase or "").strip().lower()
    scan = _scan_click_points(window)
    if not scan:
        return False

    interactive = bool(scan.get("interactive"))
    if phase not in ("turnstile", "turnstile_interactive") and not interactive:
        return False

    points = scan.get("points") or []
    if not isinstance(points, list) or not points:
        return False

    _prepare_window_for_click(window)
    if interactive:
        try:
            window.evaluate_js(_GATE_TURNSTILE_EXECUTE_JS)
            time.sleep(0.15)
            if _turnstile_passed(window):
                return True
        except Exception:
            pass

    scale = _dpi_scale(window)
    form_hwnd = _form_handle(window)
    webview_hwnd = _webview_handle(window)
    origin_x, origin_y = _client_origin_screen(form_hwnd)

    for item in points:
        if not isinstance(item, dict):
            continue
        try:
            cx = int(item.get("cx") or 0)
            cy = int(item.get("cy") or 0)
        except (TypeError, ValueError):
            continue
        if cx <= 0 or cy <= 0:
            continue
        _click_at_client_point(
            window,
            cx=cx,
            cy=cy,
            form_hwnd=form_hwnd,
            webview_hwnd=webview_hwnd,
            origin_x=origin_x,
            origin_y=origin_y,
            scale=scale,
        )
        time.sleep(0.12)
        if _turnstile_passed(window):
            return True
    return _turnstile_passed(window)


def request_native_turnstile_click(
    window: Any,
    *,
    phase: str = "",
    force: bool = False,
) -> None:
    phase = (phase or "").strip().lower()
    if not force and phase not in ("turnstile", "turnstile_interactive"):
        return
    setattr(
        window,
        "_qp_pending_click",
        {"phase": phase, "at": time.time(), "force": bool(force)},
    )


def process_pending_turnstile_click(window: Any) -> bool:
    pending = getattr(window, "_qp_pending_click", None)
    if not isinstance(pending, dict):
        return False
    if time.time() - float(pending.get("at") or 0) > 10.0:
        try:
            delattr(window, "_qp_pending_click")
        except Exception:
            pass
        return False

    global _last_click_at
    now = time.time()
    with _click_lock:
        if now - _last_click_at < 1.5:
            return False
        _last_click_at = now

    phase = str(pending.get("phase") or "turnstile_interactive")
    ok = _click_turnstile_on_gui_thread(window, phase=phase)
    if ok or phase == "turnstile_interactive":
        try:
            delattr(window, "_qp_pending_click")
        except Exception:
            pass
    return ok


def native_nudge_turnstile(
    window: Any,
    *,
    phase: str = "",
    bring_on_screen: bool = True,
) -> bool:
    del bring_on_screen
    phase = (phase or "").strip().lower()
    if phase not in ("turnstile", "turnstile_interactive"):
        return False
    request_native_turnstile_click(window, phase=phase, force=True)

    def _run() -> bool:
        return process_pending_turnstile_click(window)

    try:
        return bool(_run_on_gui_thread(window, _run))
    except Exception:
        return False
