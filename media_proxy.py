"""Proxy Steam HLS manifests and segments (avoids browser CORS in WebView)."""

from __future__ import annotations

import re
from typing import Iterator
from urllib.parse import quote, urljoin, urlparse

import requests

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

_ALLOWED_HOSTS = (
    "video.akamai.steamstatic.com",
    "shared.akamai.steamstatic.com",
    "cdn.akamai.steamstatic.com",
    "steamcdn-a.akamaihd.net",
    "steamstatic.com",
)


def _is_allowed_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return any(host == allowed or host.endswith("." + allowed) for allowed in _ALLOWED_HOSTS)


def proxy_segment_url(url: str) -> str:
    return f"/api/media/segment?url={quote(url, safe='')}"


def rewrite_hls_manifest(manifest_text: str, base_url: str) -> str:
    lines: list[str] = []
    for line in manifest_text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            uri_m = re.search(r'URI="([^"]+)"', line)
            if uri_m:
                absolute = urljoin(base_url, uri_m.group(1))
                if _is_allowed_url(absolute):
                    replacement = (
                        f"/api/media/hls-manifest?url={quote(absolute, safe='')}"
                        if absolute.endswith(".m3u8") or ".m3u8?" in absolute
                        else proxy_segment_url(absolute)
                    )
                    line = line.replace(uri_m.group(1), replacement)
            lines.append(line)
            continue

        absolute = urljoin(base_url, stripped)
        if _is_allowed_url(absolute):
            if stripped.endswith(".m3u8") or ".m3u8?" in stripped or ".m3u8?" in absolute:
                lines.append(f"/api/media/hls-manifest?url={quote(absolute, safe='')}")
            else:
                lines.append(proxy_segment_url(absolute))
        else:
            lines.append(line)
    return "\n".join(lines) + "\n"


def fetch_hls_manifest(url: str, session: requests.Session | None = None) -> str:
    if not _is_allowed_url(url):
        raise ValueError("URL not allowed for media proxy")
    client = session or requests.Session()
    response = client.get(url, headers={"User-Agent": USER_AGENT}, timeout=30)
    response.raise_for_status()
    return rewrite_hls_manifest(response.text, url)


def stream_media(url: str, session: requests.Session | None = None) -> tuple[Iterator[bytes], str]:
    if not _is_allowed_url(url):
        raise ValueError("URL not allowed for media proxy")
    client = session or requests.Session()
    response = client.get(
        url,
        headers={"User-Agent": USER_AGENT},
        stream=True,
        timeout=60,
    )
    response.raise_for_status()
    content_type = response.headers.get("Content-Type", "application/octet-stream")

    def iterator() -> Iterator[bytes]:
        try:
            for chunk in response.iter_content(chunk_size=1024 * 256):
                if chunk:
                    yield chunk
        finally:
            response.close()

    return iterator(), content_type
