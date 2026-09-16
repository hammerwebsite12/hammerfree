"""Disk space helpers for download folder."""

from __future__ import annotations

import os
import shutil


def disk_usage_for_path(path: str) -> dict[str, int | str]:
    target = os.path.abspath(path or ".")
    if not os.path.isdir(target):
        parent = os.path.dirname(target) or target
        target = parent if os.path.isdir(parent) else os.path.abspath(".")
    usage = shutil.disk_usage(target)
    return {
        "path": target,
        "free_bytes": usage.free,
        "total_bytes": usage.total,
        "used_bytes": usage.used,
    }
