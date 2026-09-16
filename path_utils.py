"""Windows long-path helpers (MAX_PATH fix)."""

from __future__ import annotations

import os
import sys


def is_windows() -> bool:
    return sys.platform == "win32"


def long_path(path: str) -> str:
    """Return extended-length path for Windows APIs."""
    if not path or not is_windows():
        return path
    path = os.path.normpath(path)
    if path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    return "\\\\?\\" + path


def ensure_dir(path: str) -> None:
    """Create directory tree, using extended paths on Windows."""
    if not path:
        return
    norm = os.path.normpath(path)
    if is_windows():
        os.makedirs(long_path(norm), exist_ok=True)
    else:
        os.makedirs(norm, exist_ok=True)


def open_binary(path: str, mode: str = "wb"):
    """Open file with long-path support on Windows."""
    if is_windows():
        return open(long_path(path), mode)  # noqa: SIM115
    return open(path, mode)  # noqa: SIM115


def remove_tree(path: str) -> None:
    """Delete directory tree, using extended paths on Windows."""
    import shutil

    if not path or not os.path.exists(path):
        return
    if is_windows():
        shutil.rmtree(long_path(path))
    else:
        shutil.rmtree(path)
