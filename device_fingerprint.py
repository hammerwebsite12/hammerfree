"""Per-machine fingerprint for QuickPlay custom OS licensing (Phase 2)."""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys

from license_manager import get_device_uuid

_FP_CACHE: str | None = None


def _run_powershell(script: str) -> str:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=25,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (completed.stdout or "").strip()
    except Exception:
        return ""


def _collect_components() -> dict[str, str]:
    uuid_raw = get_device_uuid().strip()
    uuid_stem = uuid_raw.replace("-", "").lower()

    script = (
        "$mg = (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography' "
        "-ErrorAction SilentlyContinue).MachineGuid; "
        "$disk = (Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue "
        "| Sort-Object Index | Select-Object -First 1 -ExpandProperty SerialNumber); "
        "$bios = (Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue "
        "| Select-Object -ExpandProperty SerialNumber); "
        "[pscustomobject]@{ machine_guid = [string]$mg; "
        "disk_serial = [string]$disk; bios_serial = [string]$bios } "
        "| ConvertTo-Json -Compress"
    )
    raw = _run_powershell(script)
    extra = {"machine_guid": "", "disk_serial": "", "bios_serial": ""}
    if raw:
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                extra = {
                    "machine_guid": str(parsed.get("machine_guid") or "").strip().lower(),
                    "disk_serial": str(parsed.get("disk_serial") or "").strip().lower(),
                    "bios_serial": str(parsed.get("bios_serial") or "").strip().lower(),
                }
        except json.JSONDecodeError:
            pass

    return {
        "uuid": uuid_stem,
        "machine_guid": extra["machine_guid"],
        "disk_serial": extra["disk_serial"],
        "bios_serial": extra["bios_serial"],
    }


def get_device_fingerprint() -> str:
    """64-char lowercase SHA-256 hex bound to this physical install."""
    global _FP_CACHE
    if _FP_CACHE:
        return _FP_CACHE

    parts = _collect_components()
    payload = "|".join(
        parts[key]
        for key in ("uuid", "machine_guid", "disk_serial", "bios_serial")
    )
    _FP_CACHE = hashlib.sha256(payload.encode("utf-8")).hexdigest().lower()
    return _FP_CACHE


def is_valid_device_fingerprint(value: str) -> bool:
    cleaned = (value or "").strip().lower()
    return len(cleaned) == 64 and all(c in "0123456789abcdef" for c in cleaned)
