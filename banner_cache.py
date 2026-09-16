"""Disk cache for Big Picture hero/banner art — offline fullscreen backgrounds."""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING

import requests

from app_paths import cache_dir
from cover_cache import guess_media_type, read_cached_cover
from game_details import STEAM_HERO, steam_app_id_from_url
from playzip_api import USER_AGENT, normalize_image_url

if TYPE_CHECKING:
    from download_logger import DownloadLogger
    from library_manager import LibraryEntry
    from store_manager import StoreManager

_LOCKS: dict[str, threading.Lock] = {}
_META_LOCK = threading.Lock()


def _entry_lock(entry_id: str) -> threading.Lock:
    with _META_LOCK:
        if entry_id not in _LOCKS:
            _LOCKS[entry_id] = threading.Lock()
        return _LOCKS[entry_id]


def banner_path_for_entry(entry_id: str) -> str:
    safe = "".join(ch for ch in entry_id if ch.isalnum() or ch in "-_") or "entry"
    root = os.path.join(os.path.dirname(cache_dir()), "banners")
    os.makedirs(root, exist_ok=True)
    return os.path.join(root, f"{safe}.img")


def has_cached_banner(entry_id: str) -> bool:
    path = banner_path_for_entry(entry_id)
    try:
        return os.path.isfile(path) and os.path.getsize(path) > 0
    except OSError:
        return False


def read_cached_banner(entry_id: str) -> bytes | None:
    path = banner_path_for_entry(entry_id)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "rb") as handle:
            data = handle.read()
        return data if data else None
    except OSError:
        return None


def _download_to_path(url: str, path: str, *, logger: DownloadLogger | None) -> bool:
    target = normalize_image_url(url)
    if not target:
        return False
    try:
        response = requests.get(
            target,
            timeout=(8, 45),
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        data = response.content
        if not data or len(data) < 512:
            return False
        tmp_path = f"{path}.tmp"
        with open(tmp_path, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
        return True
    except Exception as exc:
        if logger:
            logger.debug(f"Banner cache skipped: {exc}")
        try:
            if os.path.isfile(f"{path}.tmp"):
                os.unlink(f"{path}.tmp")
        except OSError:
            pass
        return False


def resolve_banner_source_url(
    entry: LibraryEntry,
    store: StoreManager,
    *,
    logger: DownloadLogger | None = None,
) -> str:
    """Resolve a wide hero/banner URL for a library entry."""
    steam_id = steam_app_id_from_url(entry.image_url or "")
    if steam_id:
        return STEAM_HERO.format(app_id=steam_id)

    try:
        service = store.create_details_service(logger)
        details = service.fetch(entry.game_id, entry.title, entry.image_url)
        hero = normalize_image_url(details.hero_image or "")
        if hero and hero != normalize_image_url(entry.image_url or ""):
            return hero
        if details.screenshots:
            full = (details.screenshots[0].get("full") or "").strip()
            if full:
                return normalize_image_url(full)
    except Exception as exc:
        if logger:
            logger.debug(f"Banner resolve failed for {entry.entry_id}: {exc}")

    return normalize_image_url(entry.image_url or "")


def cache_banner_for_entry(
    entry: LibraryEntry,
    store: StoreManager,
    *,
    logger: DownloadLogger | None = None,
) -> bool:
    if not entry.entry_id:
        return False
    path = banner_path_for_entry(entry.entry_id)
    if has_cached_banner(entry.entry_id):
        return True

    with _entry_lock(entry.entry_id):
        if has_cached_banner(entry.entry_id):
            return True
        source = resolve_banner_source_url(entry, store, logger=logger)
        if source and _download_to_path(source, path, logger=logger):
            return True
        # Fallback: reuse portrait cover bytes if hero fetch failed.
        cover = read_cached_cover(entry.entry_id)
        if cover:
            try:
                with open(path, "wb") as handle:
                    handle.write(cover)
                return True
            except OSError:
                pass
    return has_cached_banner(entry.entry_id)


def library_banner_url(entry_id: str) -> str:
    if has_cached_banner(entry_id):
        return f"/api/banners/{entry_id}"
    return ""


def warm_library_banners(
    entries: list[LibraryEntry],
    store: StoreManager,
    *,
    logger: DownloadLogger | None = None,
) -> int:
    cached = 0
    for entry in entries:
        if has_cached_banner(entry.entry_id):
            continue
        if cache_banner_for_entry(entry, store, logger=logger):
            if has_cached_banner(entry.entry_id):
                cached += 1
    return cached
