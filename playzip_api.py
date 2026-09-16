"""Store API client — browse, search, and download URL resolver with mirror failover."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import math
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Callable
from urllib.parse import unquote, urlparse

import requests

if TYPE_CHECKING:
    from download_logger import DownloadLogger

RateLimitCallback = Callable[[int, str], None]
ResolveStatusCallback = Callable[[str, str], None]
SnapshotRecordedCallback = Callable[[], None]

MIRROR_SITES = (
    "https://playzip.com",
    "https://playzip.net",
    "https://playzip.to",
)
RATE_LIMIT_SECONDS = 125

# The download-link signing secret lives server-side in a Cloudflare Worker.
# The client only holds credentials to authenticate to that Worker — never the
# upstream hashing secret. See client_secrets.py (git-ignored).
try:
    from client_secrets import APP_TOKEN, SIGNING_SECRET, WORKER_URL
except ImportError:  # pragma: no cover - template fallback
    WORKER_URL = ""
    APP_TOKEN = ""
    SIGNING_SECRET = ""

from license_manager import LicenseRequiredError, get_formatted_hwid
from device_fingerprint import get_device_fingerprint
from hardware_snapshot import get_hardware_snapshot_text

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

GAME_ITEM_PATTERN = re.compile(
    r'<a class="game_item" href="/game/(\d+)">.*?'
    r'data-src="([^"]+)".*?'
    r'<span[^>]*>(.*?)</span>',
    re.S,
)

CATEGORIES: list[dict[str, str]] = [
    {"id": "all", "label": "All"},
    {"id": "shooting", "label": "Shooter"},
    {"id": "action", "label": "Action"},
    {"id": "sports_racing", "label": "Racing"},
    {"id": "r18", "label": "R18+"},
    {"id": "adventure", "label": "Adventure"},
    {"id": "rpg", "label": "RPG"},
    {"id": "horror", "label": "Horror"},
    {"id": "simulation", "label": "Simulation"},
    {"id": "strategy", "label": "Strategy"},
    {"id": "fighting", "label": "Fighting"},
    {"id": "casual", "label": "Casual"},
    {"id": "indie", "label": "Indie"},
    {"id": "card", "label": "Card"},
    {"id": "rts", "label": "RTS"},
    {"id": "lan", "label": "LAN"},
]


def normalize_image_url(url: str) -> str:
    url = html.unescape((url or "").strip())
    if url.startswith("//"):
        url = "https:" + url
    return url


@dataclass
class GameResult:
    game_id: str
    title: str
    image_url: str = ""


class PlayZipClient:
    def __init__(
        self,
        logger: DownloadLogger | None = None,
        on_rate_limit: RateLimitCallback | None = None,
        on_resolve_status: ResolveStatusCallback | None = None,
        hardware_snapshot_submitted: bool = False,
        on_snapshot_recorded: SnapshotRecordedCallback | None = None,
    ) -> None:
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept-Language": "en-US,en;q=0.9",
            }
        )
        self.logger = logger
        self._on_rate_limit = on_rate_limit
        self._on_resolve_status = on_resolve_status
        self._hardware_snapshot_submitted = hardware_snapshot_submitted
        self._on_snapshot_recorded = on_snapshot_recorded
        self._last_download_at = 0.0
        self._base_url = MIRROR_SITES[0]

    @property
    def base_url(self) -> str:
        return self._base_url

    def _log(self, message: str, level: str = "INFO") -> None:
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(message)

    def _notify_resolve_status(self, label: str, message: str) -> None:
        if self._on_resolve_status:
            self._on_resolve_status(label, message)

    def _switch_mirror(self, base_url: str) -> None:
        self._base_url = base_url

    def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float | tuple[float, float] = 30,
        **kwargs,
    ) -> requests.Response:
        last_error: Exception | None = None
        for mirror in MIRROR_SITES:
            url = f"{mirror}{path}"
            self._switch_mirror(mirror)
            try:
                response = self.session.request(method, url, timeout=timeout, **kwargs)
            except requests.RequestException as exc:
                last_error = exc
                self._log("Primary store unavailable, trying mirror...", "WARN")
                self._notify_resolve_status("", "Store unreachable — trying mirror...")
                continue

            if response.status_code >= 500:
                last_error = RuntimeError(f"Server error {response.status_code}")
                self._log("Store mirror busy, trying next...", "WARN")
                self._notify_resolve_status("", "Store busy — trying mirror...")
                continue

            return response

        if last_error:
            raise RuntimeError(
                "Hindi maabot ang store servers. Subukan ulit mamaya."
            ) from last_error
        raise RuntimeError("Hindi maabot ang store servers. Subukan ulit mamaya.")

    def _parse_games(self, page_html: str) -> list[GameResult]:
        results: list[GameResult] = []
        seen: set[str] = set()
        for game_id, image_url, raw_title in GAME_ITEM_PATTERN.findall(page_html):
            if game_id in seen:
                continue
            seen.add(game_id)
            title = html.unescape(re.sub(r"<[^>]+>", "", raw_title).strip())
            results.append(
                GameResult(
                    game_id=game_id,
                    title=title,
                    image_url=normalize_image_url(image_url),
                )
            )
        return results

    def browse(
        self,
        page: int = 1,
        sort: str = "views",
        category: str = "all",
    ) -> list[GameResult]:
        params: dict[str, str | int] = {}
        if page > 1:
            params["page"] = page
        if sort == "latest":
            params["sort"] = "latest"

        category = (category or "all").strip().lower()
        if category and category != "all":
            path = f"/category/{category}"
            self._log(f"Browse: category={category}, page={page}, sort={sort}")
        else:
            path = "/"
            self._log(f"Browse: page={page}, sort={sort}")

        response = self._request("GET", path, params=params)
        response.raise_for_status()
        games = self._parse_games(response.text)
        self._log(f"Browse OK: {len(games)} games")
        return games

    def search(self, query: str) -> list[GameResult]:
        query = query.strip()
        if not query:
            return []

        self._log(f"Search: keywords={query!r}")
        response = self._request("GET", "/", params={"keywords": query})
        response.raise_for_status()
        games = self._parse_games(response.text)
        self._log(f"Search OK: {len(games)} result(s)")
        return games

    def fetch_page(self, path: str, *, timeout: float = 30) -> str | None:
        try:
            response = self._request("GET", path, timeout=timeout)
            if response.status_code == 200:
                return response.text
        except (requests.RequestException, RuntimeError) as exc:
            self._log(f"Page fetch failed: {exc}", "WARN")
        return None

    def _warmup_download_page(self, game_id: str, cookie: str = "") -> None:
        self._log(f"Preparing download for game {game_id}")
        headers = {"Referer": f"{self._base_url}/game/{game_id}"}
        if cookie:
            headers["Cookie"] = cookie
        response = self._request(
            "GET",
            f"/download/{game_id}",
            timeout=20,
            headers=headers,
        )
        self._log(f"Download page status: {response.status_code}")

    def _sign_via_worker(self, game_id: str, label: str) -> tuple[str, str, str]:
        """Ask the Worker to sign this download.

        Returns (timestamp, digest, cookie). The upstream hashing secret stays
        on the server; we only receive a short-lived digest bound to this game.
        """
        if not WORKER_URL or not APP_TOKEN or not SIGNING_SECRET:
            raise RuntimeError("Download signer not configured.")

        self._notify_resolve_status(label, "Contacting download signer...")
        hwid = get_formatted_hwid().lower()
        device_fp = get_device_fingerprint()
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        sig = hmac.new(
            SIGNING_SECRET.encode(),
            f"{ts}.{nonce}.{game_id}.{hwid}.{device_fp}".encode(),
            hashlib.sha256,
        ).hexdigest()

        payload: dict[str, str] = {
            "game_id": game_id,
            "hwid": hwid,
            "device_fp": device_fp,
            "ts": ts,
            "nonce": nonce,
            "sig": sig,
            "token": APP_TOKEN,
        }
        if not self._hardware_snapshot_submitted:
            snapshot = get_hardware_snapshot_text()
            if snapshot:
                payload["hw_snapshot"] = snapshot

        try:
            resp = self.session.post(
                f"{WORKER_URL}/sign",
                json=payload,
                timeout=(10, 30),
            )
        except requests.RequestException as exc:
            self._log(f"[{label}] Signer unreachable: {exc}", "ERROR")
            raise RuntimeError("Signer unreachable. Subukan ulit.") from exc

        if resp.status_code == 403:
            try:
                data = resp.json()
            except json.JSONDecodeError:
                data = {}
            if data.get("error") == "license_required":
                raise LicenseRequiredError(
                    "This device is not activated. Activate QuickPlay before downloading.",
                    custom_os=bool(data.get("custom_os")),
                )
        if resp.status_code == 429:
            raise RuntimeError(
                "RATE_LIMIT:60:Masyadong mabilis ang mga request. Sandali lang."
            )
        if resp.status_code == 401:
            self._log(f"[{label}] Signer rejected request (401)", "ERROR")
            raise RuntimeError(
                "401: Baka mali ang oras ng PC mo. I-sync ang system clock."
            )
        if resp.status_code != 200:
            self._log(f"[{label}] Signer error {resp.status_code}", "ERROR")
            raise RuntimeError("Hindi makakuha ng download signature. Subukan ulit.")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid signer response. Subukan ulit.") from exc

        if data.get("snapshot_recorded") and self._on_snapshot_recorded:
            self._hardware_snapshot_submitted = True
            self._on_snapshot_recorded()

        return str(data["timestamp"]), str(data["digest"]), str(data.get("cookie", ""))

    def _notify_rate_limit(self, seconds: int, label: str) -> None:
        if self._on_rate_limit:
            self._on_rate_limit(seconds, label)

    def _wait_for_rate_limit(self, game_id: str, label: str) -> None:
        elapsed = time.time() - self._last_download_at
        if elapsed >= RATE_LIMIT_SECONDS:
            return

        total_wait = int(math.ceil(RATE_LIMIT_SECONDS - elapsed))
        self._log(
            f"[{label}] Rate limit: wait {total_wait}s before next download",
            "WARN",
        )
        remaining = total_wait
        while remaining > 0:
            self._notify_rate_limit(remaining, label)
            step = min(1, remaining)
            time.sleep(step)
            remaining -= step
        self._notify_rate_limit(0, label)

    def rate_limit_remaining(self) -> int:
        elapsed = time.time() - self._last_download_at
        if elapsed >= RATE_LIMIT_SECONDS:
            return 0
        return int(math.ceil(RATE_LIMIT_SECONDS - elapsed))

    def get_download_url(self, game_id: str, title: str = "") -> str:
        label = title or f"game/{game_id}"
        self._log(f"[{label}] Resolving download link (id={game_id})")
        self._notify_resolve_status(label, "Starting link resolve...")
        self._wait_for_rate_limit(game_id, label)

        # The signing secret is server-side; the Worker returns a time-bound
        # digest that this (residential) client uses to call the upstream API.
        timestamp, digest, cookie = self._sign_via_worker(game_id, label)

        self._notify_resolve_status(label, "Preparing download page...")
        self._warmup_download_page(game_id, cookie)
        self._notify_resolve_status(label, "Requesting download link from store...")
        self._log(f"[{label}] Requesting download link")

        headers = {"Referer": f"{self._base_url}/download/{game_id}"}
        if cookie:
            headers["Cookie"] = cookie

        started = time.time()
        try:
            response = self._request(
                "POST",
                "/api/getGamesDownloadUrl",
                data={
                    "id": game_id,
                    "timestamp": timestamp,
                    "secretKey": digest,
                    "canvasId": "0",
                },
                headers=headers,
                timeout=(10, 90),
            )
        except requests.Timeout as exc:
            elapsed = time.time() - started
            self._log(f"[{label}] API timeout after {elapsed:.1f}s", "ERROR")
            raise RuntimeError(
                f"Timeout habang kumukuha ng download link ({elapsed:.0f}s). Subukan ulit."
            ) from exc
        except requests.RequestException as exc:
            self._log(f"[{label}] Network error: {exc}", "ERROR")
            raise RuntimeError("Network error. Subukan ulit.") from exc

        elapsed = time.time() - started
        self._log(
            f"[{label}] Download API {response.status_code} in {elapsed:.2f}s"
        )

        if response.status_code == 403:
            raise RuntimeError(
                "403: Mali ang oras ng PC mo. I-sync ang system clock."
            )

        if response.status_code == 429:
            return self._handle_rate_limit_response(game_id, label, response)

        response.raise_for_status()
        url = response.text.strip().strip('"')
        if not url.startswith("http"):
            self._log(f"[{label}] Invalid download response", "ERROR")
            raise RuntimeError("Hindi valid ang download link. Subukan ulit.")

        self._last_download_at = time.time()
        self._log(f"[{label}] Download link ready")
        self._notify_resolve_status(label, "Download link ready — starting...")
        return url

    def _handle_rate_limit_response(
        self,
        game_id: str,
        label: str,
        response: requests.Response,
    ) -> str:
        try:
            payload = response.json()
        except json.JSONDecodeError:
            self._log(f"[{label}] Rate limit (non-JSON response)", "ERROR")
            raise RuntimeError(
                f"Download limit — hintayin ang {RATE_LIMIT_SECONDS} segundo bago subukan ulit."
            )

        returned_id = str(payload.get("gameId", ""))
        returned_url = payload.get("downloadUrl", "")

        self._log(f"[{label}] Rate limit response for game #{returned_id}", "WARN")

        if returned_id == game_id and returned_url.startswith("http"):
            self._log(f"[{label}] Using cached download link")
            self._last_download_at = time.time()
            return returned_url

        wait_seconds = self.rate_limit_remaining() or RATE_LIMIT_SECONDS
        raise RuntimeError(
            f"RATE_LIMIT:{wait_seconds}:May download limit (~{RATE_LIMIT_SECONDS}s bawat game). "
            f"Hintayin {wait_seconds} segundo bago i-download ang '{label}'."
        )

    def fetch_image_bytes(self, url: str) -> bytes:
        if not url:
            return b""
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.content

    @staticmethod
    def filename_from_url(url: str, fallback: str = "download.bin") -> str:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1]) if path else fallback
        return name or fallback
