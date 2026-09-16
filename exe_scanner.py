"""Scan install folders for launchable executables and batch launchers."""

from __future__ import annotations

import os
import subprocess
import sys

PLAYABLE_EXTENSIONS = (".exe", ".bat")

SKIP_KEYWORDS = (
    "unins",
    "uninstall",
    "setup",
    "install",
    "redist",
    "vcredist",
    "dxsetup",
    "dotnet",
    "crash",
    "error",
    "support",
    "helper",
    "update",
)

# Penalize generic launchers for .exe picks, but keep .bat launchers visible.
SKIP_KEYWORDS_EXE_ONLY = ("launcher",)

PREFER_KEYWORDS = (
    "game",
    "play",
    "start",
    "main",
    "client",
    "run",
)


def scan_executables(root_dir: str) -> list[str]:
    found: list[str] = []
    root_dir = os.path.abspath(root_dir)
    for dirpath, _dirnames, filenames in os.walk(root_dir):
        for filename in filenames:
            lower = filename.lower()
            if not lower.endswith(PLAYABLE_EXTENSIONS):
                continue
            full_path = os.path.join(dirpath, filename)
            found.append(full_path)
    return sorted(found, key=_score_path, reverse=True)


def _score_path(path: str) -> tuple[int, int, str]:
    name = os.path.basename(path).lower()
    score = 0
    if any(keyword in name for keyword in SKIP_KEYWORDS):
        score -= 100
    if name.endswith(".exe") and any(keyword in name for keyword in SKIP_KEYWORDS_EXE_ONLY):
        score -= 100
    if any(keyword in name for keyword in PREFER_KEYWORDS):
        score += 20
    if name.endswith(".bat"):
        score += 5
    depth = path.count(os.sep)
    return (score, -depth, name)


def display_name(path: str, root_dir: str) -> str:
    rel = os.path.relpath(path, root_dir)
    return rel.replace("\\", " / ")


def pick_best_exe(exes: list[str]) -> str:
    if not exes:
        return ""
    ranked = sorted(exes, key=_score_path, reverse=True)
    return ranked[0]


def is_batch_launcher(path: str) -> bool:
    return os.path.splitext(path)[1].lower() == ".bat"


def launch_play_target(path: str) -> None:
    """Launch a library PLAY target (.exe normally, .bat elevated on Windows)."""
    target = os.path.abspath(path)
    if not os.path.isfile(target):
        raise FileNotFoundError(target)
    workdir = os.path.dirname(target) or None

    if sys.platform == "win32" and is_batch_launcher(target):
        import ctypes

        rc = ctypes.windll.shell32.ShellExecuteW(
            None,
            "runas",
            target,
            None,
            workdir,
            1,
        )
        if rc <= 32:
            raise OSError(f"Failed to launch batch file (ShellExecute {rc})")
        return

    subprocess.Popen([target], cwd=workdir, shell=False)
