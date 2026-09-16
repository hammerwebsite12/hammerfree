"""Windows administrator check, elevation relaunch, and first-run notice."""

from __future__ import annotations

import os
import sys

from native_dialog import show_confirm


def is_windows() -> bool:
    return sys.platform == "win32"


def is_admin() -> bool:
    if not is_windows():
        return True
    try:
        import ctypes

        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def ensure_admin_or_exit() -> None:
    """Require elevation on every start when not already running as Administrator."""
    if not is_windows() or is_admin():
        return

    import ctypes

    if not show_confirm(
        "QuickPlay must run as Administrator (elevated).\n\n"
        "This allows the app to add your download folder to Microsoft Defender "
        "exclusions so downloaded games are not removed as threats.\n\n"
        "Choose Run as Admin to continue, or Exit to close the app.",
        title="Administrator Required",
        ok_text="Run as Admin",
        cancel_text="Exit",
        wraplength=420,
    ):
        sys.exit(0)

    params = " ".join(f'"{arg}"' for arg in sys.argv)
    ctypes.windll.shell32.ShellExecuteW(
        None,
        "runas",
        sys.executable,
        params,
        None,
        1,
    )
    sys.exit(0)


def runtime_extract_dir() -> str:
    """Prefer LocalAppData for PyInstaller onefile extraction (avoids admin TEMP issues)."""
    if not is_windows():
        return ""
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        return ""
    path = os.path.join(local, "QuickPlay", "_runtime")
    os.makedirs(path, exist_ok=True)
    return path
