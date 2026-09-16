"""AnkerGames store client — browse, search, and licensed download via Worker.

Download URLs are resolved only through ``anker-dlresolver`` (see ``client_secrets``).
The upstream Anker formula and secrets (e.g. recaptcha bypass) live server-side.
"""

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

if TYPE_CHECKING:
    from download_logger import DownloadLogger

RateLimitCallback = Callable[[int, str], None]
ResolveStatusCallback = Callable[[str, str], None]

def _worker_settings() -> tuple[str, str, str]:
    """Read Worker credentials at call time (PyInstaller bundle + patch_store)."""
    try:
        import client_secrets as cs

        url = (getattr(cs, "ANKER_DL_WORKER_URL", None) or "").strip()
        token = (getattr(cs, "APP_TOKEN", None) or "").strip()
        secret = (getattr(cs, "SIGNING_SECRET", None) or "").strip()
        if url and token and secret:
            return url, token, secret
    except ImportError:
        pass
    g = globals()
    return (
        (g.get("ANKER_DL_WORKER_URL") or "").strip(),
        (g.get("APP_TOKEN") or "").strip(),
        (g.get("SIGNING_SECRET") or "").strip(),
    )

try:
    from anker.config import BASE_URL, DOWNLOAD_COOLDOWN_SECONDS
except ImportError:  # pragma: no cover - template fallback
    BASE_URL = "https://ankergames.net"
    DOWNLOAD_COOLDOWN_SECONDS = 0

from device_fingerprint import get_device_fingerprint
from hardware_snapshot import get_hardware_snapshot_text
from license_manager import LicenseRequiredError, get_formatted_hwid

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

LISTING_ATTR_PATTERN = re.compile(
    r'<article[^>]*\slisting="(\{.*?\})"',
    re.S | re.I,
)
GENERATE_DOWNLOAD_ID_PATTERN = re.compile(
    r"generateDownloadUrl\(\s*(\d+)\s*\)",
    re.I,
)
GATE_PAGE_URL_PATTERN = re.compile(
    r"downloadPage\(\s*['\"]([^'\"]+)['\"]",
    re.I,
)
RECAPTCHA_BYPASS = "development-mode"
RECENT_UPDATE_ARTICLE_PATTERN = re.compile(
    r'<article[^>]*aria-label="Game update:\s*([^"]+)"[^>]*>(.*?)</article>',
    re.S | re.I,
)
# ankergames.net 2026+ browse/search cards (replaces listing="..." JSON on <article>).
UI_POST_CARD_ARTICLE_PATTERN = re.compile(
    r'<article[^>]*\bx-data="uiPostCard\([^)]+\)"[^>]*>(.*?)</article>',
    re.S | re.I,
)
GAME_SLUG_FROM_HREF_PATTERN = re.compile(
    r'href="[^"]*/game/([^"?#]+)"',
    re.I,
)
GAME_CARD_IMG_ALT_SRC_PATTERN = re.compile(
    r'<img[^>]+alt="([^"]*)"[^>]+src="([^"]+)"',
    re.I,
)
GAME_CARD_IMG_SRC_ALT_PATTERN = re.compile(
    r'<img[^>]+src="([^"]+)"[^>]+alt="([^"]*)"',
    re.I,
)
GAME_CARD_ARIA_LABEL_PATTERN = re.compile(
    r'aria-label="([^"]+?)\s*—\s*View details"',
    re.I,
)
GAME_CARD_H3_TITLE_PATTERN = re.compile(
    r'<h3[^>]*\btitle="([^"]+)"',
    re.I,
)
ARCHIVE_EXTENSIONS = (".zip", ".7z", ".rar", ".tar", ".gz", ".001")
MAX_FILENAME_LEN = 180
_INVALID_WIN_FILENAME = re.compile(r'[<>:"/\\|?*]')
_CONTENT_DISPOSITION_FILENAME = re.compile(
    r"filename\*=(?:UTF-8''|utf-8'')([^;\s]+)|filename=\"([^\"]+)\"|filename=([^;\s]+)",
    re.I,
)
_STORE_NAME_SUFFIX = re.compile(r"[-_]?ankergames$", re.I)

