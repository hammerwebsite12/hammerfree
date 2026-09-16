"""IDM-style multi-connection HTTP downloader with pause/resume."""

from __future__ import annotations

import http.client
import json
import os
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable
from urllib.parse import urlparse, unquote

import requests
import urllib3

ProgressCallback = Callable[[int, int, float, str], None]
StatusCallback = Callable[[str], None]

# Errors that indicate a dropped/interrupted connection where resuming is safe.
TRANSIENT_ERRORS = (
    requests.exceptions.ChunkedEncodingError,
    requests.exceptions.ConnectionError,
    requests.exceptions.Timeout,
    urllib3.exceptions.ProtocolError,
    urllib3.exceptions.IncompleteRead,
    urllib3.exceptions.ReadTimeoutError,
    http.client.IncompleteRead,
    ConnectionError,
)

MAX_RETRIES = 8
RETRY_BACKOFF = 3.0
CHUNK_SIZE = 32 * 1024  # smaller chunks so pause responds faster
_PROGRESS_VERSION = 1


class DownloadState(str, Enum):
    QUEUED = "Queued"
    CONNECTING = "Connecting"
    ALLOCATING = "Allocating"
    DOWNLOADING = "Downloading"
    MERGING = "Merging"
    PAUSED = "Paused"
    COMPLETED = "Completed"
    ERROR = "Error"
    CANCELLED = "Cancelled"


@dataclass
class DownloadTask:
    url: str
    dest_path: str
    connections: int = 8
    state: DownloadState = DownloadState.QUEUED
    downloaded: int = 0
    total_size: int = 0
    speed: float = 0.0
    error: str = ""
    _pause_event: threading.Event = field(default_factory=threading.Event)
    _cancel_event: threading.Event = field(default_factory=threading.Event)
    _threads: list[threading.Thread] = field(default_factory=list)
    _main_thread: threading.Thread | None = None
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def __post_init__(self) -> None:
        self._pause_event.set()

    @property
    def filename(self) -> str:
        return os.path.basename(self.dest_path)


