"""One-time hardware snapshot for seller audit (recorded server-side on first licensed download)."""

from __future__ import annotations

import json
import subprocess

from device_fingerprint import get_device_fingerprint
from license_manager import get_device_uuid

_SNAPSHOT_CACHE: str | None = None


def _run_powershell(script: str) -> str:
    try:
        completed = subprocess.run(
            ["powershell.exe", "-NoProfile", "-Command", script],
            capture_output=True,
            text=True,
            timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return (completed.stdout or "").strip()
    except Exception:
        return ""


def _collect_snapshot_fields() -> dict[str, str]:
    script = (
        "$cs = Get-CimInstance Win32_ComputerSystem -ErrorAction SilentlyContinue; "
        "$cpu = Get-CimInstance Win32_Processor -ErrorAction SilentlyContinue "
        "| Select-Object -First 1; "
        "$bios = Get-CimInstance Win32_BIOS -ErrorAction SilentlyContinue; "
        "$os = Get-CimInstance Win32_OperatingSystem -ErrorAction SilentlyContinue; "
        "$disk = Get-CimInstance Win32_DiskDrive -ErrorAction SilentlyContinue "
        "| Sort-Object Index | Select-Object -First 1; "
        "$mg = (Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Cryptography' "
        "-ErrorAction SilentlyContinue).MachineGuid; "
        "[pscustomobject]@{ "
        "computer = $env:COMPUTERNAME; "
        "manufacturer = [string]$cs.Manufacturer; "
        "model = [string]$cs.Model; "
        "cpu = [string]$cpu.Name; "
        "ram_mb = [string][int]([math]::Round(($cs.TotalPhysicalMemory / 1MB))); "
        "bios_version = [string]$bios.SMBIOSBIOSVersion; "
        "bios_serial = [string]$bios.SerialNumber; "
        "os = [string]$os.Caption; "
        "os_build = [string]$os.BuildNumber; "
        "disk_model = [string]$disk.Model; "
        "disk_serial = [string]$disk.SerialNumber; "
        "machine_guid = [string]$mg "
        "} | ConvertTo-Json -Compress"
    )
    raw = _run_powershell(script)
    defaults = {
        "computer": "",
        "manufacturer": "",
        "model": "",
        "cpu": "",
        "ram_mb": "",
        "bios_version": "",
        "bios_serial": "",
        "os": "",
        "os_build": "",
        "disk_model": "",
        "disk_serial": "",
        "machine_guid": "",
    }
    if not raw:
        return defaults
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return {key: str(parsed.get(key) or "").strip() for key in defaults}
    except json.JSONDecodeError:
        pass
    return defaults


def get_hardware_snapshot_text() -> str:
    """DxDiag-like multiline text sent once on first licensed /sign (server decides)."""
    global _SNAPSHOT_CACHE
    if _SNAPSHOT_CACHE is not None:
        return _SNAPSHOT_CACHE

    fields = _collect_snapshot_fields()
    uuid_stem = get_device_uuid().replace("-", "").upper()
    fp = get_device_fingerprint()

    lines = [
        f"Computer: {fields['computer'] or 'unknown'}",
        f"Board: {fields['manufacturer']} {fields['model']}".strip(),
        f"CPU: {fields['cpu'] or 'unknown'}",
        f"RAM: {fields['ram_mb'] or '?'} MB",
        f"BIOS: {fields['bios_version'] or '?'} (serial: {fields['bios_serial'] or '?'})",
        f"OS: {fields['os'] or 'Windows'} Build {fields['os_build'] or '?'}",
        f"Disk0: {fields['disk_model'] or '?'} (serial: {fields['disk_serial'] or '?'})",
        f"MachineGuid: {fields['machine_guid'] or '?'}",
        f"Device-FP: {fp}",
        f"HWID-Stem: {uuid_stem}",
    ]
    _SNAPSHOT_CACHE = "\n".join(lines)
    return _SNAPSHOT_CACHE
