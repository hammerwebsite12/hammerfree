"""Fetch startup announcement text from GitHub notify.txt (Hammer-style)."""

from __future__ import annotations

import base64
import json

import requests

PRIMARY_NOTIFY_FILE = (
    "https://raw.githubusercontent.com/dvahana2424-web/sojorepo/main/notify.txt"
)
SECONDARY_NOTIFY_FILE = (
    "https://raw.githubusercontent.com/hammerwebsite12/sojorepo/main/notify.txt"
)
PRIMARY_GITHUB_API = (
    "https://api.github.com/repos/dvahana2424-web/sojorepo/contents/notify.txt"
)
SECONDARY_GITHUB_API = (
    "https://api.github.com/repos/hammerwebsite12/sojorepo/contents/notify.txt"
)
USER_AGENT = "QuickPlayDesktop/1.0"


def _decode_github_api_content(payload: dict) -> str | None:
    encoded = payload.get("content")
    if not encoded:
        return None
    try:
        cleaned = str(encoded).replace("\n", "").replace(" ", "")
        raw = base64.b64decode(cleaned)
        text = raw.decode("utf-8").strip()
        return text or None
    except (ValueError, UnicodeDecodeError):
        return None


def _fetch_via_github_api(api_url: str) -> str | None:
    try:
        response = requests.get(
            api_url,
            timeout=10,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/vnd.github+json",
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        if response.status_code != 200:
            return None
        payload = response.json()
        if isinstance(payload, dict):
            return _decode_github_api_content(payload)
    except (requests.RequestException, json.JSONDecodeError, TypeError, ValueError):
        pass
    return None


def _fetch_via_raw(file_url: str) -> str | None:
    try:
        response = requests.get(
            file_url,
            timeout=10,
            headers={
                "User-Agent": USER_AGENT,
                "Cache-Control": "no-cache",
                "Pragma": "no-cache",
            },
        )
        if response.status_code == 200:
            text = response.text.strip()
            return text or None
    except requests.RequestException:
        pass
    return None


def fetch_announcement() -> str | None:
    """Return announcement text from notify.txt, or None if unavailable/empty."""
    attempts = (
        lambda: _fetch_via_github_api(PRIMARY_GITHUB_API),
        lambda: _fetch_via_raw(PRIMARY_NOTIFY_FILE),
        lambda: _fetch_via_github_api(SECONDARY_GITHUB_API),
        lambda: _fetch_via_raw(SECONDARY_NOTIFY_FILE),
    )
    for attempt in attempts:
        text = attempt()
        if text:
            return text
    return None
