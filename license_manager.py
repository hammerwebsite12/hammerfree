"""QuickPlay licensing — HWID helpers, one-time startup check, registration UI.

License validation runs on the download Worker (not in this app). The client
never holds a GitHub PAT or talks to the userbase repo directly.

On first startup only, the app contacts the Worker once to verify registration.
After that flag is saved, no further online checks run at launch — downloads
still require a valid license via the Worker.
"""

from __future__ import annotations

import hashlib
import hmac
import subprocess
import sys
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING

import requests

from hwid_obfuscation import encode_uuid_to_code42

if TYPE_CHECKING:
    from settings_manager import SettingsManager

try:
    from client_secrets import APP_TOKEN, SIGNING_SECRET, WORKER_URL
except ImportError:  # pragma: no cover - template fallback
    WORKER_URL = ""
    APP_TOKEN = ""
    SIGNING_SECRET = ""

_APP_TITLE = "QuickPlay"

_MSG_NOT_REGISTERED = (
    "Device is not registered. Please activate.\n\n"
    "Copy your registration code below and send it to the QuickPlay seller."
)
_MSG_CUSTOM_OS = (
    "Device is not registered. Please activate.\n\n"
    "Custom OS detected — shared hardware ID. Send BOTH the registration code "
    "and device fingerprint to the QuickPlay seller for manual activation."
)
_MSG_NO_INTERNET = (
    "Cannot validate activation — no internet connection.\n\n"
    "QuickPlay needs internet once on first startup to verify your device. "
    "Connect to the internet and restart QuickPlay.\n\n"
    "Activation cannot be completed without internet."
)
_MSG_CLOCK = (
    "Cannot validate activation — system clock may be incorrect.\n\n"
    "Sync your PC date and time, then restart QuickPlay to complete the "
    "one-time activation check."
)
_MSG_SERVICE = (
    "Cannot validate activation — license service is temporarily unavailable.\n\n"
    "Check your internet connection and restart QuickPlay later."
)


class LicenseRequiredError(RuntimeError):
    """Raised when the download Worker rejects an unregistered device."""

    def __init__(self, message: str = "", *, custom_os: bool = False) -> None:
        super().__init__(message)
        self.custom_os = custom_os


@dataclass
class LicenseProbeResult:
    """Result of a one-time online license probe."""

    ok: bool = False
    licensed: bool = False
    custom_os: bool = False
    offline: bool = False
    user_message: str = ""


