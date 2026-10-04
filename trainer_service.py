"""Fling trainer search, download, library matching, and launch.

Mirrors the Fluent-Steam-Lua trainer flow (flingtrainer.com search +
attachment-link download + name match) without the WPF UI or SvcMonitor.
"""

from __future__ import annotations

import html
import json
import os
import queue
import re
import sys
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import quote_plus, unquote, urljoin

import requests

from app_paths import app_root_dir
from archive_utils import extract_archive
from download_logger import DownloadLogger
from exe_scanner import launch_play_target
from library_manager import LibraryEntry

FLING_BASE = "https://flingtrainer.com"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
)
ACCEPT_HTML = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,"
    "image/avif,image/webp,*/*;q=0.8"
)

NOT_FOUND_TTL_SEC = 7 * 24 * 3600
ERROR_RETRY_SEC = 3600
REQUEST_TIMEOUT = 20
DOWNLOAD_TIMEOUT = 90
MIN_MATCH_SCORE = 55

_INVALID_NAME_CHARS = re.compile(r'[<>:"/\\|?*]')
_TRAINER_CUT = re.compile(r"\s+trainer\b.*$", re.I)
_PLUS_NUM = re.compile(r"\s+plus\s+[\d.+]+\s*$", re.I)
_VERSION_TAIL = re.compile(r"\s+v\d[\w.]*\s*$", re.I)
_TRAILING_SUFFIXES = (
    " early access",
    " deluxe edition",
    " gold edition",
    " goty edition",
    " complete edition",
    " definitive edition",
    " steam",
    " the anniversary edition",
    " anniversary edition",
    " game of the year edition",
)
_SKIP_EXE = (
    "unins",
    "uninstall",
    "setup",
    "install",
    "redist",
    "vcredist",
    "dxsetup",
    "crash",
)
_RESERVED_STEMS = {
    "CON", "PRN", "AUX", "NUL",
    "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
    "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9",
}

_ARTICLE_RE = re.compile(
    r'<article\b[^>]*class="[^"]*post-standard[^"]*"[^>]*>(.*?)</article>',
    re.I | re.S,
)
_TITLE_HREF_RE = re.compile(
    r'<h2\b[^>]*class="[^"]*post-title[^"]*"[^>]*>\s*'
    r'<a\b[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
    re.I | re.S,
)
_IMG_RE = re.compile(
    r'<img\b[^>]*class="[^"]*wp-post-image[^"]*"[^>]*src="([^"]+)"',
    re.I | re.S,
)
_ATTACH_RE = re.compile(
    r'<a\b[^>]*class="[^"]*attachment-link[^"]*"[^>]*href="([^"]+)"'
    r'|<a\b[^>]*href="([^"]+)"[^>]*class="[^"]*attachment-link[^"]*"',
    re.I | re.S,
)
_TAG_RE = re.compile(r"<[^>]+>")
_CD_FILENAME_RE = re.compile(
    r"filename\*?=(?:UTF-8''|\"?)([^\";]+)",
    re.I,
)


EventCallback = Callable[[str, dict[str, Any]], None]


@dataclass
class TrainerInfo:
    game_name: str
    page_url: str
    cover_url: str = ""


def strip_trainer_suffix(name: str) -> str:
    text = html.unescape(name or "").replace("\u2019", "'").replace("\u2018", "'")
    text = text.strip()
    if text.lower().endswith(" trainer"):
        text = text[: -len(" trainer")].rstrip()
    return text


def extract_game_name(file_name: str) -> str:
    name = html.unescape(file_name or "").strip()
    name = os.path.splitext(name)[0]
    name = _TRAINER_CUT.sub("", name).strip()
    name = _PLUS_NUM.sub("", name).strip()
    name = _VERSION_TAIL.sub("", name).strip()
    changed = True
    lowered = name.lower()
    while changed:
        changed = False
        for suffix in _TRAILING_SUFFIXES:
            if lowered.endswith(suffix):
                name = name[: -len(suffix)].rstrip()
                lowered = name.lower()
                changed = True
                break
    return name


