"""Disk cache for library cover art — served locally for offline My Library."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import requests

from app_paths import cache_dir
from playzip_api import USER_AGENT, normalize_image_url

if TYPE_CHECKING:
    from download_logger import DownloadLogger
    from library_manager import LibraryEntry

_LOCKS: dict[str, threading.Lock] = {}
_META_LOCK = threading.Lock()


def _entry_lock(entry_id: str) -> threading.Lock:
    with _META_LOCK:
        if entry_id not in _LOCKS:
            _LOCKS[entry_id] = threading.Lock()
        return _LOCKS[entry_id]


def cover_path_for_entry(entry_id: str) -> str:
    safe = "".join(ch for ch in entry_id if ch.isalnum() or ch in "-_") or "entry"
    return os.path.join(cache_dir(), f"{safe}.img")


def has_cached_cover(entry_id: str) -> bool:
    path = cover_path_for_entry(entry_id)
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def read_cached_cover(entry_id: str) -> bytes | None:
    path = cover_path_for_entry(entry_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return data if data else None
    except OSError:
        return None


def guess_media_type(data: bytes) -> str:
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    return "image/jpeg"


def cache_cover_for_entry(
    entry_id: str,
    image_url: str,
    *,
    logger: DownloadLogger | None = None,
) -> bool:
    """Download remote cover art to disk. Returns True when a file exists afterward."""
    url = normalize_image_url(image_url)
    if not entry_id or not url:
        return False

    path = cover_path_for_entry(entry_id)
    if has_cached_cover(entry_id):
        return True

    with _entry_lock(entry_id):
        if has_cached_cover(entry_id):
            return True
        try:
            response = requests.get(
                url,
                timeout=(8, 30),
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            data = response.content
            if not data:
                return False
            tmp_path = f"{path}.tmp"
            with open(tmp_path, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, path)
            if logger:
                logger.debug(f"Cached library cover: {entry_id}")
            return True
        except Exception as exc:
            if logger:
                logger.debug(f"Cover cache skipped for {entry_id}: {exc}")
            try:
                if os.path.isfile(f"{path}.tmp"):
                    os.unlink(f"{path}.tmp")
            except OSError:
                pass
            return has_cached_cover(entry_id)


def library_cover_url(entry_id: str, image_url: str) -> str:
    """Return a URL the web UI can load — local API when cached, else remote."""
    if has_cached_cover(entry_id):
        return f"/api/covers/{entry_id}"
    return normalize_image_url(image_url)


def warm_library_covers(
    entries: list[LibraryEntry],
    *,
    logger: DownloadLogger | None = None,
) -> int:
    """Cache missing covers for all library rows. Returns number newly cached."""
    cached = 0
    for entry in entries:
        if not entry.image_url or has_cached_cover(entry.entry_id):
            continue
        if cache_cover_for_entry(entry.entry_id, entry.image_url, logger=logger):
            if has_cached_cover(entry.entry_id):
                cached += 1
    return cached