def get_device_uuid() -> str:
    """Return the machine UUID via PowerShell (same source as Hammer)."""
    try:
        completed = subprocess.run(
            [
                "powershell.exe",
                "-NoProfile",
                "-Command",
                "Get-CimInstance -Class Win32_ComputerSystemProduct "
                "| Select-Object -ExpandProperty UUID",
            ],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        value = (completed.stdout or "").strip()
        return value or "unknown-uuid"
    except Exception:
        return "unknown-uuid"


def get_formatted_hwid() -> str:
    """32-char uppercase hex HWID stem used for userbase filenames."""
    return get_device_uuid().replace("-", "").upper()


def build_registration_code() -> str:
    """42-char obfuscated registration code for activation."""
    return encode_uuid_to_code42(get_device_uuid())


def _result_from_probe_response(resp: requests.Response) -> LicenseProbeResult:
    if resp.status_code == 401:
        return LicenseProbeResult(user_message=_MSG_CLOCK)
    if resp.status_code in (502, 503):
        return LicenseProbeResult(user_message=_MSG_SERVICE)
    if resp.status_code == 429:
        return LicenseProbeResult(
            user_message=(
                "Cannot validate activation — too many requests.\n\n"
                "Wait a minute and restart QuickPlay."
            ),
        )
    if resp.status_code == 403:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get("error") == "license_required":
            return LicenseProbeResult(
                ok=True,
                licensed=False,
                custom_os=bool(data.get("custom_os")),
            )
    if resp.status_code == 200:
        try:
            data = resp.json()
        except ValueError:
            data = {}
        if data.get("licensed") is not None:
            return LicenseProbeResult(
                ok=True,
                licensed=bool(data.get("licensed")),
                custom_os=bool(data.get("custom_os")),
            )
        return LicenseProbeResult(ok=True, licensed=True)
    return LicenseProbeResult(user_message=_MSG_SERVICE)


def _probe_license_via_sign(hwid: str, device_fp: str) -> LicenseProbeResult:
    """Fallback when /license/check is not deployed yet — uses existing /sign gate."""
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    game_id = "0"
    sig = hmac.new(
        SIGNING_SECRET.encode(),
        f"{ts}.{nonce}.{game_id}.{hwid}.{device_fp}".encode(),
        hashlib.sha256,
    ).hexdigest()
    payload = {
        "game_id": game_id,
        "hwid": hwid,
        "device_fp": device_fp,
        "ts": ts,
        "nonce": nonce,
        "sig": sig,
        "token": APP_TOKEN,
    }
    try:
        resp = requests.post(
            f"{WORKER_URL}/sign",
            json=payload,
            timeout=(8, 20),
        )
    except requests.RequestException:
        return LicenseProbeResult(offline=True, user_message=_MSG_NO_INTERNET)
    return _result_from_probe_response(resp)


def probe_license_status() -> LicenseProbeResult:
    """Ask the Worker whether this device is registered (no download signing)."""
    if not WORKER_URL or not APP_TOKEN or not SIGNING_SECRET:
        return LicenseProbeResult(
            offline=True,
            user_message=_MSG_SERVICE,
        )

    from device_fingerprint import get_device_fingerprint

    hwid = get_formatted_hwid().lower()
    device_fp = get_device_fingerprint()
    ts = str(int(time.time()))
    nonce = uuid.uuid4().hex
    sig = hmac.new(
        SIGNING_SECRET.encode(),
        f"{ts}.{nonce}.{hwid}.{device_fp}".encode(),
        hashlib.sha256,
    ).hexdigest()

    payload = {
        "hwid": hwid,
        "device_fp": device_fp,
        "ts": ts,
        "nonce": nonce,
        "sig": sig,
        "token": APP_TOKEN,
    }

    try:
        resp = requests.post(
            f"{WORKER_URL}/license/check",
            json=payload,
            timeout=(8, 20),
        )
    except requests.RequestException:
        return LicenseProbeResult(offline=True, user_message=_MSG_NO_INTERNET)

    if resp.status_code == 404:
        return _probe_license_via_sign(hwid, device_fp)

    return _result_from_probe_response(resp)


_startup_activation: dict[str, object] | None = None


def consume_startup_activation() -> dict[str, object] | None:
    """Return and clear one-shot startup activation payload for the web UI."""
    global _startup_activation
    data = _startup_activation
    _startup_activation = None
    return data


def perform_first_startup_license_check(settings: SettingsManager) -> None:
    """One-time online license check before the main UI opens (Hammer-style)."""
    if settings.first_license_check_done:
        return

    result = probe_license_status()
    if not result.ok:
        from native_dialog import show_info

        show_info(result.user_message or _MSG_NO_INTERNET, f"{_APP_TITLE} — Activation")
        return

    settings.mark_first_license_check_done()

    if result.licensed:
        return

    from device_fingerprint import get_device_fingerprint

    code = build_registration_code()
    device_fp = get_device_fingerprint() if result.custom_os else ""
    global _startup_activation
    _startup_activation = {
        "code": code,
        "custom_os": result.custom_os,
        "device_fingerprint": device_fp,
        "startup": True,
    }


def show_registration_dialog(
    code: str | None = None,
    *,
    custom_os: bool = False,
    device_fingerprint: str = "",
    startup: bool = False,
) -> str:
    """Show modal with copyable registration code. Returns the code shown."""
    code = code or build_registration_code()
    if custom_os and not device_fingerprint:
        from device_fingerprint import get_device_fingerprint

        device_fingerprint = get_device_fingerprint()

    if sys.platform != "win32":
        message = _MSG_CUSTOM_OS if custom_os else _MSG_NOT_REGISTERED
        _message_box(f"{message}\n\n{code}", f"{_APP_TITLE} — Activation Required")
        return code

    try:
        import tkinter as tk
    except Exception:
        message = _MSG_CUSTOM_OS if custom_os else _MSG_NOT_REGISTERED
        _message_box(f"{message}\n\n{code}", f"{_APP_TITLE} — Activation Required")
        return code

    from native_dialog import apply_window_icon

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

    root = tk.Tk()
    title = f"{_APP_TITLE} — Activation Required"
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
        font=("Segoe UI", 11, "bold"),
        anchor="w",
    ).pack(fill="x")

    body = _MSG_CUSTOM_OS if custom_os else _MSG_NOT_REGISTERED
    if not startup:
        body = (
            "This device is not activated. Copy your registration code and send it "
            "to the QuickPlay seller to activate before downloading games."
        )
        if custom_os:
            body = _MSG_CUSTOM_OS

    tk.Label(
        card,
        text=body,
        bg=_CARD,
        fg=_MUTED,
        font=("Segoe UI", 10),
        justify="left",
        wraplength=480,
        anchor="w",
    ).pack(fill="x", pady=(10, 12))

    tk.Label(
        card,
        text="Registration Code",
        bg=_CARD,
        fg=_FG,
        font=("Segoe UI", 9, "bold"),
        anchor="w",
    ).pack(fill="x")

    code_entry = tk.Entry(
        card,
        font=("Consolas", 11, "bold"),
        bg="#141414",
        fg="#ffffff",
        readonlybackground="#141414",
        relief="flat",
        justify="center",
    )
    code_entry.insert(0, code)
    code_entry.configure(state="readonly")
    code_entry.pack(fill="x", ipady=6, pady=(4, 0))

    fp_entry = None
    if custom_os and device_fingerprint:
        tk.Label(
            card,
            text="Device Fingerprint",
            bg=_CARD,
            fg=_FG,
            font=("Segoe UI", 9, "bold"),
            anchor="w",
        ).pack(fill="x", pady=(12, 0))

        fp_entry = tk.Entry(
            card,
            font=("Consolas", 9),
            bg="#141414",
            fg="#ffffff",
            readonlybackground="#141414",
            relief="flat",
            justify="center",
        )
        fp_entry.insert(0, device_fingerprint)
        fp_entry.configure(state="readonly")
        fp_entry.pack(fill="x", ipady=4, pady=(4, 0))

    status = tk.Label(card, text="", bg=_CARD, fg="#8fdc9b", font=("Segoe UI", 9))
    status.pack(anchor="w", pady=(8, 0))

    btn_row = tk.Frame(card, bg=_CARD)
    btn_row.pack(fill="x", pady=(16, 0))

    def copy_text(text: str, label: str) -> None:
        root.clipboard_clear()
        root.clipboard_append(text)
        status.configure(text=label)

    def copy_both() -> None:
        copy_text(
            f"Registration Code: {code}\nDevice Fingerprint: {device_fingerprint}",
            "Copied registration code and fingerprint.",
        )

    if custom_os and device_fingerprint:
        tk.Button(
            btn_row,
            text="Copy Both",
            command=copy_both,
            bg=_ACCENT,
            fg=_ACCENT_FG,
            activebackground=_ACCENT_ACTIVE,
            activeforeground=_ACCENT_FG,
            relief="flat",
            font=("Segoe UI", 10, "bold"),
            padx=14,
            pady=7,
            cursor="hand2",
            borderwidth=0,
        ).pack(side="right")

    tk.Button(
        btn_row,
        text="Copy Code",
        command=lambda: copy_text(code, "Copied to clipboard."),
        bg=_ACCENT if not custom_os else _BTN_SECONDARY,
        fg=_ACCENT_FG if not custom_os else _FG,
        activebackground=_ACCENT_ACTIVE if not custom_os else _BTN_SECONDARY_ACTIVE,
        activeforeground=_ACCENT_FG if not custom_os else _FG,
        relief="flat",
        font=("Segoe UI", 10, "bold"),
        padx=14,
        pady=7,
        cursor="hand2",
        borderwidth=0,
    ).pack(side="right", padx=(0, 8))

    tk.Button(
        btn_row,
        text="Close",
        command=root.destroy,
        bg=_BTN_SECONDARY,
        fg=_FG,
        activebackground=_BTN_SECONDARY_ACTIVE,
        activeforeground=_FG,
        relief="flat",
        font=("Segoe UI", 10, "bold"),
        padx=14,
        pady=7,
        cursor="hand2",
        borderwidth=0,
    ).pack(side="right", padx=(0, 8))

    try:
        copy_text(code, "Copied to clipboard.")
    except tk.TclError:
        pass

    root.update_idletasks()
    width = max(card.winfo_reqwidth() + 32, 520)
    height = card.winfo_reqheight() + 32
    x = (root.winfo_screenwidth() - width) // 2
    y = (root.winfo_screenheight() - height) // 2
    root.geometry(f"{width}x{height}+{x}+{y}")

    root.mainloop()
    return code


# Windows MessageBox flags.
_MB_OK = 0x0
_MB_ICONINFO = 0x40


def _message_box(text: str, title: str, flags: int = _MB_OK | _MB_ICONINFO) -> int:
    from native_dialog import show_info

    show_info(text, title=title)
    return 0