class IDMDownloader:
    USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )

    def __init__(
        self,
        on_progress: ProgressCallback | None = None,
        on_status: StatusCallback | None = None,
    ) -> None:
        self.on_progress = on_progress
        self.on_status = on_status
        self._progress_disk_lock = threading.Lock()

    def start(self, task: DownloadTask) -> None:
        worker = threading.Thread(target=self._run, args=(task,), daemon=True)
        task._main_thread = worker
        worker.start()

    def pause(self, task: DownloadTask) -> None:
        task._pause_event.clear()
        with task._lock:
            task.state = DownloadState.PAUSED
            task.speed = 0.0
        self._notify_progress(task)
        self._notify_status(task, DownloadState.PAUSED.value)

    def resume(self, task: DownloadTask) -> None:
        task._pause_event.set()
        with task._lock:
            task.state = DownloadState.DOWNLOADING
        self._notify_progress(task)
        self._notify_status(task, DownloadState.DOWNLOADING.value)

    def cancel(self, task: DownloadTask) -> None:
        task._cancel_event.set()
        task._pause_event.set()
        with task._lock:
            task.state = DownloadState.CANCELLED
        self._notify_status(task, DownloadState.CANCELLED.value)
        for thread in task._threads:
            if thread.is_alive():
                thread.join(timeout=10)
        main = task._main_thread
        if main and main.is_alive():
            main.join(timeout=10)

    def _notify_progress(self, task: DownloadTask) -> None:
        if self.on_progress:
            self.on_progress(
                task.downloaded,
                task.total_size,
                task.speed,
                task.state.value,
            )

    def _notify_status(self, task: DownloadTask, message: str) -> None:
        if self.on_status:
            self.on_status(message)

    @staticmethod
    def _wait_or_cancel(task: DownloadTask, seconds: float) -> None:
        """Sleep in small steps, aborting early if the task is cancelled."""
        deadline = time.time() + seconds
        while time.time() < deadline:
            if task._cancel_event.is_set():
                return
            time.sleep(0.1)

    def _prepare_resume(self, part_path: str) -> int:
        """Bytes already saved for a single-connection download (contiguous .part)."""
        if os.path.exists(part_path):
            return os.path.getsize(part_path)
        return 0

    @staticmethod
    def _progress_path(part_path: str) -> str:
        return part_path + ".progress"

    @staticmethod
    def _legacy_chunk_paths(part_path: str, connections: int) -> list[str]:
        return [f"{part_path}.part{index}" for index in range(connections)]

    @staticmethod
    def _byte_ranges(total: int, connections: int) -> list[tuple[int, int]]:
        chunk_size = total // connections
        ranges: list[tuple[int, int]] = []
        for index in range(connections):
            start = index * chunk_size
            end = total - 1 if index == connections - 1 else (start + chunk_size - 1)
            ranges.append((start, end))
        return ranges

    def _load_progress(self, part_path: str) -> dict | None:
        path = self._progress_path(part_path)
        if not os.path.isfile(path):
            return None
        try:
            with open(path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, dict):
                return None
            if int(raw.get("version", 0)) != _PROGRESS_VERSION:
                return None
            return raw
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return None

    def _save_progress(
        self,
        part_path: str,
        total: int,
        connections: int,
        done: list[int],
    ) -> None:
        path = self._progress_path(part_path)
        payload = {
            "version": _PROGRESS_VERSION,
            "total": total,
            "connections": connections,
            "done": done,
        }
        with self._progress_disk_lock:
            tmp = f"{path}.{threading.get_ident()}.tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as handle:
                    json.dump(payload, handle)
                    handle.flush()
                    os.fsync(handle.fileno())
                for attempt in range(5):
                    try:
                        os.replace(tmp, path)
                        break
                    except OSError:
                        if attempt >= 4:
                            raise
                        time.sleep(0.05 * (attempt + 1))
            finally:
                try:
                    if os.path.isfile(tmp):
                        os.remove(tmp)
                except OSError:
                    pass

    def _multi_bytes_done(self, part_path: str, connections: int) -> int | None:
        """Total bytes finished across ranges, or None if not a multi partial."""
        progress = self._load_progress(part_path)
        if progress:
            done = progress.get("done")
            if isinstance(done, list) and len(done) == connections:
                return int(sum(int(x) for x in done))

        legacy = self._legacy_chunk_paths(part_path, connections)
        if any(os.path.isfile(path) for path in legacy):
            total_done = 0
            for path in legacy:
                if os.path.isfile(path):
                    total_done += os.path.getsize(path)
            return total_done
        return None

    def _multi_download_complete(
        self,
        part_path: str,
        total: int,
        connections: int,
    ) -> bool:
        ranges = self._byte_ranges(total, connections)
        progress = self._load_progress(part_path)
        if progress:
            done = progress.get("done")
            if not isinstance(done, list) or len(done) != connections:
                return False
            for index, (start, end) in enumerate(ranges):
                expected = end - start + 1
                if int(done[index]) < expected:
                    return False
            return os.path.isfile(part_path)

        legacy = self._legacy_chunk_paths(part_path, connections)
        if not any(os.path.isfile(path) for path in legacy):
            return False
        for index, (start, end) in enumerate(ranges):
            expected = end - start + 1
            chunk_path = legacy[index]
            actual = os.path.getsize(chunk_path) if os.path.isfile(chunk_path) else 0
            if actual < expected:
                return False
        return True

    def _ensure_multi_part_file(self, part_path: str, total: int) -> None:
        if os.path.isfile(part_path) and os.path.getsize(part_path) == total:
            return
        with open(part_path, "wb") as handle:
            if total > 0:
                handle.truncate(total)

    @staticmethod
    def _looks_uninitialized_part(part_path: str, total: int) -> bool:
        """True when a full-size `.part` is still empty (pre-allocated, no bytes yet)."""
        if total <= 0 or not os.path.isfile(part_path):
            return False
        if os.path.getsize(part_path) != total:
            return False
        sample = 65536
        with open(part_path, "rb") as handle:
            head = handle.read(sample)
            if any(head):
                return False
            if total > sample:
                handle.seek(max(0, total - sample))
                tail = handle.read(sample)
                if any(tail):
                    return False
        return True

    def _migrate_legacy_chunks(
        self,
        part_path: str,
        total: int,
        connections: int,
    ) -> list[int]:
        """Copy legacy `.part.partN` files into a pre-allocated `.part` and remove them."""
        ranges = self._byte_ranges(total, connections)
        legacy = self._legacy_chunk_paths(part_path, connections)
        done = [0] * connections
        if not any(os.path.isfile(path) for path in legacy):
            return done

        self._ensure_multi_part_file(part_path, total)
        buf = bytearray(1024 * 1024)
        with open(part_path, "r+b") as out:
            for index, (start, _end) in enumerate(ranges):
                chunk_path = legacy[index]
                if not os.path.isfile(chunk_path):
                    continue
                chunk_done = 0
                with open(chunk_path, "rb") as inp:
                    out.seek(start)
                    while True:
                        read = inp.readinto(buf)
                        if read <= 0:
                            break
                        out.write(buf[:read])
                        chunk_done += read
                done[index] = chunk_done
                try:
                    os.remove(chunk_path)
                except OSError:
                    pass
        return done

    @staticmethod
    def _content_length_from_probe(response: requests.Response) -> int:
        content_range = response.headers.get("Content-Range", "")
        if content_range and "/" in content_range:
            total_part = content_range.rsplit("/", 1)[-1].strip()
            if total_part.isdigit():
                return int(total_part)
        content_length = response.headers.get("Content-Length")
        if content_length and str(content_length).isdigit():
            return int(content_length)
        return 0

    @classmethod
    def _probe_download_url(cls, url: str, headers: dict[str, str]) -> requests.Response:
        """Some CDNs block HEAD (403) but allow ranged GET — probe size that way."""
        head = requests.head(url, headers=headers, timeout=30, allow_redirects=True)
        if head.status_code < 400:
            return head
        if head.status_code not in (403, 405):
            head.raise_for_status()

        probe_headers = dict(headers)
        probe_headers["Range"] = "bytes=0-0"
        probe = requests.get(
            url,
            headers=probe_headers,
            timeout=30,
            stream=True,
            allow_redirects=True,
        )
        probe.raise_for_status()
        return probe

    def _run(self, task: DownloadTask) -> None:
        try:
            task.state = DownloadState.CONNECTING
            self._notify_status(task, "Kumokonekta...")

            headers = {"User-Agent": self.USER_AGENT}
            probe = None
            for attempt in range(MAX_RETRIES + 1):
                try:
                    probe = self._probe_download_url(task.url, headers)
                    break
                except TRANSIENT_ERRORS:
                    if task._cancel_event.is_set() or attempt >= MAX_RETRIES:
                        raise
                    self._notify_status(
                        task,
                        f"Connecting failed, retrying ({attempt + 1}/{MAX_RETRIES})...",
                    )
                    self._wait_or_cancel(task, RETRY_BACKOFF * (attempt + 1))
                    if task._cancel_event.is_set():
                        return

            total = self._content_length_from_probe(probe)
            accept_ranges = probe.headers.get("Accept-Ranges", "").lower() == "bytes"
            if probe.status_code == 206:
                accept_ranges = True

            task.total_size = total
            os.makedirs(os.path.dirname(task.dest_path) or ".", exist_ok=True)

            part_path = task.dest_path + ".part"
            multi_done = (
                self._multi_bytes_done(part_path, task.connections)
                if total > 0 and task.connections > 1
                else None
            )
            resume_offset = self._prepare_resume(part_path)

            if multi_done is not None:
                with task._lock:
                    task.downloaded = multi_done
                if multi_done:
                    self._notify_status(task, f"Resuming from {multi_done:,} bytes...")
            elif resume_offset:
                with task._lock:
                    task.downloaded = resume_offset
                self._notify_status(task, f"Resuming from {resume_offset:,} bytes...")

            if total > 0 and self._multi_download_complete(part_path, total, task.connections):
                os.replace(part_path, task.dest_path)
                try:
                    os.remove(self._progress_path(part_path))
                except OSError:
                    pass
                task.state = DownloadState.COMPLETED
                task.downloaded = total
                self._notify_progress(task)
                self._notify_status(task, "Tapos na")
                return

            prealloc_shell = (
                task.connections > 1
                and total > 0
                and multi_done is None
                and not any(
                    os.path.isfile(path)
                    for path in self._legacy_chunk_paths(part_path, task.connections)
                )
                and self._looks_uninitialized_part(part_path, total)
            )

            if total > 0 and multi_done is None and resume_offset >= total and not prealloc_shell:
                os.replace(part_path, task.dest_path)
                task.state = DownloadState.COMPLETED
                task.downloaded = total
                self._notify_progress(task)
                self._notify_status(task, "Tapos na")
                return

            use_multi = (
                accept_ranges
                and total > 0
                and task.connections > 1
                and (multi_done is not None or resume_offset == 0 or prealloc_shell)
            )
            if not use_multi:
                self._single_connection_download(task, headers, part_path, resume_offset)
            else:
                self._multi_connection_download(task, headers, total, part_path)

            if task._cancel_event.is_set():
                return

            if os.path.exists(part_path):
                os.replace(part_path, task.dest_path)

            task.state = DownloadState.COMPLETED
            self._notify_progress(task)
            self._notify_status(task, "Download complete")

        except Exception as exc:
            if task._cancel_event.is_set():
                task.state = DownloadState.CANCELLED
            else:
                task.state = DownloadState.ERROR
                task.error = str(exc)
                self._notify_progress(task)
                self._notify_status(task, f"Error: {exc}")
                return
            self._notify_progress(task)

    def _single_connection_download(
        self,
        task: DownloadTask,
        headers: dict[str, str],
        part_path: str,
        resume_offset: int,
    ) -> None:
        task.state = DownloadState.DOWNLOADING
        self._notify_status(task, "Nagda-download...")

        offset = resume_offset
        last_tick = time.time()
        last_bytes = task.downloaded

        for attempt in range(MAX_RETRIES + 1):
            req_headers = dict(headers)
            mode = "ab" if offset else "wb"
            if offset:
                req_headers["Range"] = f"bytes={offset}-"

            try:
                with requests.get(
                    task.url,
                    headers=req_headers,
                    stream=True,
                    timeout=(30, 90),
                ) as response:
                    response.raise_for_status()
                    if task.total_size <= 0:
                        task.total_size = int(response.headers.get("Content-Length", 0))

                    with open(part_path, mode) as handle:
                        for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                            if task._cancel_event.is_set():
                                return
                            task._pause_event.wait()

                            if not chunk:
                                continue

                            handle.write(chunk)
                            offset += len(chunk)
                            with task._lock:
                                task.downloaded += len(chunk)

                            now = time.time()
                            if now - last_tick >= 0.25:
                                with task._lock:
                                    task.speed = (task.downloaded - last_bytes) / (now - last_tick)
                                last_bytes = task.downloaded
                                last_tick = now
                                self._notify_progress(task)
                return

            except TRANSIENT_ERRORS as exc:
                if task._cancel_event.is_set():
                    return
                offset = os.path.getsize(part_path) if os.path.exists(part_path) else offset
                if task.total_size > 0 and offset >= task.total_size:
                    return
                if attempt >= MAX_RETRIES:
                    raise
                wait = RETRY_BACKOFF * (attempt + 1)
                self._notify_status(
                    task,
                    f"Connection dropped, retrying ({attempt + 1}/{MAX_RETRIES}) in {wait:.0f}s...",
                )
                self._wait_or_cancel(task, wait)
                if task._cancel_event.is_set():
                    return

    def _multi_connection_download(
        self,
        task: DownloadTask,
        headers: dict[str, str],
        total: int,
        part_path: str,
    ) -> None:
        task.state = DownloadState.ALLOCATING
        self._notify_status(task, "Preparing download file...")
        self._notify_progress(task)

        ranges = self._byte_ranges(total, task.connections)
        done_list = self._migrate_legacy_chunks(part_path, total, task.connections)

        progress = self._load_progress(part_path)
        if progress and isinstance(progress.get("done"), list):
            saved = progress["done"]
            if len(saved) == task.connections:
                done_list = [min(int(saved[i]), ranges[i][1] - ranges[i][0] + 1) for i in range(task.connections)]

        self._ensure_multi_part_file(part_path, total)
        self._save_progress(part_path, total, task.connections, done_list)

        progress_map: dict[int, int] = {index: done_list[index] for index in range(task.connections)}
        with task._lock:
            task.downloaded = sum(progress_map.values())
            task.total_size = total

        task.state = DownloadState.DOWNLOADING
        self._notify_status(task, f"Downloading ({task.connections} connections)...")

        errors: list[Exception] = []

        def snapshot_done() -> list[int]:
            return [progress_map[i] for i in range(task.connections)]

        def worker(index: int, start: int, end: int) -> None:
            expected = end - start + 1
            done = min(progress_map[index], expected)

            for attempt in range(MAX_RETRIES + 1):
                if done >= expected:
                    return
                try:
                    req_headers = dict(headers)
                    req_headers["Range"] = f"bytes={start + done}-{end}"

                    with requests.get(
                        task.url,
                        headers=req_headers,
                        stream=True,
                        timeout=(30, 90),
                    ) as response:
                        response.raise_for_status()
                        with open(part_path, "r+b") as handle:
                            handle.seek(start + done)
                            for chunk in response.iter_content(chunk_size=CHUNK_SIZE):
                                if task._cancel_event.is_set():
                                    return
                                task._pause_event.wait()
                                if not chunk:
                                    continue
                                handle.write(chunk)
                                done += len(chunk)
                                progress_map[index] = done
                                with task._lock:
                                    task.downloaded = sum(progress_map.values())
                    return

                except TRANSIENT_ERRORS as exc:
                    if task._cancel_event.is_set():
                        return
                    progress_map[index] = min(done, expected)
                    with task._lock:
                        task.downloaded = sum(progress_map.values())
                    if done >= expected:
                        return
                    if attempt >= MAX_RETRIES:
                        errors.append(exc)
                        return
                    wait = RETRY_BACKOFF * (attempt + 1)
                    self._notify_status(
                        task,
                        f"Connection dropped on part {index + 1}, "
                        f"retrying ({attempt + 1}/{MAX_RETRIES})...",
                    )
                    self._wait_or_cancel(task, wait)
                    if task._cancel_event.is_set():
                        return
                except Exception as exc:
                    errors.append(exc)
                    return

        threads = [
            threading.Thread(
                target=worker,
                args=(index, start, end),
                daemon=True,
            )
            for index, (start, end) in enumerate(ranges)
        ]
        task._threads = threads
        for thread in threads:
            thread.start()

        last_tick = time.time()
        last_bytes = task.downloaded
        last_progress_save = 0.0
        while any(thread.is_alive() for thread in threads):
            if task._cancel_event.is_set():
                return
            now = time.time()
            if now - last_progress_save >= 2.0:
                self._save_progress(part_path, total, task.connections, snapshot_done())
                last_progress_save = now
            if now - last_tick >= 0.25:
                with task._lock:
                    task.speed = (task.downloaded - last_bytes) / (now - last_tick)
                last_bytes = task.downloaded
                last_tick = now
                self._notify_progress(task)
            time.sleep(0.05)

        for thread in threads:
            thread.join()

        if errors:
            raise errors[0]

        for index in range(task.connections):
            start, end = ranges[index]
            expected = end - start + 1
            if progress_map[index] < expected:
                raise IOError(
                    f"Download incomplete on part {index + 1}: "
                    f"expected {expected} bytes, got {progress_map[index]}"
                )

        self._save_progress(part_path, total, task.connections, snapshot_done())
        try:
            os.remove(self._progress_path(part_path))
        except OSError:
            pass

        with task._lock:
            task.downloaded = total
            task.speed = 0.0
        self._notify_progress(task)

    @staticmethod
    def guess_filename(url: str) -> str:
        path = urlparse(url).path
        name = unquote(path.rsplit("/", 1)[-1]) if path else "download.bin"
        return name or "download.bin"
