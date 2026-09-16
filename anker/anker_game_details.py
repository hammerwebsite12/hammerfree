"""Game details for AnkerGames pages + Steam API fallback."""

from __future__ import annotations

import html
import json
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from download_logger import DownloadLogger
    from anker.anker_api import AnkerGamesClient

from game_details import (
    GameDetails,
    GameDetailsService as _SteamDetailsMixin,
    _sanitize_description_html,
    _strip_html,
    steam_app_id_from_url,
)

JSON_LD_PATTERN = re.compile(
    r'<script[^>]+type="application/ld\+json"[^>]*>(.*?)</script>',
    re.S | re.I,
)
OG_IMAGE_PATTERN = re.compile(
    r'<meta[^>]+property="og:image"[^>]+content="([^"]+)"',
    re.I,
)
OG_DESC_PATTERN = re.compile(
    r'<meta[^>]+property="og:description"[^>]+content="([^"]+)"',
    re.I,
)
LISTING_ATTR_PATTERN = re.compile(
    r'<article[^>]*\slisting="(\{.*?\})"',
    re.S | re.I,
)
STEAM_STORE_APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)", re.I)
STEAM_APP_ID_JSON_RE = re.compile(r'"steam_app_id"\s*:\s*"(\d+)"')
OVERVIEW_PARAGRAPH_RE = re.compile(
    r'<p class="text-sm sm:text-base text-gray-600[^"]*"[^>]*>(.*?)</p>',
    re.S | re.I,
)
RELEASED_DATE_RE = re.compile(
    r'Released[^<]*<span[^>]*>([^<]+)</span>\s*</span>\s*<a',
    re.S | re.I,
)
PUBLISHER_RE = re.compile(
    r'Publisher[^<]*<span[^>]*>([^<]+)</span>',
    re.S | re.I,
)