def normalize_for_match(name: str) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _sequel_base(normalized: str) -> str:
    base = normalized
    for _ in range(2):
        trimmed = re.sub(r"(?:ii|iii|iv|vi|2|3|4|5)$", "", base)
        if trimmed == base or len(trimmed) < 6:
            break
        base = trimmed
    return base


def match_score(game_title: str, trainer_name: str) -> int:
    game = normalize_for_match(extract_game_name(game_title))
    trainer = normalize_for_match(extract_game_name(trainer_name))
    if not game or not trainer:
        return 0
    game_token = _trailing_version_token(game)
    trainer_token = _trailing_version_token(trainer)
    if game_token and trainer_token and game_token != trainer_token:
        return 0
    if game == trainer:
        return 100
    if _sequel_base(game) == _sequel_base(trainer):
        return 95
    shorter, longer = (game, trainer) if len(game) <= len(trainer) else (trainer, game)
    if shorter in longer:
        ratio = len(shorter) / len(longer)
        if ratio >= 0.85:
            return int(90 * ratio)
        if ratio >= 0.7:
            return int(75 * ratio)
        if len(shorter) >= 8 and ratio >= 0.5:
            return int(60 * ratio)
    return 0


def _trailing_version_token(normalized: str) -> str:
    for token in ("iii", "ii", "iv", "vi", "4", "3", "2", "5", "6", "7", "8", "9"):
        if normalized.endswith(token) and len(normalized) > len(token) + 2:
            return token
    return ""


def sanitize_file_name(name: str) -> str:
    cleaned = _INVALID_NAME_CHARS.sub("_", (name or "").strip())
    cleaned = cleaned.strip(" .")
    if not cleaned:
        return "trainer"
    stem = os.path.splitext(cleaned)[0].upper()
    if stem in _RESERVED_STEMS:
        return f"trainer-{cleaned}"
    return cleaned[:180]


def trainers_dir() -> str:
    path = os.path.join(app_root_dir(), "cache", "trainers")
    os.makedirs(path, exist_ok=True)
    return path


