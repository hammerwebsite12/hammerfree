"""AstralGames store client — browse, search, licensed download via astral-dlresolver."""

from __future__ import annotations

import hashlib
import hmac
import html
import json
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote, unquote, urlparse

import requests

from http_catalog import CATALOG_TIMEOUT, is_transient_request_error, renew_session

if TYPE_CHECKING:
    from download_logger import DownloadLogger

RateLimitCallback = Callable[[int, str], None]
ResolveStatusCallback = Callable[[str, str], None]


def _worker_settings() -> tuple[str, str, str]:
    """Server 3 uses anker-dlresolver (same HWID/PAT as Server 2) with store=server3."""
    try:
        import client_secrets as cs

        url = (getattr(cs, "ANKER_DL_WORKER_URL", None) or "").strip()
        if not url:
            url = (getattr(cs, "ASTRAL_DL_WORKER_URL", None) or "").strip()
        token = (getattr(cs, "APP_TOKEN", None) or "").strip()
        secret = (getattr(cs, "SIGNING_SECRET", None) or "").strip()
        if url and token and secret:
            return url, token, secret
    except ImportError:
        pass
    g = globals()
    url = (g.get("ANKER_DL_WORKER_URL") or g.get("ASTRAL_DL_WORKER_URL") or "").strip()
    return (
        url,
        (g.get("APP_TOKEN") or "").strip(),
        (g.get("SIGNING_SECRET") or "").strip(),
    )


try:
    from astral.config import BASE_URL, DOWNLOAD_COOLDOWN_SECONDS
except ImportError:
    BASE_URL = "https://astralgames.net"
    DOWNLOAD_COOLDOWN_SECONDS = 0

from device_fingerprint import get_device_fingerprint
from hardware_snapshot import get_hardware_snapshot_text
from license_manager import LicenseRequiredError, get_formatted_hwid

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

CATALOG_ENTRY_RE = re.compile(
    r'\\"name\\":\\"((?:\\\\.|[^"\\])*)\\",\\"slug\\":\\"([^\\"]+)\\",\\"image\\":\\"([^\\"]+)\\"'
    r'[^}]*?\\"tags\\":\[(.*?)\]',
    re.S,
)
ARCHIVE_EXTENSIONS = (".zip", ".7z", ".rar", ".tar", ".gz", ".001")
MAX_FILENAME_LEN = 180
_INVALID_WIN_FILENAME = re.compile(r'[<>:"/\\|?*]')

CATEGORIES: list[dict[str, str]] = [
    {"id": "all", "label": "All"},
    {"id": "action", "label": "Action"},
    {"id": "adventure", "label": "Adventure"},
    {"id": "indie", "label": "Indie"},
    {"id": "rpg", "label": "RPG"},
    {"id": "strategy", "label": "Strategy"},
    {"id": "simulation", "label": "Simulation"},
    {"id": "horror", "label": "Horror"},
    {"id": "racing", "label": "Racing"},
    {"id": "multiplayer", "label": "Multiplayer"},
]


def _sanitize_filename(name: str) -> str:
    cleaned = html.unescape((name or "").strip())
    cleaned = _INVALID_WIN_FILENAME.sub("_", cleaned)
    cleaned = cleaned.rstrip(". ")
    if len(cleaned) > MAX_FILENAME_LEN:
        root, ext = os.path.splitext(cleaned)
        cleaned = root[: max(1, MAX_FILENAME_LEN - len(ext))] + ext
    return cleaned or "download"


def _consumer_archive_name(game_id: str, ext: str = ".zip") -> str:
    slug = re.sub(r"[^a-z0-9]+", "", (game_id or "").lower())
    slug = slug or "download"
    ext = ext if ext in ARCHIVE_EXTENSIONS else ".zip"
    return _sanitize_filename(slug + ext)


def _decode_json_name(raw: str) -> str:
    try:
        return json.loads(f'"{raw}"')
    except json.JSONDecodeError:
        return raw.replace("\\\"", '"').replace("\\\\", "\\")


_BONKER_HEADER_RE = re.compile(
    r"(https://api\.bonker\.dev/api/image-cache/app_\d+)_header\.jpg",
    re.I,
)


