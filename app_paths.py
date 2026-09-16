"""Application paths — works in dev and PyInstaller frozen builds."""

from __future__ import annotations

import os
import sys


def app_root_dir() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def bundle_dir() -> str:
    if getattr(sys, "frozen", False):
        return getattr(sys, "_MEIPASS", app_root_dir())
    return os.path.dirname(os.path.abspath(__file__))


def web_dir() -> str:
    return os.path.join(bundle_dir(), "web")


def bundled_7z_path() -> str | None:
    """Path to the 7-Zip console binary shipped with the app, if present.

    Works both in dev (project ./vendor/7zip) and frozen builds (extracted
    beside the other bundled data under sys._MEIPASS/vendor/7zip).
    """
    exe = "7z.exe" if sys.platform == "win32" else "7zz"
    candidate = os.path.join(bundle_dir(), "vendor", "7zip", exe)
    if os.path.isfile(candidate):
        return candidate
    return None


def settings_file_path() -> str:
    return os.path.join(app_root_dir(), "settings.json")


def cache_dir() -> str:
    path = os.path.join(app_root_dir(), "cache", "covers")
    os.makedirs(path, exist_ok=True)
    return path


def icon_path() -> str | None:
    """Path to quickplay.ico in dev or frozen builds."""
    candidates = (
        os.path.join(bundle_dir(), "quickplay.ico"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "quickplay.ico"),
    )
    for path in candidates:
        if os.path.isfile(path):
            return path
    return None
