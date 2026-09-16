"""Microsoft Defender exclusion helpers (Windows only)."""

from __future__ import annotations

import re
import subprocess
import sys
from typing import Any


def is_windows() -> bool:
    return sys.platform == "win32"


def is_defender_excluded(path: str) -> bool:
    if not is_windows() or not path:
        return False
    norm = _norm(path)
    try:
        result = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                "(Get-MpPreference).ExclusionPath",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode != 0:
            return False
        for line in result.stdout.splitlines():
            if _norm(line.strip()) == norm:
                return True
        return False
    except (OSError, subprocess.TimeoutExpired):
        return False


def format_manual_exclusion_help(folder_path: str) -> str:
    """Step-by-step English instructions when automatic exclusion fails."""
    path = folder_path.strip() or "(your QuickPlay download folder)"
    return (
        "Microsoft Defender may delete downloaded games if this folder is not excluded.\n\n"
        "Add it manually:\n"
        "1. Open Windows Security\n"
        "2. Virus & threat protection → Manage settings\n"
        "3. Exclusions → Add or remove exclusions\n"
        "4. Add an exclusion → Folder\n"
        f"5. Select:\n{path}"
    )


def format_defender_failure_detail(folder_path: str, detail: str = "") -> str:
    """Full message for dialogs and settings when auto-exclusion failed."""
    intro = detail.strip() if detail else "Could not add this folder to Microsoft Defender exclusions."
    return f"{intro}\n\n{format_manual_exclusion_help(folder_path)}"


def add_defender_exclusion(path: str) -> tuple[bool, str]:
    """Try to add folder to Defender exclusions. Returns (success, message)."""
    if not is_windows():
        return False, "Available on Windows only."

    path = path.strip()
    if not path:
        return False, "No folder path provided."

    if is_defender_excluded(path):
        return True, f"Already excluded: {path}"

    escaped = path.replace("'", "''")
    ps_cmd = f"Add-MpPreference -ExclusionPath '{escaped}'"
    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps_cmd],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0,
        )
        if result.returncode == 0 and is_defender_excluded(path):
            return True, f"Added to Microsoft Defender exclusions: {path}"

        err = (result.stderr or result.stdout or "").strip()
        if _access_denied(err):
            return False, format_defender_failure_detail(
                path,
                "Could not add Defender exclusion (Administrator required).",
            )
        if "not recognized" in err.lower() or "get-mppreference" in err.lower():
            return False, "Microsoft Defender is not available on this system."
        return False, format_defender_failure_detail(
            path,
            err or "Failed to add Defender exclusion automatically.",
        )
    except subprocess.TimeoutExpired:
        return False, format_defender_failure_detail(path, "Defender exclusion timed out.")
    except OSError as exc:
        return False, format_defender_failure_detail(path, f"Defender exclusion failed: {exc}")


def warn_defender_exclusion_on_startup(
    download_dir: str,
    defender_enabled: bool,
    settings_manager: Any | None = None,
) -> None:
    """Show a themed warning if the download folder is not excluded from Defender."""
    if not is_windows() or not defender_enabled or not download_dir.strip():
        return
    if is_defender_excluded(download_dir):
        return

    ok, msg = add_defender_exclusion(download_dir)
    if settings_manager is not None:
        settings_manager.set_defender_status(msg)
        settings_manager.save()
    if ok:
        return

    from native_dialog import show_info

    show_info(
        format_defender_failure_detail(download_dir, msg.split("\n\n")[0]),
        title="Defender Exclusion Required",
        wraplength=440,
    )


def try_apply_defender_exclusion(path: str, enabled: bool) -> tuple[bool, str]:
    if not enabled:
        return False, "Defender exclusion disabled in settings."
    return add_defender_exclusion(path)


def _norm(path: str) -> str:
    return re.sub(r"[\\/]+$", "", path.replace("/", "\\").lower())


def _access_denied(text: str) -> bool:
    low = text.lower()
    return any(
        k in low
        for k in (
            "access is denied",
            "access denied",
            "authorizedaccess",
            "elevation",
            "administrator",
            "unauthorized",
        )
    )
