"""Scan download folder for extracted games missing from library.json."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from exe_scanner import pick_best_exe, scan_executables

# Root-level names that are never game install folders.
_SKIP_DIR_NAMES = {
    ".git",
    ".quickplay_downloads.json",
    "cache",
    "node_modules",
    "vendor",
}

_ARCHIVE_EXT = (".zip", ".rar", ".7z", ".001", ".part")


@dataclass
class ScanCandidate:
    title: str
    install_dir: str
    game_id: str
    exe_count: int
    suggested_exe: str


def _normalize_dir(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _folder_to_game_id(folder_name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", folder_name.lower()).strip("-")
    return slug or "imported-game"


def _looks_like_game_folder(install_dir: str) -> bool:
    if not os.path.isdir(install_dir):
        return False
    try:
        names = os.listdir(install_dir)
    except OSError:
        return False
    if not names:
        return False

    has_exe = bool(scan_executables(install_dir))
    if has_exe:
        return True

    # Non-EXE games / Linux builds: require several files, not only fragments.
    file_count = 0
    for name in names:
        lower = name.lower()
        if lower.endswith(_ARCHIVE_EXT) or lower.endswith(".part"):
            continue
        full = os.path.join(install_dir, name)
        if os.path.isfile(full):
            file_count += 1
        elif os.path.isdir(full):
            file_count += 1
    return file_count >= 2


def scan_download_folder(
    download_dir: str,
    known_install_dirs: set[str],
) -> list[ScanCandidate]:
    """Return install folders under download_dir not already in the library."""
    root = os.path.abspath(download_dir or "")
    if not root or not os.path.isdir(root):
        return []

    known = {_normalize_dir(p) for p in known_install_dirs if p}
    results: list[ScanCandidate] = []

    try:
        entries = os.listdir(root)
    except OSError:
        return []

    for name in entries:
        if not name or name.startswith("."):
            continue
        if name.lower() in _SKIP_DIR_NAMES:
            continue

        full = os.path.join(root, name)
        if not os.path.isdir(full):
            continue
        if not _looks_like_game_folder(full):
            continue

        norm = _normalize_dir(full)
        if norm in known:
            continue

        exes = scan_executables(full)
        results.append(
            ScanCandidate(
                title=name,
                install_dir=full,
                game_id=_folder_to_game_id(name),
                exe_count=len(exes),
                suggested_exe=pick_best_exe(exes),
            )
        )

    results.sort(key=lambda c: c.title.lower())
    return results