class TrainerManager:
    def __init__(
        self,
        logger: DownloadLogger | None = None,
        on_event: EventCallback | None = None,
    ) -> None:
        self.logger = logger
        self.on_event = on_event
        self._lock = threading.Lock()
        self._index: dict[str, Any] = {"games": {}}
        self._fetching: set[str] = set()
        self._jobs: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._thread: threading.Thread | None = None
        self._session = requests.Session()
        self._session.headers.update(
            {
                "User-Agent": USER_AGENT,
                "Accept": ACCEPT_HTML,
                "Accept-Language": "en-US,en;q=0.9,zh-CN;q=0.8",
            }
        )
        self._load_index()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(
            target=self._worker,
            name="trainer-worker",
            daemon=True,
        )
        self._thread.start()

    def status_for(self, entry: LibraryEntry) -> dict[str, Any]:
        rec = self._record_for(entry)
        fetching = False
        with self._lock:
            fetching = entry.entry_id in self._fetching or self._norm_key(entry.title) in self._fetching
        downloaded = bool(rec and rec.get("status") == "downloaded" and self._file_ok(rec.get("trainer_path")))
        available = downloaded or bool(rec and rec.get("status") in ("available", "downloaded"))
        if rec and rec.get("status") == "downloaded" and not downloaded:
            available = bool(rec.get("page_url"))
        if rec and rec.get("status") == "error":
            available = True
        return {
            "trainer_available": available,
            "trainer_downloaded": downloaded,
            "trainer_fetching": fetching,
            "trainer_name": str((rec or {}).get("trainer_name") or ""),
        }

    def notify_entry(self, entry: LibraryEntry, *, download: bool, probe: bool = True) -> None:
        self.start()
        self._jobs.put(("entry", (entry.entry_id, entry.title, download, probe)))

    def scan_library(
        self,
        entries: list[LibraryEntry],
        *,
        download: bool,
        probe: bool = True,
    ) -> None:
        self.start()
        snapshot = [(e.entry_id, e.title) for e in entries]
        self._jobs.put(("scan", (snapshot, download, probe)))

    def launch_for_entry(
        self,
        entry: LibraryEntry,
        *,
        fetch_if_needed: bool = True,
    ) -> dict[str, Any]:
        rec = self._record_for(entry)
        path = str((rec or {}).get("trainer_path") or "")
        if self._file_ok(path):
            return self._launch_path(path, entry)
        if not fetch_if_needed:
            return {"ok": False, "error": "trainer_not_downloaded"}
        fetched = self._process_entry(entry.entry_id, entry.title, download=True)
        path = str((fetched or {}).get("trainer_path") or "")
        if not self._file_ok(path):
            return {"ok": False, "error": "trainer_not_found"}
        return self._launch_path(path, entry)

    def remove_for_entry(self, entry_id: str, title: str = "") -> None:
        """Drop cached trainer files and index rows when a library game is deleted."""
        entry_id = (entry_id or "").strip()
        title = (title or "").strip()
        paths: set[str] = set()
        keys_to_drop: set[str] = set()

        with self._lock:
            if entry_id:
                self._fetching.discard(entry_id)
            if title:
                self._fetching.discard(self._norm_key(title))

            games = self._index.get("games") or {}
            if entry_id:
                keys_to_drop.add(entry_id)
            if title:
                keys_to_drop.add(self._norm_key(title))

            for key, item in list(games.items()):
                if not isinstance(item, dict):
                    continue
                if key in keys_to_drop or (entry_id and item.get("entry_id") == entry_id):
                    keys_to_drop.add(key)
                    trainer_path = str(item.get("trainer_path") or "").strip()
                    if trainer_path:
                        paths.add(trainer_path)

            for key in keys_to_drop:
                games.pop(key, None)
            if keys_to_drop:
                self._save_index()

        for trainer_path in paths:
            self._delete_trainer_path(trainer_path)
        if title:
            self._delete_trainer_download_archives(title)

        if paths or keys_to_drop:
            self._log(f"Removed trainer data for deleted library game: {title or entry_id}")

    def _delete_trainer_path(self, trainer_path: str) -> None:
        from path_utils import remove_tree

        if not trainer_path:
            return
        root = os.path.normcase(os.path.abspath(trainers_dir()))
        path = os.path.normcase(os.path.abspath(trainer_path))
        if not path.startswith(root):
            return

        downloads = os.path.normcase(os.path.join(root, "downloads"))
        try:
            if os.path.isfile(path):
                os.remove(path)
        except OSError:
            pass

        parent = os.path.normcase(os.path.dirname(path))
        if parent not in (root, downloads) and os.path.isdir(parent):
            try:
                remove_tree(parent)
            except OSError:
                pass

    def _delete_trainer_download_archives(self, title: str) -> None:
        """Remove leftover Fling zip/rar in downloads/ for this game title."""
        downloads = os.path.join(trainers_dir(), "downloads")
        if not os.path.isdir(downloads):
            return
        stem = sanitize_file_name(extract_game_name(title) or title).lower()
        if not stem:
            return
        for name in os.listdir(downloads):
            lower = name.lower()
            if not lower.endswith((".zip", ".rar", ".7z", ".exe")):
                continue
            if stem in lower or lower.startswith(stem[: min(len(stem), 12)]):
                try:
                    os.remove(os.path.join(downloads, name))
                except OSError:
                    pass

    def _worker(self) -> None:
        while True:
            kind, payload = self._jobs.get()
            try:
                if kind == "scan":
                    snapshot, download, probe = payload
                    for entry_id, title in snapshot:
                        self._process_entry(
                            entry_id, title, download=download, probe=probe
                        )
                        time.sleep(0.4 if probe else 0.02)
                elif kind == "entry":
                    entry_id, title, download, probe = payload
                    self._process_entry(
                        entry_id, title, download=download, probe=probe
                    )
            except Exception as exc:
                self._log(f"Trainer worker error: {exc}")
            finally:
                self._jobs.task_done()

    def _process_entry(
        self,
        entry_id: str,
        title: str,
        *,
        download: bool,
        probe: bool = True,
    ) -> dict[str, Any]:
        existing = self._record_for_ids(entry_id, title)
        info: TrainerInfo | None = None
        try:
            rec = self._match_local(title)
            if rec and self._file_ok(rec.get("trainer_path")):
                saved = self._store_record(entry_id, title, rec)
                self._emit_status(entry_id, title, fetching=False)
                return saved

            if not probe and not download:
                return existing or {}

            if existing and not self._should_refresh(existing, download=download):
                return existing

            with self._lock:
                self._fetching.add(entry_id)
                self._fetching.add(self._norm_key(title))
            self._emit_status(entry_id, title, fetching=True)

            info = self._search_best(title)
            if not info:
                saved = self._store_record(
                    entry_id,
                    title,
                    {
                        "status": "not_found",
                        "trainer_path": "",
                        "trainer_name": "",
                        "page_url": "",
                    },
                )
                self._emit_status(entry_id, title, fetching=False)
                return saved

            if not download:
                saved = self._store_record(
                    entry_id,
                    title,
                    {
                        "status": "available",
                        "trainer_path": "",
                        "trainer_name": info.game_name,
                        "page_url": info.page_url,
                    },
                )
                self._emit_status(entry_id, title, fetching=False)
                return saved

            path = self._download(info)
            saved = self._store_record(
                entry_id,
                title,
                {
                    "status": "downloaded",
                    "trainer_path": path,
                    "trainer_name": info.game_name,
                    "page_url": info.page_url,
                },
            )
            self._log(f"Trainer ready for {title}: {os.path.basename(path)}")
            self._emit_status(
                entry_id,
                title,
                fetching=False,
                message=f"Trainer ready: {title}",
            )
            return saved
        except Exception as exc:
            self._log(f"Trainer fetch failed for {title}: {exc}")
            saved = self._store_record(
                entry_id,
                title,
                {
                    "status": "error",
                    "trainer_path": "",
                    "trainer_name": info.game_name if info else str((existing or {}).get("trainer_name") or ""),
                    "page_url": info.page_url if info else str((existing or {}).get("page_url") or ""),
                    "error": str(exc),
                },
            )
            self._emit_status(entry_id, title, fetching=False)
            return saved
        finally:
            with self._lock:
                self._fetching.discard(entry_id)
                self._fetching.discard(self._norm_key(title))

    def _should_refresh(self, rec: dict[str, Any], *, download: bool) -> bool:
        status = rec.get("status")
        checked = float(rec.get("checked_at") or 0)
        age = time.time() - checked
        if status == "downloaded" and self._file_ok(rec.get("trainer_path")):
            return False
        if status == "available" and rec.get("page_url"):
            return download
        if status == "not_found" and age < NOT_FOUND_TTL_SEC:
            return False
        if status == "error":
            return download
        return True

    def _trainer_search_queries(self, title: str) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []

        def add(raw: str) -> None:
            q = re.sub(r"\s+", " ", (raw or "").strip())
            key = q.lower()
            if q and key not in seen:
                seen.add(key)
                out.append(q)

        add(title)
        short = re.split(r"[:\-–|]", title, maxsplit=1)[0].strip()
        add(short)
        add(re.sub(r"\s+(II|III|IV|2|3|4|5)\s*$", "", title, flags=re.I).strip())
        if short:
            add(re.sub(r"\s+(II|III|IV|2|3|4|5)\s*$", "", short, flags=re.I).strip())
        return out

    def _search_best(self, title: str) -> TrainerInfo | None:
        merged: list[TrainerInfo] = []
        seen_urls: set[str] = set()
        for query in self._trainer_search_queries(title):
            for item in self.search_trainers(query):
                key = item.page_url.lower()
                if key in seen_urls:
                    continue
                seen_urls.add(key)
                merged.append(item)
        best: TrainerInfo | None = None
        best_score = 0
        for item in merged:
            score = match_score(title, item.game_name)
            if score > best_score:
                best_score = score
                best = item
        if best_score < MIN_MATCH_SCORE:
            return None
        return best

    def search_trainers(self, query: str) -> list[TrainerInfo]:
        q = (query or "").strip()
        if not q:
            return []
        results = self._search_wp(q)
        if not results:
            results = self._search_html(q)
        if not results and not q.lower().endswith("trainer"):
            extra = f"{q} trainer"
            results = self._search_wp(extra) or self._search_html(extra)
        return results

    def _search_wp(self, query: str) -> list[TrainerInfo]:
        url = (
            f"{FLING_BASE}/wp-json/wp/v2/posts"
            f"?search={quote_plus(query)}&per_page=10"
        )
        response = self._session.get(
            url,
            timeout=REQUEST_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, list):
            return []
        results: list[TrainerInfo] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            raw_title = item.get("title")
            if isinstance(raw_title, dict):
                raw_title = raw_title.get("rendered") or ""
            name = strip_trainer_suffix(html.unescape(str(raw_title or "")))
            if name.lower().startswith("my trainers archive"):
                continue
            page_url = str(item.get("link") or "").strip()
            if not name or not page_url:
                continue
            results.append(TrainerInfo(game_name=name, page_url=page_url))
        return results

    def _search_html(self, query: str) -> list[TrainerInfo]:
        url = f"{FLING_BASE}/?s={quote_plus(query)}"
        html_text = self._get_text(url)
        return _parse_search_html(html_text)

    def get_download_url(self, page_url: str) -> str | None:
        if not page_url:
            return None
        html_text = self._get_text(page_url)
        match = _ATTACH_RE.search(html_text or "")
        if not match:
            return None
        href = match.group(1) or match.group(2) or ""
        href = html.unescape(href).strip()
        if not href:
            return None
        return urljoin(FLING_BASE + "/", href)

    def _download(self, info: TrainerInfo) -> str:
        last_exc: Exception | None = None
        for attempt in range(3):
            try:
                return self._download_once(info)
            except Exception as exc:
                last_exc = exc
                if attempt < 2:
                    time.sleep(2.0 * (attempt + 1))
        raise last_exc or RuntimeError(f"Download failed for {info.game_name}")

    def _download_once(self, info: TrainerInfo) -> str:
        dl_url = self.get_download_url(info.page_url)
        if not dl_url:
            raise RuntimeError(f"No Fling download link for {info.game_name}")

        dest_dir = os.path.join(trainers_dir(), "downloads")
        os.makedirs(dest_dir, exist_ok=True)

        headers = {
            "Referer": info.page_url,
            "Accept": "application/octet-stream,application/zip,*/*;q=0.8",
        }
        with self._session.get(
            dl_url,
            headers=headers,
            timeout=DOWNLOAD_TIMEOUT,
            stream=True,
        ) as response:
            response.raise_for_status()
            ctype = (response.headers.get("Content-Type") or "").lower()
            if "text/html" in ctype:
                raise RuntimeError("Fling returned a web page instead of a trainer file")
            file_name = _filename_from_response(response, info.game_name)
            save_path = os.path.join(dest_dir, file_name)
            with open(save_path, "wb") as handle:
                for chunk in response.iter_content(chunk_size=64 * 1024):
                    if chunk:
                        handle.write(chunk)

        exe_path = self._ensure_exe(save_path, info.game_name)
        if not exe_path:
            raise RuntimeError(f"No trainer EXE found for {info.game_name}")
        return exe_path

    def _ensure_exe(self, downloaded: str, game_name: str) -> str | None:
        ext = os.path.splitext(downloaded)[1].lower()
        if ext == ".exe":
            return downloaded
        if ext in (".zip", ".rar", ".7z"):
            out_dir = os.path.join(
                trainers_dir(),
                sanitize_file_name(extract_game_name(game_name) or game_name),
            )
            os.makedirs(out_dir, exist_ok=True)
            extract_archive(downloaded, out_dir)
            return _pick_trainer_exe(out_dir)
        return downloaded if downloaded.lower().endswith(".exe") else None

    def _match_local(self, title: str) -> dict[str, Any] | None:
        best_path = ""
        best_score = 0
        best_name = ""
        root = trainers_dir()
        if not os.path.isdir(root):
            return None
        for dirpath, _dirnames, filenames in os.walk(root):
            for filename in filenames:
                if not filename.lower().endswith(".exe"):
                    continue
                lower = filename.lower()
                if any(skip in lower for skip in _SKIP_EXE):
                    continue
                path = os.path.join(dirpath, filename)
                score = match_score(title, filename)
                cached = self._cached_display_name(path)
                if cached:
                    score = max(score, match_score(title, cached))
                if score > best_score:
                    best_score = score
                    best_path = path
                    best_name = cached or extract_game_name(filename)
        if best_score < MIN_MATCH_SCORE or not best_path:
            return None
        return {
            "status": "downloaded",
            "trainer_path": best_path,
            "trainer_name": best_name,
            "page_url": "",
        }

    def _cached_display_name(self, trainer_path: str) -> str:
        recs = self._index.get("games") or {}
        for rec in recs.values():
            if not isinstance(rec, dict):
                continue
            if os.path.normcase(str(rec.get("trainer_path") or "")) == os.path.normcase(trainer_path):
                return str(rec.get("trainer_name") or "")
        return ""

    def _launch_path(self, path: str, entry: LibraryEntry) -> dict[str, Any]:
        try:
            if sys.platform == "win32":
                os.startfile(os.path.abspath(path))  # type: ignore[attr-defined]
            else:
                launch_play_target(path)
        except Exception as exc:
            return {"ok": False, "error": str(exc)}
        self._log(f"Launched trainer for {entry.title}: {os.path.basename(path)}")
        return {"ok": True, "trainer_path": path, "title": entry.title}

    def _get_text(self, url: str) -> str:
        response = self._session.get(url, timeout=REQUEST_TIMEOUT)
        response.raise_for_status()
        response.encoding = response.encoding or "utf-8"
        return response.text

    def _index_path(self) -> str:
        return os.path.join(trainers_dir(), "index.json")

    def _load_index(self) -> None:
        path = self._index_path()
        if not os.path.isfile(path):
            self._index = {"games": {}}
            return
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if isinstance(raw, dict) and isinstance(raw.get("games"), dict):
                self._index = raw
            else:
                self._index = {"games": {}}
        except (OSError, json.JSONDecodeError):
            self._index = {"games": {}}

    def _save_index(self) -> None:
        path = self._index_path()
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as handle:
            json.dump(self._index, handle, indent=2)
        os.replace(tmp, path)

    def _store_record(self, entry_id: str, title: str, rec: dict[str, Any]) -> dict[str, Any]:
        payload = {
            "title": title,
            "entry_id": entry_id,
            "status": rec.get("status") or "error",
            "trainer_path": rec.get("trainer_path") or "",
            "trainer_name": rec.get("trainer_name") or "",
            "page_url": rec.get("page_url") or "",
            "error": rec.get("error") or "",
            "checked_at": time.time(),
        }
        with self._lock:
            games = self._index.setdefault("games", {})
            games[entry_id] = payload
            games[self._norm_key(title)] = payload
            self._save_index()
        return payload

    def _record_for(self, entry: LibraryEntry) -> dict[str, Any] | None:
        return self._record_for_ids(entry.entry_id, entry.title)

    def _record_for_ids(self, entry_id: str, title: str) -> dict[str, Any] | None:
        with self._lock:
            games = self._index.get("games") or {}
            rec = games.get(entry_id) or games.get(self._norm_key(title))
            return dict(rec) if isinstance(rec, dict) else None

    @staticmethod
    def _norm_key(title: str) -> str:
        return "title:" + normalize_for_match(extract_game_name(title))

    @staticmethod
    def _file_ok(path: str | None) -> bool:
        return bool(path) and os.path.isfile(path)

    def _emit_status(
        self,
        entry_id: str,
        title: str,
        *,
        fetching: bool,
        message: str | None = None,
    ) -> None:
        rec = self._record_for_ids(entry_id, title) or {}
        downloaded = rec.get("status") == "downloaded" and self._file_ok(rec.get("trainer_path"))
        available = downloaded or rec.get("status") in ("available", "downloaded")
        payload = {
            "entry_id": entry_id,
            "title": title,
            "trainer_available": bool(available),
            "trainer_downloaded": bool(downloaded),
            "trainer_fetching": fetching,
            "trainer_name": str(rec.get("trainer_name") or ""),
        }
        if message:
            payload["message"] = message
        if self.on_event:
            self.on_event("trainer_updated", payload)

    def _log(self, message: str) -> None:
        if self.logger:
            self.logger.info(message)