class AnkerGameDetailsService(_SteamDetailsMixin):
    """Same interface as GameDetailsService but parses Anker HTML + JSON-LD."""

    def __init__(
        self,
        client: AnkerGamesClient,
        logger: DownloadLogger | None = None,
    ) -> None:
        self.client = client
        self.session = client.session
        self.logger = logger

    def fetch(
        self,
        game_id: str,
        title: str = "",
        image_url: str = "",
    ) -> GameDetails:
        from anker.anker_api import normalize_image_url

        slug = (game_id or "").strip()
        self._log(f"Loading game details for {slug}")

        details = GameDetails(
            game_id=slug,
            title=title,
            image_url=normalize_image_url(image_url),
            hero_image=normalize_image_url(image_url),
        )

        page_html = self.client.fetch_page(f"/game/{slug}")
        decoded = html.unescape(page_html or "")

        if decoded:
            self._parse_anker_page(decoded, slug, details)

        steam_id = details.steam_app_id or self._extract_steam_app_id(decoded, slug)
        if not steam_id:
            steam_id = steam_app_id_from_url(details.image_url) or steam_app_id_from_url(
                details.hero_image
            )

        if steam_id:
            details.steam_app_id = steam_id
            steam = self._fetch_steam(steam_id)
            if steam:
                self._merge_steam(details, steam)

        if not details.title and title:
            details.title = title
        if not details.hero_image and image_url:
            details.hero_image = normalize_image_url(image_url)

        return details

    def _extract_steam_app_id(self, page_html: str, slug: str) -> str:
        if not page_html:
            return ""

        h1 = re.search(r"<h1[^>]*>(.*?)</h1>", page_html, re.S | re.I)

        if h1:
            window = page_html[h1.start() : min(len(page_html), h1.end() + 120_000)]
            for pattern in (STEAM_STORE_APP_RE, STEAM_APP_ID_JSON_RE):
                match = pattern.search(window)
                if match:
                    return match.group(1)

        for pattern in (STEAM_STORE_APP_RE, STEAM_APP_ID_JSON_RE):
            match = pattern.search(page_html)
            if match:
                return match.group(1)
        return ""

    def _parse_listing_blob(self, raw: str) -> dict[str, Any] | None:
        cleaned = html.unescape(raw.strip()).replace("\\/", "/")
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _parse_anker_page(
        self,
        page_html: str,
        slug: str,
        details: GameDetails,
    ) -> None:
        from anker.anker_api import normalize_image_url

        self._parse_json_ld(page_html, slug, details)
        self._parse_visible_overview(page_html, details)

        if not details.hero_image:
            og = OG_IMAGE_PATTERN.search(page_html)
            if og:
                details.hero_image = html.unescape(og.group(1).strip())

        if not details.description:
            ogd = OG_DESC_PATTERN.search(page_html)
            if ogd:
                details.description = html.unescape(ogd.group(1).strip())

        for match in LISTING_ATTR_PATTERN.finditer(page_html):
            item = self._parse_listing_blob(match.group(1))
            if not item:
                continue
            if str(item.get("slug", "")).strip().lower() != slug.lower():
                continue
            self._apply_listing_item(item, details)
            break

        if details.hero_image:
            details.hero_image = normalize_image_url(details.hero_image)
        if details.image_url:
            details.image_url = normalize_image_url(details.image_url)

        if details.description_html or details.description or details.screenshots:
            details.source = "store" if not details.steam_app_id else details.source

    def _parse_json_ld(
        self,
        page_html: str,
        slug: str,
        details: GameDetails,
    ) -> None:
        slug_path = f"/game/{slug}".lower()
        for match in JSON_LD_PATTERN.finditer(page_html):
            blob = match.group(1).strip()
            try:
                data = json.loads(blob)
            except json.JSONDecodeError:
                continue

            items = data if isinstance(data, list) else [data]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("@type", "")).lower() != "videogame":
                    continue

                url = str(item.get("url") or "").lower()
                name = str(item.get("name") or "").strip()
                if slug_path not in url and slug.replace("-", " ").lower() not in name.lower():
                    continue

                if name:
                    details.title = html.unescape(name)

                desc = str(item.get("description") or "").strip()
                if desc:
                    details.description = html.unescape(desc)

                image = item.get("image")
                if isinstance(image, str) and image.startswith("http"):
                    details.hero_image = html.unescape(image)
                elif isinstance(image, list) and image:
                    first = str(image[0])
                    if first.startswith("http"):
                        details.hero_image = html.unescape(first)

                genres = item.get("genre") or []
                if isinstance(genres, list):
                    parsed_genres: list[str] = []
                    for g in genres:
                        if isinstance(g, str) and g.strip():
                            parsed_genres.append(html.unescape(g.strip()))
                        elif isinstance(g, dict) and g.get("title"):
                            parsed_genres.append(html.unescape(str(g["title"])))
                    if parsed_genres:
                        details.genres = parsed_genres

                shots = item.get("screenshot") or []
                if isinstance(shots, str):
                    shots = [shots]
                if isinstance(shots, list):
                    for shot_url in shots:
                        url = html.unescape(str(shot_url).strip())
                        if url.startswith("http"):
                            details.screenshots.append({"thumb": url, "full": url})

                actor = item.get("actor")
                if isinstance(actor, list) and actor and not details.developer:
                    first = actor[0]
                    if isinstance(first, dict):
                        details.developer = html.unescape(str(first.get("name") or "").strip())
                    elif isinstance(first, str):
                        details.developer = html.unescape(first)

                provider = item.get("provider")
                if isinstance(provider, dict) and not details.developer:
                    details.developer = html.unescape(
                        str(provider.get("name") or "").strip()
                    )

                published = str(item.get("datePublished") or "").strip()
                if published:
                    details.release_date = published

                file_size = str(item.get("fileSize") or "").strip()
                if file_size and not details.store_size_bytes:
                    parsed = self._parse_size_label(file_size)
                    if parsed:
                        details.store_size_bytes = parsed

                rating = item.get("aggregateRating")
                if isinstance(rating, dict):
                    try:
                        details.review_score = int(
                            float(str(rating.get("ratingValue") or 0))
                        )
                        details.review_count = int(rating.get("ratingCount") or 0)
                    except (TypeError, ValueError):
                        pass

                details.source = "store"
                return

    def _parse_visible_overview(self, page_html: str, details: GameDetails) -> None:
        paragraphs: list[str] = []
        for match in OVERVIEW_PARAGRAPH_RE.finditer(page_html):
            inner = match.group(1).strip()
            if inner:
                paragraphs.append(inner)

        if paragraphs:
            if not details.description:
                details.description = _strip_html(paragraphs[0], max_len=2000)
            combined = "".join(f"<p>{p}</p>" for p in paragraphs)
            details.description_html = _sanitize_description_html(combined)

        if not details.developer:
            pub = PUBLISHER_RE.search(page_html)
            if pub:
                details.developer = html.unescape(pub.group(1).strip())

        if not details.release_date:
            rel = RELEASED_DATE_RE.search(page_html)
            if rel:
                details.release_date = html.unescape(rel.group(1).strip())

    def _apply_listing_item(self, item: dict[str, Any], details: GameDetails) -> None:
        from anker.anker_api import normalize_image_url

        title = html.unescape(str(item.get("title") or "").strip())
        if title:
            details.title = title

        overview = html.unescape(str(item.get("overview") or "").strip())
        if overview:
            details.description = overview

        body = html.unescape(str(item.get("body") or "").strip())
        if body:
            details.description_html = _sanitize_description_html(body)
            plain = _strip_html(body, max_len=2000)
            if plain:
                details.description = plain

        image = str(item.get("imageurl") or item.get("coverurl") or "").strip()
        if image:
            details.hero_image = normalize_image_url(image)

        runtime = str(item.get("runtime") or item.get("size_gb") or "").strip()
        if runtime:
            size = self._parse_size_label(runtime)
            if size:
                details.store_size_bytes = size

        steam_raw = str(item.get("steam_app_id") or "").strip()
        if steam_raw.isdigit():
            details.steam_app_id = steam_raw

        dev = html.unescape(str(item.get("developer_name") or "").strip())
        if dev and dev.lower() != "steam":
            details.developer = dev

        release = str(item.get("release_date") or item.get("published_at") or "").strip()
        if release:
            details.release_date = release[:10]

        genres = item.get("genres") or []
        if isinstance(genres, list) and not details.genres:
            details.genres = [
                html.unescape(str(g.get("title", "")))
                for g in genres
                if isinstance(g, dict) and g.get("title")
            ]

        details.source = "store"

    @staticmethod
    def _parse_size_label(label: str) -> int | None:
        match = re.search(
            r"(\d+(?:\.\d+)?)\s*(tb|gb|mb|kb)",
            label,
            re.I,
        )
        if not match:
            return None
        amount = float(match.group(1))
        unit = match.group(2).upper()
        mult = {"TB": 1024**4, "GB": 1024**3, "MB": 1024**2, "KB": 1024}.get(unit)
        if not mult:
            return None
        return int(amount * mult)