def normalize_poster_url(url: str) -> str:
    """Astral embeds wide Steam headers; grid cards need portrait capsules."""
    image = html.unescape((url or "").strip())
    if not image:
        return ""
    if image.startswith("//"):
        image = "https:" + image
    match = _BONKER_HEADER_RE.search(image)
    if match:
        return f"{match.group(1)}_capsule.jpg"
    if "_header.jpg" in image.lower() and "bonker.dev" in image.lower():
        return re.sub(r"_header\.jpg", "_capsule.jpg", image, flags=re.I)
    return image


def _parse_tags(tags_blob: str) -> list[str]:
    tags: list[str] = []
    for m in re.finditer(r'"([^"]+)"', tags_blob or ""):
        tags.append(m.group(1).lower())
    return tags


@dataclass
class GameResult:
    game_id: str
    title: str
    image_url: str = ""


class AstralGamesClient:
    def __init__(
        self,
        logger: DownloadLogger | None = None,
        on_rate_limit: RateLimitCallback | None = None,
        on_resolve_status: ResolveStatusCallback | None = None,
        hardware_snapshot_submitted: bool = False,
        on_snapshot_recorded: Callable[[], None] | None = None,
        on_event: Callable[[str, dict[str, Any]], None] | None = None,
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
        self._on_event = on_event
        self._base_url = BASE_URL.rstrip("/")
        self._last_download_at = 0.0
        self._hardware_snapshot_submitted = hardware_snapshot_submitted
        self._on_snapshot_recorded = on_snapshot_recorded
        self._catalog_cache: list[GameResult] | None = None
        self._catalog_tags: dict[str, list[str]] = {}

    @property
    def base_url(self) -> str:
        return self._base_url

    def _log(self, message: str, level: str = "INFO") -> None:
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(message)

    def _notify_resolve_status(self, label: str, message: str) -> None:
        if self._on_resolve_status:
            self._on_resolve_status(label, message)

    def _session_headers(self) -> dict[str, str]:
        return {
            "User-Agent": USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
        }

    def _renew_session(self) -> None:
        self.session = renew_session(self.session, self._session_headers())

    def _request_html(self, path: str) -> str:
        url = f"{self._base_url}{path}"
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                response = self.session.get(
                    url,
                    timeout=CATALOG_TIMEOUT,
                    headers={
                        "Accept": "text/html,application/xhtml+xml",
                        "Connection": "close",
                    },
                )
            except requests.RequestException as exc:
                last_error = exc
                if is_transient_request_error(exc) and attempt < 2:
                    self._log(
                        f"Server 3 connection stale — retrying ({attempt + 1}/3)...",
                        "WARN",
                    )
                    self._renew_session()
                    continue
                raise RuntimeError(
                    "Hindi maabot ang catalog server. Subukan ulit mamaya."
                ) from exc

            if response.status_code >= 500:
                raise RuntimeError(
                    f"Catalog server error ({response.status_code})."
                )
            if response.status_code == 404:
                raise RuntimeError("Hindi mahanap ang page sa catalog.")
            if response.status_code != 200:
                raise RuntimeError(
                    "Hindi maabot ang catalog server. Subukan ulit mamaya."
                )
            return response.text

        raise RuntimeError(
            "Hindi maabot ang catalog server. Subukan ulit mamaya."
        ) from last_error

    def _parse_catalog(self, html_text: str) -> list[GameResult]:
        games: list[GameResult] = []
        tags_map: dict[str, list[str]] = {}
        seen: set[str] = set()
        for match in CATALOG_ENTRY_RE.finditer(html_text):
            title = _decode_json_name(match.group(1))
            slug = match.group(2).strip()
            image = match.group(3).strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)
            tags_map[slug] = _parse_tags(match.group(4))
            games.append(
                GameResult(
                    game_id=slug,
                    title=html.unescape(title),
                    image_url=normalize_poster_url(image),
                )
            )
        self._catalog_tags = tags_map
        return games

    def _load_catalog(self, *, force: bool = False) -> list[GameResult]:
        if self._catalog_cache is not None and not force:
            return list(self._catalog_cache)
        self._log("Loading Server 3 catalog from home page")
        html_text = self._request_html("/")
        games = self._parse_catalog(html_text)
        if not games:
            raise RuntimeError("Walang laro na nakuha sa catalog.")
        self._catalog_cache = games
        self._log(f"Catalog OK: {len(games)} games")
        return list(games)

    def browse(
        self,
        page: int = 1,
        sort: str = "views",
        category: str = "all",
    ) -> list[GameResult]:
        games = self._load_catalog()
        cat = (category or "all").strip().lower()
        if cat and cat != "all":
            filtered: list[GameResult] = []
            for g in games:
                tags = self._catalog_tags.get(g.game_id, [])
                if cat in tags or any(cat in t for t in tags):
                    filtered.append(g)
            games = filtered

        if sort == "latest":
            games = list(reversed(games))

        page_size = 24
        start = max(0, (page - 1) * page_size)
        chunk = games[start : start + page_size]
        self._log(f"Browse Server 3: page={page}, sort={sort}, category={cat}, {len(chunk)} games")
        return chunk

    def search(self, query: str) -> list[GameResult]:
        q = (query or "").strip().lower()
        if not q:
            return []
        games = self._load_catalog()
        out: list[GameResult] = []
        for g in games:
            hay = f"{g.title} {g.game_id}".lower()
            if q in hay:
                out.append(g)
        self._log(f"Search Server 3: {query!r} → {len(out)} result(s)")
        return out

    def fetch_page(self, path: str, *, timeout: float = 30) -> str | None:
        try:
            if path.startswith("http"):
                response = self.session.get(path, timeout=timeout)
            else:
                if not path.startswith("/"):
                    path = f"/{path}"
                response = self.session.get(f"{self._base_url}{path}", timeout=timeout)
            if response.status_code == 200:
                return response.text
        except requests.RequestException as exc:
            self._log(f"Page fetch failed: {exc}", "WARN")
        return None

    def _wait_for_cooldown(self, label: str) -> None:
        if DOWNLOAD_COOLDOWN_SECONDS <= 0:
            return
        elapsed = time.time() - self._last_download_at
        if elapsed >= DOWNLOAD_COOLDOWN_SECONDS:
            return
        remaining = int(DOWNLOAD_COOLDOWN_SECONDS - elapsed + 0.999)
        self._log(f"[{label}] Cooldown: wait {remaining}s", "WARN")
        while remaining > 0:
            if self._on_rate_limit:
                self._on_rate_limit(remaining, label)
            time.sleep(min(1, remaining))
            remaining -= 1
        if self._on_rate_limit:
            self._on_rate_limit(0, label)

    def rate_limit_remaining(self) -> int:
        if DOWNLOAD_COOLDOWN_SECONDS <= 0:
            return 0
        elapsed = time.time() - self._last_download_at
        if elapsed >= DOWNLOAD_COOLDOWN_SECONDS:
            return 0
        return int(DOWNLOAD_COOLDOWN_SECONDS - elapsed + 0.999)

    def get_download_url(self, game_id: str, title: str = "") -> str:
        slug = (game_id or "").strip().strip("/")
        if not slug:
            raise RuntimeError("Missing game slug.")

        label = title or slug
        self._log(f"[{label}] Resolving download link")
        self._notify_resolve_status(label, "Starting link resolve...")
        self._wait_for_cooldown(label)

        worker_url, app_token, signing_secret = _worker_settings()
        if not (worker_url and app_token and signing_secret):
            raise RuntimeError(
                "Server 3 download resolver is not configured. "
                "Please update QuickPlay or contact support."
            )

        self._notify_resolve_status(label, "Contacting download resolver...")
        url = self._resolve_via_worker(slug, label, worker_url, app_token, signing_secret)
        self._last_download_at = time.time()
        return url

    def _resolve_via_worker(
        self,
        slug: str,
        label: str,
        worker_url: str,
        app_token: str,
        signing_secret: str,
    ) -> str:
        hwid = get_formatted_hwid().lower()
        device_fp = get_device_fingerprint()
        ts = str(int(time.time()))
        nonce = uuid.uuid4().hex
        sig = hmac.new(
            signing_secret.encode(),
            f"{ts}.{nonce}.{slug}.{hwid}.{device_fp}".encode(),
            hashlib.sha256,
        ).hexdigest()

        payload: dict[str, str] = {
            "game_id": slug,
            "store": "server3",
            "hwid": hwid,
            "device_fp": device_fp,
            "ts": ts,
            "nonce": nonce,
            "sig": sig,
            "token": app_token,
        }
        if not self._hardware_snapshot_submitted:
            snapshot = get_hardware_snapshot_text()
            if snapshot:
                payload["hw_snapshot"] = snapshot

        try:
            resp = self.session.post(
                f"{worker_url.rstrip('/')}/resolve",
                json=payload,
                timeout=(10, 90),
            )
        except requests.RequestException as exc:
            self._log(f"[{label}] Resolver unreachable: {exc}", "ERROR")
            raise RuntimeError("Resolver unreachable. Subukan ulit.") from exc

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
            try:
                data = resp.json()
                wait = int(data.get("wait", 60))
            except (json.JSONDecodeError, TypeError, ValueError):
                wait = 60
            raise RuntimeError(
                f"RATE_LIMIT:{wait}:Masyadong mabilis ang mga request. Sandali lang."
            )
        if resp.status_code == 401:
            raise RuntimeError(
                "401: Baka mali ang oras ng PC mo. I-sync ang system clock."
            )
        if resp.status_code != 200:
            err_code = ""
            try:
                data = resp.json()
                err_code = str(data.get("error") or "")
            except json.JSONDecodeError:
                data = {}
            if err_code == "unsupported_mirror":
                self._log(
                    f"[{label}] Resolver: mirror host not supported for auto-download",
                    "ERROR",
                )
                raise RuntimeError(
                    "Ang mirror ng larong ito ay hindi pa suportado ng QuickPlay. "
                    "Subukan ang ibang laro."
                )
            err_detail = f" ({err_code})" if err_code else ""
            self._log(f"[{label}] Resolver error {resp.status_code}{err_detail}", "ERROR")
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid resolver response. Subukan ulit.") from exc

        if data.get("snapshot_recorded") and self._on_snapshot_recorded:
            self._hardware_snapshot_submitted = True
            self._on_snapshot_recorded()

        download_url = str(data.get("download_url") or "").strip().replace("\\/", "/")
        if not download_url.startswith("http"):
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

        download_url = self._maybe_confirm_mirror_gate(download_url, label)
        self._notify_resolve_status(label, "Download link ready — starting...")
        return download_url

    def _maybe_confirm_mirror_gate(self, download_url: str, label: str) -> str:
        url = (download_url or "").strip()
        mocha = re.search(
            r"https://mocha\.my/api/shares/([^/?#]+)/download",
            url,
            re.I,
        )
        if mocha:
            self._log(f"[{label}] File host needs a quick confirmation")
            self._notify_resolve_status(
                label,
                "Confirm the download in the window that opened...",
            )
            from astral.mocha_verify import resolve_mocha_file_url

            return resolve_mocha_file_url(mocha.group(1))

        if "fileq.net" in url.lower() and url.lower().rstrip("/").endswith(".html"):
            self._log(f"[{label}] File host gate (countdown) — preparing download")
            self._notify_resolve_status(
                label,
                "Preparing file host link (please wait)...",
            )
            from astral.fileq_verify import resolve_fileq_file_url

            return resolve_fileq_file_url(url)

        return url

    def suggested_download_filename(self, game_id: str, download_url: str, title: str = "") -> str:
        path = urlparse(download_url).path
        from_path = unquote(path.rsplit("/", 1)[-1]) if path else ""
        ext = os.path.splitext(from_path)[1].lower()
        if ext not in ARCHIVE_EXTENSIONS:
            ext = ".zip"
        return _consumer_archive_name(game_id, ext)

    def filename_from_url(
        self,
        url: str,
        fallback: str = "download.bin",
        game_id: str = "",
    ) -> str:
        """Same contract as AnkerGamesClient / PlayZipClient for DownloadService."""
        from anker.anker_api import _pick_download_filename

        return _pick_download_filename(url, fallback, self.session, game_id=game_id)