def _parse_search_html(html_text: str) -> list[TrainerInfo]:
    results: list[TrainerInfo] = []
    chunks = _ARTICLE_RE.findall(html_text or "")
    if not chunks:
        chunks = [html_text or ""]
    for article in chunks:
        title_match = _TITLE_HREF_RE.search(article)
        if not title_match:
            continue
        page_url = html.unescape(title_match.group(1)).strip()
        raw_name = _TAG_RE.sub("", title_match.group(2))
        name = strip_trainer_suffix(raw_name)
        if not name:
            continue
        img_match = _IMG_RE.search(article)
        cover = html.unescape(img_match.group(1)).strip() if img_match else ""
        results.append(TrainerInfo(game_name=name, page_url=page_url, cover_url=cover))
        if len(results) >= 10:
            break
    return results


def _filename_from_response(response: requests.Response, game_name: str) -> str:
    disposition = response.headers.get("Content-Disposition") or ""
    match = _CD_FILENAME_RE.search(disposition)
    if match:
        raw = html.unescape(match.group(1).strip().strip('"'))
        flat = sanitize_file_name(os.path.basename(unquote(raw)))
        if flat and os.path.splitext(flat)[1]:
            return flat
    path_name = os.path.basename(unquote(response.url.split("?", 1)[0]))
    ext = os.path.splitext(path_name)[1].lower()
    ctype = (response.headers.get("Content-Type") or "").lower()
    if not ext:
        if "zip" in ctype:
            ext = ".zip"
        elif "7z" in ctype:
            ext = ".7z"
        else:
            ext = ".exe"
    if ext == ".exe" and path_name.lower().endswith(".exe"):
        flat = sanitize_file_name(path_name)
        if flat:
            return flat
    return f"{sanitize_file_name(game_name)}-FLiNG{ext}"


def _pick_trainer_exe(root: str) -> str | None:
    found: list[str] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            if not filename.lower().endswith(".exe"):
                continue
            lower = filename.lower()
            if any(skip in lower for skip in _SKIP_EXE):
                continue
            found.append(os.path.join(dirpath, filename))
    if not found:
        return None

    def score(path: str) -> tuple[int, int]:
        name = os.path.basename(path).lower()
        prefer = 2 if "trainer" in name or "fling" in name else 0
        return (prefer, len(name))

    found.sort(key=score, reverse=True)
    return found[0]