# Featured genres observed on ankergames.net (slug → label).
CATEGORIES: list[dict[str, str]] = [
    {"id": "all", "label": "All"},
    {"id": "action", "label": "Action"},
    {"id": "adventure", "label": "Adventure"},
    {"id": "simulation", "label": "Simulation"},
    {"id": "horror", "label": "Horror"},
    {"id": "rpg", "label": "RPG"},
    {"id": "strategy", "label": "Strategy"},
    {"id": "indie", "label": "Indie"},
    {"id": "casual", "label": "Casual"},
    {"id": "racing", "label": "Racing"},
    {"id": "sports", "label": "Sports"},
    {"id": "war", "label": "War"},
    {"id": "mystery", "label": "Mystery"},
    {"id": "fantasy", "label": "Fantasy"},
]


def _sanitize_filename(name: str) -> str:
    cleaned = html.unescape((name or "").strip())
    cleaned = _INVALID_WIN_FILENAME.sub("_", cleaned)
    cleaned = cleaned.rstrip(". ")
    if len(cleaned) > MAX_FILENAME_LEN:
        root, ext = os.path.splitext(cleaned)
        cleaned = root[: max(1, MAX_FILENAME_LEN - len(ext))] + ext
    return cleaned or "download"


def _archive_ext(name: str) -> str:
    ext = os.path.splitext(name)[1].lower()
    return ext if ext in ARCHIVE_EXTENSIONS else ""


def _strip_store_branding(name: str) -> str:
    stem, ext = os.path.splitext(_sanitize_filename(name))
    stem = _STORE_NAME_SUFFIX.sub("", stem).rstrip("-_. ")
    if not stem:
        stem = "download"
    return stem + (ext if ext in ARCHIVE_EXTENSIONS else ".zip")


def _consumer_archive_name(game_id: str, ext: str = ".zip") -> str:
    """User-facing save name: slug only, no store branding (e.g. metalslug.zip)."""
    slug = re.sub(r"[^a-z0-9]+", "", (game_id or "").lower())
    slug = slug or "download"
    ext = ext if ext in ARCHIVE_EXTENSIONS else ".zip"
    return _sanitize_filename(slug + ext)


def _detect_archive_ext(url: str, fallback: str, session: requests.Session | None) -> str:
    path = urlparse(url).path
    from_path = unquote(path.rsplit("/", 1)[-1]) if path else ""
    ext = _archive_ext(from_path)
    if ext:
        return ext
    if session is not None:
        from_cd = _filename_from_content_disposition(session, url)
        ext = _archive_ext(from_cd)
        if ext:
            return ext
    ext = _archive_ext(fallback)
    return ext or ".zip"


def _filename_from_content_disposition(session: requests.Session, url: str) -> str:
    try:
        response = session.head(url, timeout=20, allow_redirects=True)
        if response.status_code >= 400:
            return ""
        cd = response.headers.get("Content-Disposition", "")
        if not cd:
            return ""
        match = _CONTENT_DISPOSITION_FILENAME.search(cd)
        if not match:
            return ""
        raw = match.group(1) or match.group(2) or match.group(3) or ""
        return _sanitize_filename(unquote(raw.strip().strip("'")))
    except Exception:
        return ""


def _pick_download_filename(
    url: str,
    fallback: str,
    session: requests.Session | None,
    game_id: str = "",
) -> str:
    ext = _detect_archive_ext(url, fallback, session)
    if game_id:
        return _consumer_archive_name(game_id, ext)

    path = urlparse(url).path
    from_path = unquote(path.rsplit("/", 1)[-1]) if path else ""
    from_path = _sanitize_filename(from_path)

    if from_path and len(from_path) <= MAX_FILENAME_LEN and _archive_ext(from_path):
        return _strip_store_branding(from_path)

    # Hash-only CDN paths (400+ chars, no extension) break Windows MAX_PATH with .part.partN
    if session is not None:
        from_cd = _filename_from_content_disposition(session, url)
        if from_cd and _archive_ext(from_cd):
            return _strip_store_branding(from_cd)

    fb = _sanitize_filename(fallback)
    if fb and not _archive_ext(fb):
        fb = fb.rsplit(".", 1)[0] + ext if "." in fb else fb + ext
    return _strip_store_branding(fb or f"download{ext}")


