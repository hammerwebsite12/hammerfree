"""Anker catalog hosts — primary ankergames.to, legacy ankergames.net fallback."""

from __future__ import annotations

import requests

_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

PRIMARY_BASE_URL = "https://ankergames.to"
LEGACY_BASE_URL = "https://ankergames.net"

ANKER_STORE_HOSTS = frozenset(
    {
        "ankergames.to",
        "www.ankergames.to",
        "ankergames.net",
        "www.ankergames.net",
    }
)


def is_anker_store_host(host: str) -> bool:
    return (host or "").lower() in ANKER_STORE_HOSTS


def base_url_candidates() -> tuple[str, ...]:
    primary = PRIMARY_BASE_URL
    fallbacks: tuple[str, ...] = (LEGACY_BASE_URL,)
    try:
        from anker.config import BASE_URL, FALLBACK_BASE_URLS

        if BASE_URL:
            primary = str(BASE_URL).rstrip("/")
        if FALLBACK_BASE_URLS:
            fallbacks = tuple(str(u).rstrip("/") for u in FALLBACK_BASE_URLS if u)
    except ImportError:
        try:
            from anker.config import BASE_URL

            if BASE_URL:
                primary = str(BASE_URL).rstrip("/")
        except ImportError:
            pass

    ordered: list[str] = []
    seen: set[str] = set()
    for url in (primary, *fallbacks):
        if not url or url in seen:
            continue
        seen.add(url)
        ordered.append(url)
    return tuple(ordered) or (PRIMARY_BASE_URL, LEGACY_BASE_URL)


def pick_reachable_base_url(timeout: float = 12.0) -> str:
    """First candidate that serves the browse catalog; else config primary."""
    headers = {"User-Agent": _USER_AGENT, "Accept": "text/html"}
    for base in base_url_candidates():
        try:
            resp = requests.get(f"{base}/games", headers=headers, timeout=timeout)
            if resp.status_code < 500 and "uiPostCard" in resp.text:
                return base
        except requests.RequestException:
            continue
    return base_url_candidates()[0]
