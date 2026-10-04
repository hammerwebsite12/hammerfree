"""Resolve cover art for library entries imported from disk scan."""

from __future__ import annotations

import html
import json
import os
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import requests

from playzip_api import USER_AGENT

if TYPE_CHECKING:
    from download_logger import DownloadLogger
    from store_manager import StoreManager

PLAYZIP_CAP_RE = re.compile(
    r'class="capsule_div"[^>]*>.*?<img[^>]+src="([^"]+)"',
    re.S | re.I,
)
ANKER_LISTING_RE = re.compile(
    r'<article[^>]*\slisting="(\{.*?\})"',
    re.S | re.I,
)

# Wide/hero assets — never use for portrait library capsules.
_HERO_URL_MARKERS = (
    "library_hero",
    "header.jpg",
    "/hero",
    "capsule_616x353",
    "header_image",
    "screenshot",
    "/ss_",
)


@dataclass
class ArtworkMatch:
    game_id: str
    title: str
    image_url: str
    source: str = ""


def _normalize_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


def _folder_search_queries(folder_title: str, folder_slug: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    short_title = re.split(r"[:\-–|]", folder_title, maxsplit=1)[0].strip()
    for raw in (
        folder_title.replace(".", " ").replace("_", " ").strip(),
        short_title.replace(".", " ").replace("_", " ").strip(),
        folder_slug.replace("-", " ").strip(),
        folder_slug.strip(),
    ):
        q = re.sub(r"\s+", " ", raw).strip()
        key = q.lower()
        if q and key not in seen:
            seen.add(key)
            out.append(q)
    return out


def _pick_search_match(
    results: list[Any],
    folder_title: str,
    folder_slug: str,
) -> Any | None:
    if not results:
        return None
    slug = (folder_slug or "").lower().strip()
    folder_key = _normalize_key(folder_title.replace(".", " ").replace("_", " "))

    for game in results:
        if (getattr(game, "game_id", "") or "").lower() == slug:
            return game
    for game in results:
        if _normalize_key(getattr(game, "title", "")) == folder_key:
            return game
    partial = [
        g
        for g in results
        if folder_key
        and len(folder_key) >= 4
        and folder_key in _normalize_key(getattr(g, "title", ""))
    ]
    if len(partial) == 1:
        return partial[0]
    return None


def _normalize_poster_url(client: Any, image: str) -> str:
    from playzip_api import normalize_image_url

    image = html.unescape((image or "").strip())
    if not image:
        return ""
    if not image.startswith("http"):
        base = getattr(client, "base_url", "") or getattr(client, "_base_url", "")
        if base:
            if "poster" in image or not image.startswith("/"):
                image = f"{base.rstrip('/')}/uploads/poster/{image.lstrip('/')}"
            else:
                image = f"{base.rstrip('/')}{image}"
    return normalize_image_url(image)


def _looks_like_hero_url(url: str) -> bool:
    lower = (url or "").lower()
    return any(marker in lower for marker in _HERO_URL_MARKERS)


def _looks_like_bad_library_cover_url(url: str) -> bool:
    """Wide/micro Steam assets that look broken when scaled into a 2:3 capsule."""
    if not url:
        return True
    if _looks_like_hero_url(url):
        return True
    lower = url.lower()
    if "capsule_231" in lower or "capsule_616x353" in lower:
        return True
    match = re.search(r"capsule_(\d+)x(\d+)", lower)
    if match:
        width, height = int(match.group(1)), int(match.group(2))
        if width >= height * 1.15:
            return True
    if "header_image" in lower or "/header." in lower:
        return True
    return False


def _steam_portrait_cover_urls(app_id: str) -> list[str]:
    bases = (
        "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps",
        "https://shared.fastly.steamstatic.com/store_item_assets/steam/apps",
        "https://cdn.akamai.steamstatic.com/steam/apps",
    )
    names = (
        "library_600x900_2x.jpg",
        "library_600x900.jpg",
        "library_capsule_2x.jpg",
    )
    urls: list[str] = []
    seen: set[str] = set()
    for base in bases:
        for name in names:
            url = f"{base}/{app_id}/{name}"
            if url not in seen:
                seen.add(url)
                urls.append(url)
    return urls


def _steam_cover_url_reachable(url: str) -> bool:
    try:
        response = requests.head(
            url,
            timeout=8,
            allow_redirects=True,
            headers={"User-Agent": USER_AGENT},
        )
        if response.status_code == 405:
            response = requests.get(
                url,
                timeout=12,
                stream=True,
                headers={"User-Agent": USER_AGENT},
            )
            response.raise_for_status()
            return True
        return response.status_code < 400 and int(response.headers.get("Content-Length") or 1) > 512
    except Exception:
        return False


def _decode_anker_listing(raw: str) -> dict[str, Any] | None:
    cleaned = html.unescape(raw.strip()).replace("\\/", "/")
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _extract_listing_poster_from_page(
    client: Any,
    page_html: str,
    slug: str,
    store_id: str,
) -> str:
    """Extract vertical listing poster from a game detail page (never og:image)."""
    if not page_html:
        return ""

    slug_l = slug.lower()
    from store_manager import STORE_SERVER2

    if store_id == STORE_SERVER2:
        for match in ANKER_LISTING_RE.finditer(page_html):
            item = _decode_anker_listing(match.group(1))
            if not item:
                continue
            item_slug = str(item.get("slug") or "").strip().lower()
            if item_slug and item_slug != slug_l:
                continue
            image = str(item.get("imageurl") or item.get("coverurl") or "").strip()
            poster = _normalize_poster_url(client, image)
            if poster and not _looks_like_hero_url(poster):
                return poster

    cap = PLAYZIP_CAP_RE.search(page_html)
    if cap:
        from playzip_api import normalize_image_url

        poster = normalize_image_url(html.unescape(cap.group(1).strip()))
        if poster and not _looks_like_hero_url(poster):
            return poster

    return ""


def _resolve_via_search(
    store: StoreManager,
    folder_title: str,
    folder_slug: str,
    store_order: list[str],
    logger: DownloadLogger | None,
) -> ArtworkMatch | None:
    search_cache: dict[str, list[Any]] = {}
    slug = (folder_slug or "").lower().strip()

    for store_id in store_order:
        client = store.get_client_for(store_id)
        for query in _folder_search_queries(folder_title, folder_slug):
            cache_key = f"{store_id}:{query.lower()}"
            if cache_key not in search_cache:
                try:
                    search_cache[cache_key] = client.search(query)
                except Exception:
                    search_cache[cache_key] = []
            match = _pick_search_match(search_cache[cache_key], folder_title, folder_slug)
            if not match or not getattr(match, "image_url", ""):
                continue
            image_url = getattr(match, "image_url", "")
            if _looks_like_bad_library_cover_url(image_url):
                continue
            if logger:
                logger.info(
                    f"Cover art resolved via {store_id} search: "
                    f"{getattr(match, 'title', folder_title)}"
                )
            return ArtworkMatch(
                game_id=getattr(match, "game_id", slug),
                title=getattr(match, "title", ""),
                image_url=image_url,
                source="search",
            )
    return None


def _resolve_via_game_page(
    store: StoreManager,
    folder_title: str,
    folder_slug: str,
    store_order: list[str],
    logger: DownloadLogger | None,
) -> ArtworkMatch | None:
    slug = (folder_slug or "").strip().lower()
    if not slug or slug == "imported-game":
        return None

    for store_id in store_order:
        client = store.get_client_for(store_id)
        try:
            page_html = client.fetch_page(f"/game/{slug}")
            poster = _extract_listing_poster_from_page(client, page_html or "", slug, store_id)
            if poster:
                if logger:
                    logger.info(f"Cover art resolved via {store_id} listing: {folder_title}")
                return ArtworkMatch(
                    game_id=slug,
                    title="",
                    image_url=poster,
                    source="listing",
                )
        except Exception as exc:
            if logger:
                logger.debug(f"Game page lookup failed ({store_id}/{slug}): {exc}")
    return None


def _find_steam_app_id(install_dir: str) -> str:
    root = os.path.abspath(install_dir or "")
    if not root or not os.path.isdir(root):
        return ""
    for dirpath, dirnames, filenames in os.walk(root):
        depth = dirpath[len(root) :].count(os.sep)
        if depth > 6:
            dirnames.clear()
            continue
        if "steam_appid.txt" not in filenames:
            continue
        path = os.path.join(dirpath, "steam_appid.txt")
        try:
            with open(path, encoding="utf-8", errors="ignore") as handle:
                app_id = handle.read().strip()
        except OSError:
            continue
        if app_id.isdigit():
            return app_id
    return ""


def _resolve_via_steam_app(
    app_id: str,
    logger: DownloadLogger | None,
) -> ArtworkMatch | None:
    if not app_id.isdigit():
        return None
    try:
        response = requests.get(
            "https://store.steampowered.com/api/appdetails",
            params={"appids": app_id},
            timeout=15,
            headers={"User-Agent": USER_AGENT},
        )
        response.raise_for_status()
        payload = response.json().get(app_id) or {}
        if not payload.get("success"):
            return None
        data = payload.get("data") or {}
        image_url = ""
        for candidate in _steam_portrait_cover_urls(app_id):
            if _steam_cover_url_reachable(candidate):
                image_url = candidate
                break
        if not image_url:
            fallback = str(data.get("capsule_image") or data.get("header_image") or "").strip()
            if fallback and not _looks_like_bad_library_cover_url(fallback):
                image_url = fallback
        if not image_url or _looks_like_bad_library_cover_url(image_url):
            return None
        title = html.unescape(str(data.get("name") or "").strip())
        if logger:
            logger.info(f"Cover art resolved via Steam app {app_id}: {title or app_id}")
        return ArtworkMatch(
            game_id=app_id,
            title=title,
            image_url=image_url,
            source="steam",
        )
    except Exception as exc:
        if logger:
            logger.debug(f"Steam artwork lookup failed for {app_id}: {exc}")
        return None


def resolve_library_artwork(
    store: StoreManager,
    folder_title: str,
    folder_slug: str,
    logger: DownloadLogger | None = None,
    *,
    install_dir: str = "",
) -> ArtworkMatch | None:
    """Look up portrait capsule art — browse/search poster first, never og:image."""
    from store_manager import STORE_SERVER1, STORE_SERVER2

    store_order = [store.active_store]
    alt = STORE_SERVER2 if store_order[0] == STORE_SERVER1 else STORE_SERVER1
    store_order.append(alt)

    hit = _resolve_via_search(store, folder_title, folder_slug, store_order, logger)
    if hit:
        return hit

    hit = _resolve_via_game_page(store, folder_title, folder_slug, store_order, logger)
    if hit:
        return hit

    slug = (folder_slug or "").strip()
    if slug.isdigit():
        hit = _resolve_via_steam_app(slug, logger)
        if hit:
            return hit

    app_id = _find_steam_app_id(install_dir)
    if app_id:
        return _resolve_via_steam_app(app_id, logger)
    return None


def should_refresh_artwork(image_url: str) -> bool:
    """True when library row has no art or a wide/broken cover URL."""
    if not image_url:
        return True
    return _looks_like_bad_library_cover_url(image_url)
