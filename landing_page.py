"""Resolve the Hammer Steam landing page URL (same sources as Hammer desktop app)."""

from __future__ import annotations

import re
import time

import requests

DEFAULT_LANDING_URL = "https://www.facebook.com/profile.php?id=61566938626245"
PRIMARY_LANDING_FILE = (
    "https://raw.githubusercontent.com/dvahana2424-web/sojorepo/main/landingpagehammer.file"
)
SECONDARY_LANDING_FILE = (
    "https://raw.githubusercontent.com/hammerwebsite12/sojorepo/main/landingpagehammer.file"
)
CACHE_TTL_SECONDS = 30 * 60
USER_AGENT = "QuickPlayDesktop/1.0"

_URL_RE = re.compile(r"https?://[^\s\"']+")

_cached_url = DEFAULT_LANDING_URL
_cached_at = 0.0


def _extract_url(text: str) -> str | None:
    match = _URL_RE.search((text or "").strip())
    if not match:
        return None
    url = match.group(0).strip()
    if url.startswith(("http://", "https://")):
        return url
    return None


def _fetch_landing_from_file(file_url: str) -> str | None:
    try:
        response = requests.get(
            file_url,
            timeout=15,
            headers={
                "User-Agent": USER_AGENT,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        if response.status_code == 200:
            return _extract_url(response.text)
    except requests.RequestException:
        pass
    return None


def resolve_landing_page_url() -> str:
    """Return cached landing URL, refreshing from GitHub files when stale."""
    global _cached_url, _cached_at

    now = time.time()
    if now - _cached_at < CACHE_TTL_SECONDS:
        return _cached_url

    for file_url in (PRIMARY_LANDING_FILE, SECONDARY_LANDING_FILE):
        found = _fetch_landing_from_file(file_url)
        if found:
            _cached_url = found
            _cached_at = now
            return _cached_url

    _cached_url = DEFAULT_LANDING_URL
    _cached_at = now
    return _cached_url