def normalize_image_url(url: str) -> str:
    url = html.unescape((url or "").strip())
    if url.startswith("//"):
        url = "https:" + url
    return url


@dataclass
class GameResult:
    """game_id is the Anker slug (e.g. project-zomboid)."""

    game_id: str
    title: str
    image_url: str = ""


class AnkerGamesClient:
    """Drop-in shape similar to PlayZipClient for browse/search/download."""

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

    @property
    def base_url(self) -> str:
        return self._base_url

    def _log(self, message: str, level: str = "INFO") -> None:
        if self.logger:
            getattr(self.logger, level.lower(), self.logger.info)(message)

    def _notify_resolve_status(self, label: str, message: str) -> None:
        if self._on_resolve_status:
            self._on_resolve_status(label, message)

    def _request_html(
        self,
        path: str,
        *,
        params: dict[str, str | int] | None = None,
        timeout: float = 30,
    ) -> str:
        url = f"{self._base_url}{path}"
        try:
            response = self.session.get(
                url,
                params=params or {},
                timeout=timeout,
                headers={"Accept": "text/html,application/xhtml+xml"},
            )
        except requests.RequestException as exc:
            raise RuntimeError(
                "Hindi maabot ang AnkerGames. Subukan ulit mamaya."
            ) from exc

        if response.status_code >= 500:
            raise RuntimeError(f"AnkerGames server error ({response.status_code}).")

        response.raise_for_status()
        return response.text

    @staticmethod
    def _decode_listing_attr(raw: str) -> dict | None:
        cleaned = html.unescape(raw.strip())
        cleaned = cleaned.replace("\\/", "/")
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            return None
        return data if isinstance(data, dict) else None

    def _parse_recent_updates(self, page_html: str) -> list[GameResult]:
        results: list[GameResult] = []
        seen: set[str] = set()

        for match in RECENT_UPDATE_ARTICLE_PATTERN.finditer(page_html):
            title = html.unescape(match.group(1).strip())
            block = match.group(2)
            slug_match = re.search(r'href="[^"]*/game/([^"?#]+)"', block, re.I)
            if not slug_match or not title:
                continue
            slug = slug_match.group(1).strip()
            if not slug or slug in seen:
                continue
            seen.add(slug)

            image = ""
            img_match = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
            if img_match:
                image = img_match.group(1).strip()
            if image and not image.startswith("http"):
                image = f"{self._base_url}/uploads/poster/{image.lstrip('/')}"

            results.append(
                GameResult(
                    game_id=slug,
                    title=title,
                    image_url=normalize_image_url(image),
                )
            )
        return results

    def _parse_ui_post_cards(self, page_html: str) -> list[GameResult]:
        """Parse browse/search cards that use Alpine uiPostCard (no listing= JSON)."""
        results: list[GameResult] = []
        seen: set[str] = set()

        for match in UI_POST_CARD_ARTICLE_PATTERN.finditer(page_html):
            block = match.group(1)
            slug_match = GAME_SLUG_FROM_HREF_PATTERN.search(block)
            if not slug_match:
                continue
            slug = slug_match.group(1).strip()
            if not slug or slug in seen:
                continue

            title = ""
            image = ""

            img_match = GAME_CARD_IMG_ALT_SRC_PATTERN.search(block)
            if img_match:
                title = html.unescape(img_match.group(1).strip())
                image = img_match.group(2).strip()
            else:
                img_match = GAME_CARD_IMG_SRC_ALT_PATTERN.search(block)
                if img_match:
                    image = img_match.group(1).strip()
                    title = html.unescape(img_match.group(2).strip())

            if not title:
                aria = GAME_CARD_ARIA_LABEL_PATTERN.search(block)
                if aria:
                    title = html.unescape(aria.group(1).strip())

            if not title:
                h3 = GAME_CARD_H3_TITLE_PATTERN.search(block)
                if h3:
                    title = html.unescape(h3.group(1).strip())

            if not title:
                continue

            if not image:
                bare_img = re.search(r'<img[^>]+src="([^"]+)"', block, re.I)
                if bare_img:
                    image = bare_img.group(1).strip()

            if image and not image.startswith("http"):
                image = f"{self._base_url}/uploads/poster/{image.lstrip('/')}"

            seen.add(slug)
            results.append(
                GameResult(
                    game_id=slug,
                    title=title,
                    image_url=normalize_image_url(image),
                )
            )
        return results

    def _parse_listings(self, page_html: str) -> list[GameResult]:
        results: list[GameResult] = []
        seen: set[str] = set()

        for match in LISTING_ATTR_PATTERN.finditer(page_html):
            item = self._decode_listing_attr(match.group(1))
            if not item:
                continue
            slug = str(item.get("slug") or "").strip()
            title = html.unescape(str(item.get("title") or "").strip())
            if not slug or not title or slug in seen:
                continue
            seen.add(slug)

            image = (
                str(item.get("imageurl") or item.get("coverurl") or "").strip()
                or str(item.get("image") or "").strip()
            )
            if image and not image.startswith("http"):
                image = f"{self._base_url}/uploads/poster/{image.lstrip('/')}"

            results.append(
                GameResult(
                    game_id=slug,
                    title=title,
                    image_url=normalize_image_url(image),
                )
            )
        if results:
            return results

        ui_cards = self._parse_ui_post_cards(page_html)
        if ui_cards:
            return ui_cards

        return self._parse_recent_updates(page_html)

    def _browse_path(
        self,
        page: int,
        sort: str,
        category: str,
    ) -> tuple[str, dict[str, str | int]]:
        category = (category or "all").strip().lower()
        params: dict[str, str | int] = {}

        if category and category != "all":
            path = f"/genre/{quote(category, safe='')}"
        elif sort == "trending":
            path = "/trending"
        elif sort == "latest":
            path = "/recent-updates"
        else:
            path = "/games"

        if page > 1:
            params["page"] = page
        return path, params

    def browse(
        self,
        page: int = 1,
        sort: str = "views",
        category: str = "all",
    ) -> list[GameResult]:
        path, params = self._browse_path(page, sort, category)
        self._log(f"Browse Anker: path={path}, page={page}, sort={sort}, category={category}")
        html_text = self._request_html(path, params=params)
        games = self._parse_listings(html_text)
        self._log(f"Browse OK: {len(games)} games")
        return games

    def search(self, query: str) -> list[GameResult]:
        query = query.strip()
        if not query:
            return []

        encoded = quote(query, safe="")
        self._log(f"Search Anker: {query!r}")
        html_text = self._request_html(f"/search/{encoded}")
        games = self._parse_listings(html_text)
        self._log(f"Search OK: {len(games)} result(s)")
        return games

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

    def _notify_rate_limit(self, seconds: int, label: str) -> None:
        if self._on_rate_limit:
            self._on_rate_limit(seconds, label)

    def _wait_for_cooldown(self, label: str) -> None:
        if DOWNLOAD_COOLDOWN_SECONDS <= 0:
            return
        elapsed = time.time() - self._last_download_at
        if elapsed >= DOWNLOAD_COOLDOWN_SECONDS:
            return
        remaining = int(DOWNLOAD_COOLDOWN_SECONDS - elapsed + 0.999)
        self._log(f"[{label}] Cooldown: wait {remaining}s", "WARN")
        while remaining > 0:
            self._notify_rate_limit(remaining, label)
            time.sleep(min(1, remaining))
            remaining -= 1
        self._notify_rate_limit(0, label)

    def rate_limit_remaining(self) -> int:
        if DOWNLOAD_COOLDOWN_SECONDS <= 0:
            return 0
        elapsed = time.time() - self._last_download_at
        if elapsed >= DOWNLOAD_COOLDOWN_SECONDS:
            return 0
        return int(DOWNLOAD_COOLDOWN_SECONDS - elapsed + 0.999)

    def get_download_url(self, game_id: str, title: str = "") -> str:
        """Resolve a CDN download URL via the licensed cloud resolver (Worker only)."""
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
                "Server 2 download resolver is not configured. "
                "Please update QuickPlay or contact support."
            )

        self._log(f"[{label}] Using cloud download resolver")
        self._notify_resolve_status(label, "Contacting download resolver...")
        url = self._resolve_via_worker(
            slug, label, worker_url, app_token, signing_secret
        )
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
        """Licensed resolve via anker-dlresolver Worker."""
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
            self._log(f"[{label}] Resolver rejected request (401)", "ERROR")
            raise RuntimeError(
                "401: Baka mali ang oras ng PC mo. I-sync ang system clock."
            )
        if resp.status_code != 200:
            err_detail = ""
            try:
                data = resp.json()
                if data.get("error"):
                    err_detail = f" ({data['error']})"
            except json.JSONDecodeError:
                pass
            self._log(f"[{label}] Resolver error {resp.status_code}{err_detail}", "ERROR")
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

        try:
            data = resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Invalid resolver response. Subukan ulit.") from exc

        if data.get("snapshot_recorded") and self._on_snapshot_recorded:
            self._hardware_snapshot_submitted = True
            self._on_snapshot_recorded()

        if data.get("client_resolve"):
            gate_url = str(data.get("gate_url") or "").strip().replace("\\/", "/")
            return self._resolve_via_client_gate(slug, label, gate_url or None)

        download_url = str(data.get("download_url") or "").strip().replace("\\/", "/")
        if not download_url.startswith("http"):
            raise RuntimeError("Hindi valid ang download link. Subukan ulit.")
        if self._is_anker_gate_url(download_url):
            return self._resolve_via_client_gate(slug, label, download_url)

        self._log(f"[{label}] Download link ready")
        self._notify_resolve_status(label, "Download link ready — starting...")
        return download_url

    @staticmethod
    def _is_anker_gate_url(url: str) -> bool:
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        host = (parsed.hostname or "").lower()
        if host not in ("ankergames.net", "www.ankergames.net"):
            return False
        path = parsed.path.lower()
        return path.startswith("/download/") or path.startswith("/download-file/")

    def _local_generate_gate_url(self, slug: str, label: str) -> str:
        """Residential CSRF flow → signed /download/ gate URL."""
        game_path = f"/game/{quote(slug.strip('/'))}"
        try:
            csrf_resp = self.session.get(
                f"{self._base_url}/csrf-token",
                headers={
                    "Accept": "application/json",
                    "X-Requested-With": "XMLHttpRequest",
                },
                timeout=30,
            )
            csrf_resp.raise_for_status()
            csrf_token = str(csrf_resp.json().get("token") or "").strip()
        except (requests.RequestException, json.JSONDecodeError, TypeError, ValueError) as exc:
            self._log(f"[{label}] CSRF failed: {exc}", "ERROR")
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.") from exc

        if not csrf_token:
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

        page_html = self._request_html(game_path)
        id_match = GENERATE_DOWNLOAD_ID_PATTERN.search(page_html)
        if not id_match:
            raise RuntimeError("Hindi available ang download para sa larong ito.")

        download_id = id_match.group(1)
        payload = {"g-recaptcha-response": RECAPTCHA_BYPASS}
        headers = {
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
            "X-CSRF-TOKEN": csrf_token,
            "Referer": f"{self._base_url}{game_path}",
            "Origin": self._base_url,
        }

        try:
            gen_resp = self.session.post(
                f"{self._base_url}/generate-download-url/{download_id}",
                json=payload,
                headers=headers,
                timeout=30,
            )
        except requests.RequestException as exc:
            self._log(f"[{label}] Generate URL failed: {exc}", "ERROR")
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.") from exc

        if gen_resp.status_code == 419:
            try:
                csrf_token = str(
                    self.session.get(
                        f"{self._base_url}/csrf-token",
                        headers={
                            "Accept": "application/json",
                            "X-Requested-With": "XMLHttpRequest",
                        },
                        timeout=30,
                    ).json().get("token")
                    or ""
                ).strip()
                headers["X-CSRF-TOKEN"] = csrf_token
                gen_resp = self.session.post(
                    f"{self._base_url}/generate-download-url/{download_id}",
                    json=payload,
                    headers=headers,
                    timeout=30,
                )
            except (requests.RequestException, json.JSONDecodeError, TypeError, ValueError):
                pass

        if gen_resp.status_code == 429:
            raise RuntimeError(
                "RATE_LIMIT:60:Masyadong mabilis ang mga request. Sandali lang."
            )
        if gen_resp.status_code >= 400:
            self._log(f"[{label}] Generate URL HTTP {gen_resp.status_code}", "ERROR")
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.")

        try:
            gen_data = gen_resp.json()
        except json.JSONDecodeError as exc:
            raise RuntimeError("Hindi makakuha ng download link. Subukan ulit.") from exc

        gate_url = str(gen_data.get("download_url") or "").strip().replace("\\/", "/")
        if not gate_url.startswith("http"):
            raise RuntimeError("Hindi valid ang download link. Subukan ulit.")
        return gate_url

    def _resolve_via_client_gate(
        self,
        slug: str,
        label: str,
        gate_url: str | None = None,
    ) -> str:
        """Finish Anker gate hops in a WebView (Turnstile + countdown)."""
        from anker.gate_resolver import resolve_anker_gate_url

        # Worker gate URLs expire in seconds — always mint a fresh link on the client
        # immediately before opening the verification window.
        self._log(f"[{label}] Preparing download verification")
        gate_url = self._local_generate_gate_url(slug, label)

        max_attempts = 2
        last_error = ""
        for attempt in range(1, max_attempts + 1):
            if attempt > 1:
                self._log(
                    f"[{label}] Verification link expired — retrying ({attempt}/{max_attempts})",
                    "WARN",
                )
                gate_url = self._local_generate_gate_url(slug, label)
            self._log(f"[{label}] Resolving CDN link (background)")
            try:
                final_url = resolve_anker_gate_url(
                    gate_url,
                    on_event=self._on_event,
                    label=label,
                )
            except RuntimeError as exc:
                last_error = str(exc)
                if attempt < max_attempts and "expired" in last_error.lower():
                    continue
                raise
            if self._is_anker_gate_url(final_url):
                # We must land on the real CDN file, not another ankergames hop.
                last_error = "Incomplete download link. Subukan ulit."
                if attempt < max_attempts:
                    continue
                raise RuntimeError(last_error)
            self._log(f"[{label}] Download link ready")
            if self._on_event:
                self._on_event(
                    "gate_progress",
                    {
                        "label": label,
                        "countdown": 0,
                        "status_message": "Passing to downloader...",
                        "phase": "handoff",
                    },
                )
            return final_url
        raise RuntimeError(last_error or "Could not resolve download link. Subukan ulit.")

    def fetch_image_bytes(self, url: str) -> bytes:
        if not url:
            return b""
        response = self.session.get(url, timeout=30)
        response.raise_for_status()
        return response.content

    def filename_from_url(
        self,
        url: str,
        fallback: str = "download.bin",
        game_id: str = "",
    ) -> str:
        return _pick_download_filename(url, fallback, self.session, game_id=game_id)


# Alias so DownloadService can swap imports with minimal changes.
PlayZipClient = AnkerGamesClient
