"""Game details for Server 3 game pages + Steam API fallback."""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import requests

if TYPE_CHECKING:
    from astral.astral_api import AstralGamesClient, normalize_poster_url
    from download_logger import DownloadLogger

STEAM_APPDETAILS = "https://store.steampowered.com/api/appdetails"
STEAM_HERO = "https://shared.akamai.steamstatic.com/store_item_assets/steam/apps/{app_id}/library_hero.jpg"
STEAM_STORE_APP_RE = re.compile(r"store\.steampowered\.com/app/(\d+)", re.I)
DESCRIPTION_RE = re.compile(r'"description":"((?:\\.|[^"\\])*)"', re.S)
NAME_RE = re.compile(r'"name":"((?:\\.|[^"\\])*)"', re.S)
IMAGE_RE = re.compile(r'"image":"(https://[^"]+)"')


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
    video_type: str = ""
    steam_app_id: str = ""
    genres: list[str] = field(default_factory=list)
    screenshots: list[str] = field(default_factory=list)
    requirements: dict[str, Any] = field(default_factory=dict)


class AstralGameDetailsService:
    def __init__(self, client: AstralGamesClient, logger: DownloadLogger) -> None:
        self._client = client
        self._logger = logger

    def fetch(
        self,
        game_id: str,
        title: str = "",
        image_url: str = "",
    ) -> GameDetails:
        slug = (game_id or "").strip().strip("/")
        page = self._client.fetch_page(f"/game/{quote_slug(slug)}")
        details = GameDetails(game_id=slug, title=title or slug)
        if not page:
            return details

        if title:
            details.title = title
        else:
            m = NAME_RE.search(page)
            if m:
                details.title = _unescape_json(m.group(1))

        img = IMAGE_RE.search(page)
        if img:
            poster = normalize_poster_url(img.group(1))
            details.image_url = poster
            details.hero_image = poster

        desc = DESCRIPTION_RE.search(page)
        if desc:
            raw = _unescape_json(desc.group(1))
            details.description = html.unescape(raw)
            details.description_html = details.description

        for pattern in (STEAM_STORE_APP_RE,):
            m = pattern.search(page)
            if m:
                details.steam_app_id = m.group(1)
                break

        if details.steam_app_id:
            self._merge_steam(details)

        return details

    def _merge_steam(self, details: GameDetails) -> None:
        app_id = details.steam_app_id
        try:
            resp = requests.get(
                STEAM_APPDETAILS,
                params={"appids": app_id, "l": "english"},
                timeout=20,
            )
            data = resp.json().get(app_id, {})
            if not data.get("success"):
                return
            info = data.get("data") or {}
            if not details.description:
                details.description = info.get("short_description") or ""
            details.developer = ", ".join(info.get("developers") or [])
            details.release_date = (info.get("release_date") or {}).get("date") or ""
            details.genres = [g.get("description", "") for g in info.get("genres") or []]
            if app_id:
                details.hero_image = STEAM_HERO.format(app_id=app_id)
        except (requests.RequestException, ValueError, AttributeError):
            pass


def quote_slug(slug: str) -> str:
    from urllib.parse import quote

    return quote(slug, safe="")


def _unescape_json(raw: str) -> str:
    import json

    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace("\\n", "\n").replace('\\"', '"')
