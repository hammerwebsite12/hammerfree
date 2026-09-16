"""Themed native dialogs matching the QuickPlay web UI (pre-webview bootstrap)."""

from __future__ import annotations

import sys

from app_paths import icon_path

_BG = "#0b0a0a"
_PANEL = "#161414"
_CARD = "#1f1d1d"
_FG = "#e0e0e0"
_MUTED = "#9a9a9a"
_ACCENT = "#ffb347"
_BORDER = "#2a2a2a"
_BTN_SECONDARY = "#414141"
_BTN_SECONDARY_ACTIVE = "#555555"
_ACCENT_ACTIVE = "#ffc966"
_ACCENT_FG = "#0b0a0a"
_FONT = ("Segoe UI", 10)
_FONT_TITLE = ("Segoe UI", 11, "bold")
_FONT_BTN = ("Segoe UI", 10, "bold")


def show_info(message: str, title: str = "QuickPlay", *, wraplength: int = 360) -> None:
    """Single OK dialog."""
    _show_dialog(message, title, confirm=True, cancel=False, wraplength=wraplength)


def show_confirm(
    message: str,
    title: str = "QuickPlay",
    *,
    ok_text: str = "OK",
    cancel_text: str = "Cancel",
    wraplength: int = 360,
) -> bool:
    """OK / Cancel dialog. Returns True when OK is chosen."""
    return bool(
        _show_dialog(
            message,
            title,
            confirm=True,
            cancel=True,
            ok_text=ok_text,
            cancel_text=cancel_text,
            wraplength=wraplength,
        )
    )


def apply_window_icon(root) -> None:
    path = icon_path()
    if not path:
        return
    try:
        root.iconbitmap(path)
    except Exception:
        pass


_MB_OK = 0x00000000
_MB_OKCANCEL = 0x00000001
_MB_ICONINFORMATION = 0x00000040
_IDOK = 1


def _win32_message_box(message: str, title: str, *, cancel: bool) -> bool:
    """Fallback when tkinter is unavailable (e.g. Windows Server without Tcl/Tk)."""
    import ctypes

    flags = _MB_ICONINFORMATION | (_MB_OKCANCEL if cancel else _MB_OK)
    result = ctypes.windll.user32.MessageBoxW(None, message, title, flags)
    if cancel:
        return result == _IDOK
    return True


def _fallback_dialog(message: str, title: str, *, cancel: bool) -> bool:
    if sys.platform == "win32":
        return _win32_message_box(message, title, cancel=cancel)

    text = f"[{title}] {message}\n"
    try:
        sys.stderr.write(text)
    except UnicodeEncodeError:
        sys.stderr.buffer.write(text.encode("utf-8", errors="replace"))
    return not cancel


def _show_dialog(
    message: str,
    title: str,
    *,
    confirm: bool,
    cancel: bool,
    ok_text: str = "OK",
    cancel_text: str = "Cancel",
    wraplength: int = 360,
) -> bool | None:
    if sys.platform != "win32":
        return _fallback_dialog(message, title, cancel=cancel)

    try:
        import tkinter as tk
    except Exception:
        return _fallback_dialog(message, title, cancel=cancel)

    try:
        return _show_tk_dialog(
            message,
            title,
            tk=tk,
            confirm=confirm,
            cancel=cancel,
            ok_text=ok_text,
            cancel_text=cancel_text,
            wraplength=wraplength,
        )
    except Exception:
        return _fallback_dialog(message, title, cancel=cancel)


def _show_tk_dialog(
    message: str,
    title: str,
    *,
    tk,
    confirm: bool,
    cancel: bool,
    ok_text: str,
    cancel_text: str,
    wraplength: int,
) -> bool:
    result = {"ok": False}

    root = tk.Tk()
    root.title(title)
    apply_window_icon(root)
    root.configure(bg=_PANEL)
    root.resizable(False, False)
    root.attributes("-topmost", True)

    outer = tk.Frame(root, bg=_PANEL, padx=16, pady=16)
    outer.pack(fill="both", expand=True)

    card = tk.Frame(
        outer,
        bg=_CARD,
        highlightbackground=_BORDER,
        highlightthickness=1,
        padx=20,
        pady=18,
    )
    card.pack(fill="both", expand=True)

    tk.Label(
        card,
        text=title,
        bg=_CARD,
        fg=_ACCENT,
        font=_FONT_TITLE,
        anchor="w",
    ).pack(fill="x")

    tk.Label(
        card,
        text=message,
        bg=_CARD,
        fg=_MUTED,
        font=_FONT,
        justify="left",
        wraplength=wraplength,
        anchor="w",
    ).pack(fill="x", pady=(10, 0))

    btn_row = tk.Frame(card, bg=_CARD)
    btn_row.pack(fill="x", pady=(18, 0))

    def on_ok() -> None:
        result["ok"] = True
        root.destroy()

    def on_cancel() -> None:
        result["ok"] = False
        root.destroy()

    cancel_btn = None
    ok_btn = None

    if cancel:
        cancel_btn = tk.Button(
            btn_row,
            text=cancel_text,
            command=on_cancel,
            bg=_BTN_SECONDARY,
            fg=_FG,
            activebackground=_BTN_SECONDARY_ACTIVE,
            activeforeground=_FG,
            relief="flat",
            font=_FONT_BTN,
            padx=16,
            pady=7,
            cursor="hand2",
            borderwidth=0,
        )
        cancel_btn.pack(side="right")

    if confirm:
        ok_btn = tk.Button(
            btn_row,
            text=ok_text,
            command=on_ok,
            bg=_ACCENT,
            fg=_ACCENT_FG,
            activebackground=_ACCENT_ACTIVE,
            activeforeground=_ACCENT_FG,
            relief="flat",
            font=_FONT_BTN,
            padx=16,
            pady=7,
            cursor="hand2",
            borderwidth=0,
        )
        ok_btn.pack(side="right", padx=(0, 8 if cancel else 0))

    root.bind("<Escape>", lambda _e: on_cancel() if cancel else on_ok())
    root.bind("<Return>", lambda _e: on_ok())

    root.update_idletasks()
    width = max(card.winfo_reqwidth() + 32, 400)
    height = card.winfo_reqheight() + 32
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    if cancel_btn is not None:
        cancel_btn.focus_set()
    elif ok_btn is not None:
        ok_btn.focus_set()

    root.mainloop()
    return result["ok"]
