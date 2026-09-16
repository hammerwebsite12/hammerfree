"""Game store-style details — PlayZip page + Steam Store API fallback."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from download_logger import DownloadLogger
    from playzip_api import PlayZipClient

STEAM_APPDETAILS = "https://store.steampowered.com/api/appdetails"
STEAM_HERO = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_hero.jpg"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

STEAM_APP_RE = re.compile(r"steam/apps/(\d+)/", re.I)


@dataclass
class GameDetails:
    game_id: str
    title: str
    image_url: str = ""
    description: str = ""
    description_html: str = ""
    developer: str = ""
    release_date: str = ""
    hero_image: str = ""
    video_url: str = ""
    video_type: str = ""  # mp4 | webm | hls
    steam_app_id: str = ""
    genres: list[str] = field(default_factory=list)
    screenshots: list[dict[str, str]] = field(default_factory=list)
    review_score: int | None = None
    review_count: int | None = None
    store_size_bytes: int | None = None
    source: str = "store"  # store | steam | mixed


def steam_app_id_from_url(url: str) -> str:
    if not url:
        return ""
    match = STEAM_APP_RE.search(url)
    return match.group(1) if match else ""


def _strip_html(text: str, max_len: int = 1200) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) > max_len:
        return text[: max_len - 1].rstrip() + "…"
    return text


def _sanitize_description_html(raw: str) -> str:
    """Keep basic formatting tags for the detail panel."""
    if not raw:
        return ""
    raw = html.unescape(raw)
    raw = re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=re.I | re.S)
    raw = re.sub(r"<style[^>]*>.*?</style>", "", raw, flags=re.I | re.S)
    allowed = ("p", "br", "strong", "b", "em", "i", "h3", "h4", "ul", "ol", "li")
    # Simple pass: remove disallowed tags
    raw = re.sub(
        r"</?(?!(" + "|".join(allowed) + r")\b)[^>]+>",
        "",
        raw,
        flags=re.I,
    )
    return raw.strip()


class GameDetailsService:
    def __init__(self, client: PlayZipClient, logger: DownloadLogger | None = None) -> None:
        self.client = client
        self.session = client.session
        self.logger = logger

    def _log(self, msg: str, level: str = "INFO") -> None:
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(msg)

    def fetch(
        self,
        game_id: str,
        title: str = "",
        image_url: str = "",
    ) -> GameDetails:
        from playzip_api import normalize_image_url

        self._log(f"Loading game details for game {game_id}")

        details = GameDetails(
            game_id=game_id,
            title=title,
            image_url=normalize_image_url(image_url),
            hero_image=normalize_image_url(image_url),
        )

        page_html = self.client.fetch_page(f"/game/{game_id}")
        if page_html:
            self._parse_playzip_page(page_html, details)

        steam_id = details.steam_app_id or steam_app_id_from_url(details.image_url)
        if not steam_id:
            steam_id = steam_app_id_from_url(details.hero_image)

        if steam_id:
            details.steam_app_id = steam_id
            steam = self._fetch_steam(steam_id)
            if steam:
                self._merge_steam(details, steam)
        elif not details.description:
            meta = self._meta_description_only(details)
            if meta:
                details.description = meta

        if not details.title and title:
            details.title = title
        if not details.hero_image and image_url:
            details.hero_image = normalize_image_url(image_url)

        return details

    def _meta_description_only(self, details: GameDetails) -> str:
        return details.description

    def _parse_playzip_page(self, page_html: str, details: GameDetails) -> None:
        title_m = re.search(
            r'<div class="content_title"[^>]*>\s*(.*?)\s*</div>',
            page_html,
            re.S,
        )
        if title_m:
            raw_title = re.sub(r"<[^>]+>", "", title_m.group(1)).strip()
            raw_title = re.sub(r"\s+Free Download\s*$", "", raw_title, flags=re.I).strip()
            if raw_title:
                details.title = html.unescape(raw_title)

        meta_m = re.search(
            r'<meta name="description" content="([^"]*)"',
            page_html,
            re.I,
        )
        if meta_m:
            details.description = html.unescape(meta_m.group(1)).strip()

        body_m = re.search(
            r'<div class="content_body"[^>]*>(.*?)</div>\s*<div class="content_right"',
            page_html,
            re.S,
        )
        if body_m:
            body_html = body_m.group(1).strip()
            details.description_html = _sanitize_description_html(body_html)
            plain = _strip_html(body_html, max_len=2000)
            if plain:
                details.description = plain

        cap_m = re.search(
            r'class="capsule_div"[^>]*>.*?<img[^>]+src="([^"]+)"',
            page_html,
            re.S | re.I,
        )
        if cap_m:
            cap_url = html.unescape(cap_m.group(1).strip())
            details.hero_image = cap_url
            sid = steam_app_id_from_url(cap_url)
            if sid:
                details.steam_app_id = sid

        # Images embedded in playzip article (non-steam games)
        if not details.screenshots:
            for img_url in re.findall(r'<img[^>]+src="([^"]+)"', body_m.group(1) if body_m else ""):
                img_url = html.unescape(img_url.strip())
                if img_url.startswith("http") and "avatar" not in img_url.lower():
                    details.screenshots.append(
                        {"thumb": img_url, "full": img_url}
                    )

        details.source = "store"

    def _fetch_steam(self, app_id: str) -> dict[str, Any] | None:
        try:
            response = self.session.get(
                STEAM_APPDETAILS,
                params={"appids": app_id, "l": "en", "cc": "us"},
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=25,
            )
            response.raise_for_status()
            wrapper = response.json().get(str(app_id), {})
            if not wrapper.get("success"):
                return None
            return wrapper.get("data") or {}
        except (requests.RequestException, ValueError) as exc:
            self._log(f"Steam appdetails failed for {app_id}: {exc}", "WARN")
            return None

    def _merge_steam(self, details: GameDetails, data: dict[str, Any]) -> None:
        name = (data.get("name") or "").strip()
        if name:
            details.title = name

        devs = data.get("developers") or []
        if devs:
            details.developer = str(devs[0])

        short = (data.get("short_description") or "").strip()
        if short:
            details.description = short

        release = data.get("release_date") or {}
        if isinstance(release, dict):
            details.release_date = (release.get("date") or "").strip()

        genres = data.get("genres") or []
        details.genres = [
            g.get("description", "")
            for g in genres
            if isinstance(g, dict) and g.get("description")
        ]

        reviews = data.get("reviews") or {}
        if isinstance(reviews, dict):
            total = int(reviews.get("total_reviews") or 0)
            positive = int(reviews.get("total_positive") or 0)
            if total > 0 and positive >= 0:
                details.review_count = total
                details.review_score = int(positive / total * 100)

        app_id = details.steam_app_id
        details.hero_image = STEAM_HERO.format(app_id=app_id)

        movies = data.get("movies") or []
        details.video_url, details.video_type = self._pick_trailer(movies)

        shots: list[dict[str, str]] = []
        for shot in data.get("screenshots") or []:
            if not isinstance(shot, dict):
                continue
            full = (shot.get("path_full") or "").strip()
            if not full:
                continue
            thumb = (shot.get("path_thumbnail") or full).strip()
            shots.append({"thumb": thumb, "full": full})
        if shots:
            details.screenshots = shots

        details.store_size_bytes = self._parse_storage_bytes(data.get("pc_requirements"))

        details.source = "mixed" if details.description_html else "steam"

    def _parse_storage_bytes(self, pc_requirements: Any) -> int | None:
        if not isinstance(pc_requirements, dict):
            return None
        pattern = re.compile(
            r"(?i)storage[^0-9]*(\d+(?:\.\d+)?)\s*(tb|gb|mb|kb)",
            re.DOTALL,
        )
        sizes: list[int] = []
        for key in ("minimum", "recommended"):
            html_text = pc_requirements.get(key) or ""
            if not isinstance(html_text, str):
                continue
            clean = re.sub(r"<[^>]+>", " ", html_text)
            match = pattern.search(clean)
            if not match:
                continue
            amount = float(match.group(1))
            unit = match.group(2).upper()
            mult = {"TB": 1024**4, "GB": 1024**3, "MB": 1024**2, "KB": 1024}.get(unit)
            if mult:
                sizes.append(int(amount * mult))
        return max(sizes) if sizes else None

    def _pick_trailer(self, movies: list[dict[str, Any]]) -> tuple[str, str]:
        if not movies:
            return "", ""
        chosen = None
        for movie in movies:
            if movie.get("highlight"):
                chosen = movie
                break
        if chosen is None:
            chosen = movies[0]

        mp4 = chosen.get("mp4") or {}
        if isinstance(mp4, dict):
            for key in ("max", "480"):
                url = (mp4.get(key) or "").strip()
                if url:
                    return url, "mp4"

        webm = chosen.get("webm") or {}
        if isinstance(webm, dict):
            for key in ("max", "480"):
                url = (webm.get(key) or "").strip()
                if url:
                    return url, "webm"

        for key in ("hls_h264", "dash_h264", "dash_av1"):
            url = (chosen.get(key) or "").strip()
            if url:
                return url, "hls"
        return "", ""
