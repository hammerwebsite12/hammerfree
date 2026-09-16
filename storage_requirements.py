"""Disk space checks for download + extraction peak usage."""

from __future__ import annotations

from archive_utils import format_bytes

# During extract the archive and extracted folder coexist — plan for ~2× game size.
EXTRACT_PEAK_MULTIPLIER = 2.0
# Keep some room for OS / temp files.
MIN_OS_MARGIN_BYTES = 2 * 1024**3


def required_disk_bytes(estimated_game_bytes: int | None) -> int | None:
    if not estimated_game_bytes or estimated_game_bytes <= 0:
        return None
    return int(estimated_game_bytes * EXTRACT_PEAK_MULTIPLIER) + MIN_OS_MARGIN_BYTES


def evaluate_disk_space(
    free_bytes: int,
    estimated_game_bytes: int | None,
) -> dict[str, int | bool | None]:
    required = required_disk_bytes(estimated_game_bytes)
    if required is None:
        return {
            "ok": True,
            "unknown_size": True,
            "free_bytes": free_bytes,
            "required_bytes": None,
            "estimated_game_bytes": None,
            "shortfall_bytes": 0,
        }
    ok = free_bytes >= required
    return {
        "ok": ok,
        "unknown_size": False,
        "free_bytes": free_bytes,
        "required_bytes": required,
        "estimated_game_bytes": estimated_game_bytes,
        "shortfall_bytes": max(0, required - free_bytes),
    }


def space_error_message(check: dict[str, int | bool | None], title: str = "") -> str:
    prefix = f'"{title}" needs ' if title else "This game needs "
    if check.get("unknown_size"):
        return (
            f"{prefix}enough free space for the download and extraction, "
            f"but the estimated size is unknown. "
            f"Free space now: {format_bytes(int(check['free_bytes'] or 0))}."
        )
    required = int(check["required_bytes"] or 0)
    estimated = int(check["estimated_game_bytes"] or 0)
    free = int(check["free_bytes"] or 0)
    shortfall = int(check["shortfall_bytes"] or 0)
    return (
        f"{prefix}at least {format_bytes(required)} free "
        f"(download + extract for a ~{format_bytes(estimated)} game). "
        f"You have {format_bytes(free)} free — short by {format_bytes(shortfall)}."
    )
