"""Strip source URLs and internal hostnames from user-visible log lines."""

from __future__ import annotations

import re

_SENSITIVE_HOSTS = (
    r"playzip\.com",
    r"playzip\.net",
    r"playzip\.to",
    r"koyso\.com",
    r"koyso\.to",
    r"koyso\.net",
)

_ANKER_BRAND_RE = re.compile(r"ankergames(?:\.net)?", re.I)
_STORE_FILENAME_SUFFIX = re.compile(r"-AnkerGames(?=\.[^.\s]+)", re.I)
_HOST_RE = re.compile("|".join(_SENSITIVE_HOSTS), re.I)
_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.I)
_API_PATH_RE = re.compile(r"/api/\w+", re.I)

_anker_sanitize_enabled = False


def set_anker_sanitize(enabled: bool) -> None:
    """When True, also strip Anker branding from logs (Server 2 only)."""
    global _anker_sanitize_enabled
    _anker_sanitize_enabled = bool(enabled)


def sanitize_log_message(message: str) -> str:
    if not message:
        return message

    text = message
    if _anker_sanitize_enabled:
        text = _STORE_FILENAME_SUFFIX.sub("", text)
        text = _ANKER_BRAND_RE.sub("store", text)
    text = _URL_RE.sub("[link]", text)
    text = _HOST_RE.sub("store", text)
    text = _API_PATH_RE.sub("download service", text)

    replacements = {
        "POST download service": "Requesting download link",
        "Game details: [link]": "Loading game details",
        "Browse request:": "Loading games:",
        "Search request:": "Searching:",
        "Warming up download page": "Preparing download",
        "Download page status:": "Download page:",
        "from PlayZip": "from store",
        "PlayZip": "store",
        "playzip": "store",
        "Anker gate": "download gate",
        "Anker download": "download",
        "Resolving Anker download link": "Resolving download link",
        "Resolving CDN link from Anker gate page": "Resolving download link",
        "Resolving CDN link (background)": "Resolving download link",
    }
    if _anker_sanitize_enabled:
        replacements.update(
            {
                "Browse Anker:": "Loading games:",
                "Search Anker:": "Searching:",
                "AnkerGames": "store",
            }
        )
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r" +", " ", text).strip()
    return text
