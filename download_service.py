"""Download orchestration — resolve URL, IDM download, extract, library, EXE picker."""

from __future__ import annotations

import os
import shutil
import subprocess
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from archive_utils import (
    archive_is_broken,
    archive_is_usable,
    extract_archive,
    format_bytes,
    is_download_archive_path,
)
from download_logger import DownloadLogger
from disk_space import disk_usage_for_path
from storage_requirements import evaluate_disk_space, space_error_message
from download_queue import DownloadQueueStore, QueuedDownload
from download_wait_queue import DownloadWaitQueueStore, WaitQueueItem
from pending_exe_store import PendingExeStore, StoredExePicker
from exe_scanner import display_name, launch_play_target, pick_best_exe, scan_executables
from idm_downloader import (
    DownloadState,
    DownloadTask,
    IDMDownloader,
    partial_bytes_for_dest,
    part_disk_bytes,
)
from device_fingerprint import get_device_fingerprint
from library_artwork import resolve_library_artwork, should_refresh_artwork
from library_manager import LibraryEntry, LibraryManager
from library_scan import ScanCandidate, scan_download_folder
from license_manager import LicenseRequiredError, build_registration_code
from playzip_api import GameResult
from settings_manager import SettingsManager
from store_manager import StoreManager
from trainer_service import TrainerManager

EventCallback = Callable[[str, dict[str, Any]], None]

_GATE_VERIFY_PHASES = frozenset({
    "waiting",
    "countdown",
    "turnstile",
    "turnstile_interactive",
    "turnstile_passed",
    "link_ready",
    "handoff",
    "processing",
    "ready",
    "fetching",
})

# Only one download pipeline (resolve → download → extract) at a time.
# Error/license states keep the slot until the user retries or cancels.
_SLOT_BUSY_PHASES = frozenset({
    "resolving", "rate_limit", "download", "extract", "error", "license_required",
})


@dataclass
class TaskView:
    task_id: str
    title: str
    game_id: str
    image_url: str
    state: str
    phase: str  # download | extract | done | error
    downloaded: int = 0
    total_size: int = 0
    speed: float = 0.0
    extract_current: int = 0
    extract_total: int = 100
    extract_message: str = ""
    prepare_current: int = 0
    prepare_total: int = 0
    disk_free: int = 0
    disk_total: int = 0
    status_message: str = ""
    rate_limit_seconds: int = 0
    verify_countdown: bool = False
    link_refresh: bool = False
    error: str = ""
    dest_path: str = ""
    install_dir: str = ""


@dataclass
class ExePickerPending:
    task_id: str
    title: str
    game_id: str
    image_url: str
    install_dir: str
    exes: list[str]
    default_exe: str
    entry_id: str = ""


@dataclass
class LibraryHealResult:
    auto_imported: int = 0
    merged_legacy: int = 0
    restored_backup: bool = False
    recovered_corrupt: bool = False
    artwork_queued: bool = False

    @property
    def changed(self) -> bool:
        return bool(
            self.auto_imported
            or self.merged_legacy
            or self.restored_backup
            or self.recovered_corrupt
        )


class DownloadService:
    def __init__(
        self,
        settings: SettingsManager,
        logger: DownloadLogger,
        store: StoreManager,
        on_event: EventCallback | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.root_dir = settings.download_dir
        self.logger = logger
        self.on_event = on_event
        self.library = LibraryManager(settings.library_path)
        self._last_library_heal: dict[str, Any] | None = None
        self._auto_heal_library(silent=True)

        self._lock = threading.Lock()
        self._tasks: dict[str, DownloadTask] = {}
        self._task_views: dict[str, TaskView] = {}
        self._task_meta: dict[str, GameResult] = {}
        self._downloaders: dict[str, IDMDownloader] = {}
        self._pending_exe: dict[str, ExePickerPending] = {}
        self._cancelled_pending: set[str] = set()
        self._refresh_in_flight: set[str] = set()
        self._recovery_hold: dict[str, QueuedDownload] = {}
        self._recovery_payloads: list[dict[str, Any]] = []
        self._load_pending_exe_from_disk()
        self._warm_library_cover_cache()
        self._warm_library_banner_cache()
        self.trainers = TrainerManager(logger=logger, on_event=self.emit)
        self.trainers.start()
        self._sync_trainers()

    @property
    def client(self):
        return self.store.get_client()

    def _pending_exe_store(self) -> PendingExeStore:
        return PendingExeStore(self.root_dir)

    def _save_pending_exe_to_disk(self) -> None:
        store = self._pending_exe_store()
        stored = {
            task_id: StoredExePicker(
                task_id=pending.task_id,
                title=pending.title,
                game_id=pending.game_id,
                image_url=pending.image_url,
                install_dir=pending.install_dir,
                exes=pending.exes,
                default_exe=pending.default_exe,
                entry_id=pending.entry_id,
            )
            for task_id, pending in self._pending_exe.items()
        }
        store.save_all(stored)

    def _load_pending_exe_from_disk(self) -> None:
        for task_id, stored in self._pending_exe_store().load().items():
            if not os.path.isdir(stored.install_dir):
                self._pending_exe_store().remove(task_id)
                continue
            exes = scan_executables(stored.install_dir) or stored.exes
            self._pending_exe[task_id] = ExePickerPending(
                task_id=stored.task_id,
                title=stored.title,
                game_id=stored.game_id,
                image_url=stored.image_url,
                install_dir=stored.install_dir,
                exes=exes,
                default_exe=pick_best_exe(exes) if exes else stored.default_exe,
                entry_id=stored.entry_id,
            )
        if self._pending_exe:
            self.logger.info(f"Restored {len(self._pending_exe)} pending EXE picker(s)")
        self._reconcile_pending_exe_with_library()

    def _queue_store(self) -> DownloadQueueStore:
        return DownloadQueueStore(self.root_dir)

    def _wait_queue_store(self) -> DownloadWaitQueueStore:
        return DownloadWaitQueueStore(self.root_dir)

    def _view_holds_slot(self, view: TaskView) -> bool:
        """Paused downloads free the slot so the next queued game can start."""
        if view.phase not in _SLOT_BUSY_PHASES:
            return False
        if view.phase == "download" and view.state == DownloadState.PAUSED.value:
            return False
        return True

    def _is_slot_busy(self) -> bool:
        with self._lock:
            return any(self._view_holds_slot(view) for view in self._task_views.values())

    def _queue_status_message(self, position: int) -> str:
        if position <= 1:
            return "Waiting in queue — next up"
        return f"Waiting in queue (#{position})"

    def _refresh_queue_positions(self) -> None:
        for index, item in enumerate(self._wait_queue_store().load(), start=1):
            with self._lock:
                view = self._task_views.get(item.task_id)
                if not view or view.phase != "queued":
                    continue
                view.status_message = self._queue_status_message(index)
                payload = asdict(view)
                payload["queue_position"] = index
                self.emit("task_update", payload)

    def _dest_path_for_task(self, task_id: str, view: TaskView) -> str:
        if view.dest_path:
            return view.dest_path
        for item in self._wait_queue_store().load():
            if item.task_id == task_id and item.dest_path:
                return item.dest_path
        for item in self._queue_store().load():
            if item.game_id == view.game_id and item.dest_path:
                return item.dest_path
        with self._lock:
            task = self._tasks.get(task_id)
            if task and task.dest_path:
                return task.dest_path
        return ""

    def _purge_download_persistence(
        self,
        *,
        game_id: str,
        task_id: str = "",
        dest_path: str = "",
    ) -> list[str]:
        """Remove persisted wait/resume records for a cancelled download."""
        wait_store = self._wait_queue_store()
        if task_id:
            wait_store.remove(task_id)
        wait_store.remove_by_game_id(game_id)

        cleanup: list[str] = []
        if dest_path:
            cleanup.extend(self._queue_store().remove_by_game_id(game_id))
            self._queue_store().remove(dest_path)
        else:
            cleanup.extend(self._queue_store().remove_by_game_id(game_id))

        seen: set[str] = set()
        result: list[str] = []
        for path in cleanup:
            if not path:
                continue
            norm = os.path.normpath(path)
            if norm in seen:
                continue
            seen.add(norm)
            result.append(norm)
        if dest_path:
            norm = os.path.normpath(dest_path)
            if norm not in seen:
                result.append(norm)
        return result

    def _enqueue_for_later(
        self,
        game: GameResult,
        task_id: str,
        *,
        dest_path: str = "",
        connections: int = 0,
    ) -> int:
        wait_store = self._wait_queue_store()
        wait_store.append(
            WaitQueueItem(
                game_id=game.game_id,
                title=game.title,
                image_url=game.image_url,
                task_id=task_id,
                dest_path=dest_path,
                connections=connections,
            )
        )
        position = len(wait_store.load())
        view = TaskView(
            task_id=task_id,
            title=game.title,
            game_id=game.game_id,
            image_url=game.image_url,
            state="Waiting",
            phase="queued",
            status_message=self._queue_status_message(position),
            dest_path=dest_path,
        )
        with self._lock:
            self._task_views[task_id] = view
        payload = asdict(view)
        payload["queue_position"] = position
        self.emit("task_update", payload)
        self.logger.info(
            f"[{game.title}] Added to download queue (position {position})"
        )
        return position

    def _start_resolve_thread(
        self,
        pending_id: str,
        game: GameResult,
        *,
        force_dest: str | None = None,
        force_connections: int | None = None,
        status_message: str = "Resolving download link...",
        partial_bytes: int = 0,
        partial_total: int = 0,
    ) -> None:
        pending_view = TaskView(
            task_id=pending_id,
            title=game.title,
            game_id=game.game_id,
            image_url=game.image_url,
            state=DownloadState.CONNECTING.value,
            phase="resolving",
            status_message=status_message,
            downloaded=partial_bytes,
            total_size=partial_total,
            dest_path=force_dest or "",
        )
        with self._lock:
            self._task_views[pending_id] = pending_view
            self._cancelled_pending.discard(pending_id)
        self.emit("task_update", asdict(pending_view))

        threading.Thread(
            target=self._resolve_and_queue,
            args=(pending_id, game),
            kwargs={
                "force_dest": force_dest,
                "force_connections": force_connections,
            },
            daemon=True,
        ).start()

    def _advance_queue(self) -> None:
        with self._lock:
            if any(self._view_holds_slot(view) for view in self._task_views.values()):
                return

        item = self._wait_queue_store().pop_first()
        if not item:
            return

        with self._lock:
            if any(self._view_holds_slot(view) for view in self._task_views.values()):
                items = self._wait_queue_store().load()
                self._wait_queue_store().save_all([item] + items)
                return
            self._task_views.pop(item.task_id, None)

        self.emit("task_update", {"task_id": item.task_id, "removed": True})
        self._refresh_queue_positions()

        game = GameResult(
            game_id=item.game_id,
            title=item.title,
            image_url=item.image_url,
        )
        pending_id = f"pending-{item.game_id}-{uuid.uuid4().hex[:8]}"
        if item.dest_path:
            self.logger.info(f"[{item.title}] Starting queued resume from wait list")
            self._start_resolve_thread(
                pending_id,
                game,
                force_dest=item.dest_path or None,
                force_connections=item.connections or None,
                status_message="Resuming — getting fresh download link...",
            )
        else:
            self.logger.info(f"[{item.title}] Starting queued download from wait list")
            self._start_resolve_thread(pending_id, game)

    def resume_pending_downloads(self) -> None:
        import glob

        wait_store = self._wait_queue_store()
        wait_items = wait_store.load()
        if wait_items:
            self.logger.info(f"Restored {len(wait_items)} queued download(s)")
            for index, item in enumerate(wait_items, start=1):
                view = TaskView(
                    task_id=item.task_id,
                    title=item.title,
                    game_id=item.game_id,
                    image_url=item.image_url,
                    state="Waiting",
                    phase="queued",
                    status_message=self._queue_status_message(index),
                    dest_path=item.dest_path,
                )
                with self._lock:
                    self._task_views[item.task_id] = view
                payload = asdict(view)
                payload["queue_position"] = index
                self.emit("task_update", payload)

        store = self._queue_store()
        pending = store.load()
        if not pending:
            self._sweep_download_folder_residue()
            self._advance_queue()
            return

        self.logger.info(f"Checking {len(pending)} saved download(s) for resume...")
        to_resume: list[QueuedDownload] = []
        recovery_items: list[QueuedDownload] = []
        for item in pending:
            part_path = item.dest_path + ".part"
            chunk_files = glob.glob(glob.escape(part_path) + ".part*")
            progress_path = part_path + ".progress"
            single_partial = os.path.exists(part_path)
            has_partial = single_partial or bool(chunk_files) or os.path.isfile(progress_path)

            # Treat the download as finished ONLY when the final file exists
            # AND no partial fragments remain on disk. A stale or previously
            # completed file must never cancel a resume while partial chunks
            # are still present (os.replace overwrites it after the merge).
            if os.path.isfile(item.dest_path) and not has_partial:
                store.remove(item.dest_path)
                if archive_is_usable(item.dest_path):
                    self.logger.info(
                        f"[{item.title}] Complete archive on disk — continuing with extraction"
                    )
                    game = GameResult(
                        game_id=item.game_id,
                        title=item.title,
                        image_url=item.image_url,
                    )
                    self._start_extraction_for_game(game, item.dest_path)
                else:
                    try:
                        os.remove(item.dest_path)
                        self.logger.info(
                            f"[{item.title}] Removed incomplete download archive — will re-download"
                        )
                    except OSError as exc:
                        self.logger.warn(
                            f"[{item.title}] Could not remove bad archive: {exc}"
                        )
                    to_resume.append(item)
                continue

            if has_partial:
                recovery_items.append(item)
            else:
                to_resume.append(item)

        if recovery_items:
            payloads = [self._recovery_payload(item) for item in recovery_items]
            with self._lock:
                self._recovery_hold = {item.dest_path: item for item in recovery_items}
                self._recovery_payloads = payloads
            self.emit("download_recovery", {"items": payloads})
            self.logger.info(
                f"Found {len(recovery_items)} interrupted download(s) — waiting for Resume or Delete"
            )

        if not to_resume and not recovery_items:
            self._sweep_download_folder_residue()
            self._advance_queue()
            return

        if not to_resume:
            self._advance_queue()
            return

        first, *rest = to_resume
        for extra in rest:
            task_id = f"wait-{extra.game_id}-{uuid.uuid4().hex[:8]}"
            self._enqueue_for_later(
                GameResult(
                    game_id=extra.game_id,
                    title=extra.title,
                    image_url=extra.image_url,
                ),
                task_id,
                dest_path=extra.dest_path,
                connections=extra.connections,
            )

        self._begin_resume_queued_item(first)

        self._sweep_download_folder_residue()

    def apply_settings(self) -> dict[str, Any]:
        """Reload paths after settings change."""
        self.root_dir = self.settings.download_dir
        self.library = LibraryManager(self.settings.library_path)
        heal = self._auto_heal_library(silent=True)
        self._last_library_heal = {
            **asdict(heal),
            "entry_count": len(self.library.entries),
        }
        self.store.set_store(self.settings.store, persist=False)
        self._pending_exe.clear()
        self._load_pending_exe_from_disk()
        self.emit("library_updated", {})
        self.emit("settings_updated", self.settings.to_dict())
        self._sync_trainers()
        return self.settings.to_dict()

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.on_event:
            self.on_event(event_type, payload)

    def _on_rate_limit_tick(self, seconds: int, label: str) -> None:
        with self._lock:
            for view in self._task_views.values():
                if view.title == label and view.phase in ("resolving", "rate_limit"):
                    view.rate_limit_seconds = max(0, seconds)
                    view.verify_countdown = False
                    if seconds > 0:
                        view.status_message = f"Queued — download in {seconds}s"
                    else:
                        view.status_message = "Resolving download link..."
                    self.emit("task_update", asdict(view))
                    break

    def _find_resolving_view(self, label: str) -> TaskView | None:
        label = (label or "").strip()
        if label:
            for view in self._task_views.values():
                if view.phase in ("resolving", "rate_limit") and view.title == label:
                    return view
        resolving = [
            view for view in self._task_views.values() if view.phase == "resolving"
        ]
        if len(resolving) == 1:
            return resolving[0]
        return None

    def _on_resolve_status_tick(self, label: str, message: str) -> None:
        text = (message or "").strip()
        if not text:
            return
        with self._lock:
            view = self._find_resolving_view(label)
            if view is None:
                return
            view.state = DownloadState.CONNECTING.value
            view.speed = 0.0
            view.status_message = text
            self.emit("task_update", asdict(view))

    def on_gate_verify_progress(self, payload: dict[str, Any]) -> None:
        """Sync Server 2 gate page countdown/status onto the download task UI."""
        label = str(payload.get("label") or "").strip()
        countdown = max(0, int(payload.get("countdown") or 0))
        phase = str(payload.get("phase") or "waiting").strip()
        message = str(payload.get("status_message") or "Preparing download verification...").strip()
        with self._lock:
            view = None
            if label:
                for candidate in self._task_views.values():
                    if candidate.phase == "resolving" and candidate.title == label:
                        view = candidate
                        break
            if view is None:
                resolving = [
                    v for v in self._task_views.values() if v.phase == "resolving"
                ]
                if len(resolving) == 1:
                    view = resolving[0]
            if view is None:
                return
            view.verify_countdown = phase in _GATE_VERIFY_PHASES
            view.rate_limit_seconds = countdown
            view.status_message = message
            self.emit("task_update", asdict(view))

    def _wait_countdown(self, task_id: str, seconds: int, prefix: str) -> bool:
        """Tick countdown on task view. Returns False if cancelled."""
        remaining = max(0, int(seconds))
        while remaining > 0:
            with self._lock:
                if task_id in self._cancelled_pending:
                    return False
                view = self._task_views.get(task_id)
                if not view:
                    return False
                view.rate_limit_seconds = remaining
                view.status_message = f"{prefix} {remaining}s"
                self.emit("task_update", asdict(view))
            time.sleep(1)
            remaining -= 1
        return task_id not in self._cancelled_pending

    def _resolve_and_queue(
        self,
        pending_id: str,
        game: GameResult,
        force_dest: str | None = None,
        force_connections: int | None = None,
    ) -> None:
        title = game.title
        max_rate_limit_retries = 8
        rate_limit_attempts = 0
        while pending_id not in self._cancelled_pending:
            try:
                url = self.client.get_download_url(game.game_id, title=title)
                if force_dest:
                    # Resume: keep the original destination so the existing
                    # partial (.part / .partN) files are picked up.
                    dest = force_dest
                    self.logger.info(f"[{title}] Resuming with fresh link")
                else:
                    filename = self.client.filename_from_url(url, fallback=f"{title}.bin")
                    dest = os.path.join(self.root_dir, filename)
                    self.logger.info(f"[{title}] Saving as {filename}")
                with self._lock:
                    if pending_id in self._cancelled_pending:
                        return
                    self._task_views.pop(pending_id, None)
                self._queue_download(game, url, dest, connections=force_connections)
                return
            except LicenseRequiredError as exc:
                code = build_registration_code()
                device_fp = get_device_fingerprint()
                is_abnormal = exc.custom_os
                if is_abnormal:
                    user_msg = (
                        "Custom OS detected — shared hardware ID on this device. "
                        "Send your registration code AND device fingerprint to the "
                        "QuickPlay seller for manual activation."
                    )
                else:
                    user_msg = str(exc)
                self.logger.warn(f"[{title}] License required before download")
                with self._lock:
                    view = self._task_views.get(pending_id)
                    if view:
                        view.phase = "license_required"
                        view.state = DownloadState.ERROR.value
                        view.error = user_msg
                        view.rate_limit_seconds = 0
                        view.status_message = "Activation required"
                        payload = asdict(view)
                        self.emit("task_update", payload)
                self.emit(
                    "license_required",
                    {
                        "task_id": pending_id,
                        "title": title,
                        "game_id": game.game_id,
                        "registration_code": code,
                        "device_fingerprint": device_fp,
                        "is_abnormal_hwid": is_abnormal,
                        "message": user_msg,
                    },
                )
                return
            except Exception as exc:
                user_msg, wait_seconds = self._parse_download_error(exc)
                if wait_seconds:
                    rate_limit_attempts += 1
                    if rate_limit_attempts > max_rate_limit_retries:
                        self.logger.error(
                            f"[{title}] Still rate limited after "
                            f"{max_rate_limit_retries} tries — stopping."
                        )
                        with self._lock:
                            view = self._task_views.get(pending_id)
                            if view:
                                view.phase = "error"
                                view.state = DownloadState.ERROR.value
                                view.error = (
                                    "Rate limited too many times. Press Retry later."
                                )
                                view.rate_limit_seconds = 0
                                self.emit("task_update", asdict(view))
                        return
                    self.logger.warn(f"[{title}] Rate limit — retry in {wait_seconds}s")
                    with self._lock:
                        view = self._task_views.get(pending_id)
                        if not view:
                            return
                        view.phase = "rate_limit"
                        view.state = "Waiting"
                        view.error = ""
                        view.rate_limit_seconds = wait_seconds
                        view.status_message = f"Rate limit — retry in {wait_seconds}s"
                        self.emit("task_update", asdict(view))
                    if not self._wait_countdown(
                        pending_id, wait_seconds, "Rate limit — retry in"
                    ):
                        return
                    with self._lock:
                        view = self._task_views.get(pending_id)
                        if not view:
                            return
                        view.phase = "resolving"
                        view.state = DownloadState.CONNECTING.value
                        view.rate_limit_seconds = 0
                        view.status_message = "Retrying download link..."
                        self.emit("task_update", asdict(view))
                    continue

                self.logger.error(f"[{title}] Failed to get download link: {user_msg}")
                with self._lock:
                    view = self._task_views.get(pending_id)
                    if view:
                        if force_dest:
                            view.phase = "download"
                            view.state = DownloadState.ERROR.value
                            view.error = user_msg
                            view.status_message = user_msg
                            view.link_refresh = False
                            view.speed = 0.0
                        else:
                            view.phase = "error"
                            view.state = DownloadState.ERROR.value
                            view.error = user_msg
                            view.status_message = user_msg
                        view.rate_limit_seconds = 0
                        view.verify_countdown = False
                        self.emit("task_update", asdict(view))
                return

    def _parse_download_error(self, exc: Exception) -> tuple[str, int | None]:
        message = str(exc)
        if message.startswith("RATE_LIMIT:"):
            parts = message.split(":", 2)
            try:
                wait = int(parts[1])
            except (IndexError, ValueError):
                wait = 125
            user_msg = parts[2] if len(parts) > 2 else (
                f"May download limit. Hintayin {wait} segundo bago subukan ulit."
            )
            return user_msg, wait
        return message, None

    def list_tasks(self) -> list[dict[str, Any]]:
        wait_positions = {
            item.task_id: index
            for index, item in enumerate(self._wait_queue_store().load(), start=1)
        }
        with self._lock:
            rows: list[dict[str, Any]] = []
            for view in self._task_views.values():
                payload = asdict(view)
                if view.phase == "queued":
                    payload["queue_position"] = wait_positions.get(view.task_id, 0)
                rows.append(payload)
            return rows

    def get_pending_exe(self, task_id: str) -> dict[str, Any] | None:
        pending = self._pending_exe.get(task_id)
        if not pending:
            return None
        return {
            "task_id": pending.task_id,
            "entry_id": pending.entry_id,
            "title": pending.title,
            "install_dir": pending.install_dir,
            "image_url": pending.image_url,
            "exes": [
                {"path": p, "label": display_name(p, pending.install_dir)}
                for p in pending.exes
            ],
            "default_exe": pending.default_exe,
        }

    def list_pending_exe(self) -> list[str]:
        return list(self._pending_exe.keys())

    def _fetch_store_size_bytes(
        self,
        game_id: str,
        title: str,
        image_url: str = "",
    ) -> int | None:
        try:
            details = self.store.create_details_service(self.logger).fetch(
                game_id,
                title=title,
                image_url=image_url,
            )
            return details.store_size_bytes
        except Exception:
            return None

    def check_download_space(
        self,
        game_id: str,
        title: str,
        image_url: str = "",
        store_size_bytes: int | None = None,
    ) -> dict[str, Any]:
        estimated = store_size_bytes or self._fetch_store_size_bytes(
            game_id, title, image_url
        )
        usage = disk_usage_for_path(self.root_dir)
        check = evaluate_disk_space(int(usage["free_bytes"]), estimated)
        check["download_dir"] = usage["path"]
        check["message"] = space_error_message(check, title=title)
        return check

    def start_download(
        self,
        game_id: str,
        title: str,
        image_url: str = "",
        store_size_bytes: int | None = None,
    ) -> dict[str, Any]:
        game = GameResult(game_id=game_id, title=title, image_url=image_url)
        self.logger.info(f"Download requested: {title} (id={game_id})")

        space = self.check_download_space(
            game_id,
            title,
            image_url,
            store_size_bytes=store_size_bytes,
        )
        if not space.get("ok") and not space.get("unknown_size"):
            self.logger.warn(f"[{title}] Blocked — insufficient disk space")
            return {
                "ok": False,
                "error": "insufficient_disk_space",
                **space,
            }

        active_download_phases = {"resolving", "rate_limit", "download", "extract", "queued"}
        with self._lock:
            for view in self._task_views.values():
                if view.game_id == game_id and view.phase in active_download_phases:
                    return {
                        "ok": True,
                        "queued": True,
                        "already_queued": True,
                        "message": f"{title} is already in progress or queued.",
                        "rate_limit_seconds": view.rate_limit_seconds,
                    }
            already_pending_exe = any(
                p.game_id == game_id for p in self._pending_exe.values()
            )
        if already_pending_exe:
            return {
                "ok": True,
                "message": f"{title} finished — choose the PLAY executable.",
                "rate_limit_seconds": 0,
            }
        if self._wait_queue_store().contains_game(game_id):
            return {
                "ok": True,
                "queued": True,
                "already_queued": True,
                "message": f"{title} is already in the download queue.",
                "rate_limit_seconds": 0,
            }

        pending_id = f"pending-{game_id}-{uuid.uuid4().hex[:8]}"
        with self._lock:
            if any(self._view_holds_slot(view) for view in self._task_views.values()):
                task_id = f"wait-{game_id}-{uuid.uuid4().hex[:8]}"
                position = len(self._wait_queue_store().load()) + 1
                wait_store = self._wait_queue_store()
                wait_store.append(
                    WaitQueueItem(
                        game_id=game.game_id,
                        title=game.title,
                        image_url=game.image_url,
                        task_id=task_id,
                    )
                )
                view = TaskView(
                    task_id=task_id,
                    title=game.title,
                    game_id=game.game_id,
                    image_url=game.image_url,
                    state="Waiting",
                    phase="queued",
                    status_message=self._queue_status_message(position),
                )
                self._task_views[task_id] = view
                payload = asdict(view)
                payload["queue_position"] = position
                self.emit("task_update", payload)
                self.logger.info(
                    f"[{game.title}] Added to download queue (position {position})"
                )
                return {
                    "ok": True,
                    "queued": True,
                    "queue_position": position,
                    "title": title,
                    "message": f"Queued at position {position}: {title}",
                    "rate_limit_seconds": 0,
                }

            pending_view = TaskView(
                task_id=pending_id,
                title=game.title,
                game_id=game.game_id,
                image_url=game.image_url,
                state=DownloadState.CONNECTING.value,
                phase="resolving",
                status_message="Resolving download link...",
            )
            self._task_views[pending_id] = pending_view
            self._cancelled_pending.discard(pending_id)

        self.emit("task_update", asdict(pending_view))
        threading.Thread(
            target=self._resolve_and_queue,
            args=(pending_id, game),
            daemon=True,
        ).start()
        remaining = self.client.rate_limit_remaining()
        if remaining > 0:
            with self._lock:
                view = self._task_views.get(pending_id)
                if view:
                    view.rate_limit_seconds = remaining
                    view.status_message = f"Queued — download in {remaining}s"
                    self.emit("task_update", asdict(view))
        return {
            "ok": True,
            "queued": False,
            "started": True,
            "title": title,
            "message": f"Starting download for {title}",
            "rate_limit_seconds": remaining,
        }

    def _queue_download(
        self,
        game: GameResult,
        url: str,
        dest_path: str,
        connections: int | None = None,
    ) -> None:
        task_id = dest_path
        active = {
            DownloadState.QUEUED,
            DownloadState.CONNECTING,
            DownloadState.ALLOCATING,
            DownloadState.DOWNLOADING,
            DownloadState.MERGING,
            DownloadState.PAUSED,
        }
        with self._lock:
            if task_id in self._tasks and self._tasks[task_id].state in active:
                return

        conn = connections or self.settings.connections
        task = DownloadTask(
            url=url,
            dest_path=dest_path,
            connections=conn,
        )
        view = TaskView(
            task_id=task_id,
            title=game.title,
            game_id=game.game_id,
            image_url=game.image_url,
            state=task.state.value,
            phase="download",
            dest_path=dest_path,
            link_refresh=False,
        )
        with self._lock:
            self._tasks[task_id] = task
            self._task_views[task_id] = view
            self._task_meta[task_id] = game

        self._queue_store().upsert(
            QueuedDownload(
                game_id=game.game_id,
                title=game.title,
                image_url=game.image_url,
                url=url,
                dest_path=dest_path,
                connections=conn,
            )
        )

        self.emit("task_update", asdict(view))
        self.logger.info(f"[{game.title}] Download started → {dest_path}")

        def on_progress(downloaded: int, total: int, speed: float, state: str) -> None:
            with self._lock:
                v = self._task_views.get(task_id)
                if not v:
                    return
                # Stale progress ticks must not overwrite an intentional pause/cancel.
                if v.phase in ("resolving", "rate_limit"):
                    return
                if v.state == DownloadState.PAUSED.value and state != DownloadState.PAUSED.value:
                    v.downloaded = downloaded
                    v.total_size = total
                    v.speed = 0.0
                    payload = asdict(v)
                    self.emit("task_update", payload)
                    return
                if v.state == DownloadState.CANCELLED.value:
                    return
                v.downloaded = downloaded
                v.total_size = total
                v.speed = speed
                v.state = state
                if state == DownloadState.ERROR.value:
                    v.speed = 0.0
                if state == DownloadState.DOWNLOADING.value:
                    v.prepare_current = 0
                    v.prepare_total = 0
                payload = asdict(v)
            self.emit("task_update", payload)
            if state == DownloadState.COMPLETED.value:
                self.logger.info(f"[{game.title}] Download finished")
                self._on_download_complete(task_id)

        def on_status(message: str) -> None:
            self.logger.info(f"[{game.title}] {message}")
            with self._lock:
                v = self._task_views.get(task_id)
                if not v:
                    return
                if v.phase in ("resolving", "rate_limit"):
                    return
                v.status_message = message
                if message in {s.value for s in DownloadState}:
                    v.state = message
                    if message == DownloadState.PAUSED.value:
                        v.speed = 0.0
                if message == DownloadState.CANCELLED.value:
                    return
                if v.state == DownloadState.DOWNLOADING.value:
                    v.prepare_current = 0
                    v.prepare_total = 0
                payload = asdict(v)
            self.emit("task_update", payload)

        def on_prepare(allocated: int, total: int, free_bytes: int, disk_total: int) -> None:
            with self._lock:
                v = self._task_views.get(task_id)
                if not v or v.phase in ("resolving", "rate_limit"):
                    return
                if v.state in (DownloadState.CANCELLED.value, DownloadState.PAUSED.value):
                    return
                v.prepare_current = allocated
                v.prepare_total = total
                v.disk_free = free_bytes
                v.disk_total = disk_total
                if total > 0 and v.total_size <= 0:
                    v.total_size = total
                payload = asdict(v)
            self.emit("task_update", payload)

        downloader = IDMDownloader(
            on_progress=on_progress,
            on_status=on_status,
            on_prepare=on_prepare,
        )
        with self._lock:
            self._downloaders[task_id] = downloader
        downloader.start(task)

    def retry_download(self, task_id: str) -> dict[str, Any]:
        with self._lock:
            view = self._task_views.get(task_id)
            if not view or view.phase not in ("rate_limit", "error", "resolving"):
                return {"ok": False, "error": "Nothing to retry for this task."}
            game = GameResult(
                game_id=view.game_id,
                title=view.title,
                image_url=view.image_url,
            )
            view.phase = "resolving"
            view.state = DownloadState.CONNECTING.value
            view.error = ""
            view.rate_limit_seconds = 0
            view.status_message = "Retrying download link..."
            self._cancelled_pending.discard(task_id)
            self.emit("task_update", asdict(view))

        threading.Thread(
            target=self._resolve_and_queue,
            args=(task_id, game),
            daemon=True,
        ).start()
        return {"ok": True}

    def _connections_for_dest(self, dest_path: str) -> int:
        for item in self._queue_store().load():
            if item.dest_path == dest_path:
                return max(1, int(item.connections))
        return self.settings.connections

    def refresh_download_link(self, task_id: str) -> dict[str, Any]:
        """Fetch a new CDN URL and restart the download, keeping partial files."""
        with self._lock:
            if task_id in self._refresh_in_flight:
                return {"ok": False, "error": "Refresh already in progress."}
            view = self._task_views.get(task_id)
            task = self._tasks.get(task_id)
            downloader = self._downloaders.get(task_id)
            game = self._task_meta.get(task_id)

        if not view:
            return {"ok": False, "error": "Task not found."}
        if view.phase != "download":
            return {"ok": False, "error": "Only active downloads can refresh the link."}

        refreshable_states = {
            DownloadState.QUEUED.value,
            DownloadState.CONNECTING.value,
            DownloadState.ALLOCATING.value,
            DownloadState.DOWNLOADING.value,
            DownloadState.MERGING.value,
            DownloadState.PAUSED.value,
            DownloadState.ERROR.value,
        }
        if view.state not in refreshable_states:
            return {"ok": False, "error": "Cannot refresh link in this state."}

        dest = (view.dest_path or task_id).strip()
        if not dest:
            return {"ok": False, "error": "Missing destination path."}

        connections = task.connections if task else self._connections_for_dest(dest)
        if not game:
            game = GameResult(
                game_id=view.game_id,
                title=view.title,
                image_url=view.image_url,
            )

        if downloader and task:
            downloader.cancel(task)

        with self._lock:
            self._refresh_in_flight.add(task_id)
            self._tasks.pop(task_id, None)
            self._downloaders.pop(task_id, None)
            self._cancelled_pending.discard(task_id)
            v = self._task_views.get(task_id)
            if v:
                v.phase = "resolving"
                v.state = DownloadState.CONNECTING.value
                v.error = ""
                v.speed = 0.0
                v.rate_limit_seconds = 0
                v.verify_countdown = False
                v.link_refresh = True
                v.status_message = "Refreshing download link — please wait..."
                self.emit("task_update", asdict(v))

        def run_refresh() -> None:
            try:
                self._resolve_and_queue(
                    task_id,
                    game,
                    force_dest=dest,
                    force_connections=connections,
                )
            finally:
                with self._lock:
                    self._refresh_in_flight.discard(task_id)

        threading.Thread(target=run_refresh, daemon=True).start()
        self.logger.info(
            f"[{view.title}] User requested fresh download link (keeping partial files)"
        )
        return {"ok": True}

    def pause_task(self, task_id: str, *, advance: bool = True) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            downloader = self._downloaders.get(task_id)
            view = self._task_views.get(task_id)
        if not task or not downloader or not view:
            return False
        if view.state == DownloadState.PAUSED.value:
            return True
        downloader.pause(task)
        with self._lock:
            v = self._task_views.get(task_id)
            if v:
                v.state = DownloadState.PAUSED.value
                v.status_message = "Paused — next in queue can start"
                v.speed = 0.0
                self.emit("task_update", asdict(v))
        self.logger.info(f"[{view.title}] Download paused")
        if advance:
            self._advance_queue()
        return True

    def resume_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            downloader = self._downloaders.get(task_id)
            view = self._task_views.get(task_id)
            steal: list[str] = []
            blocked = False
            for oid, ov in self._task_views.items():
                if oid == task_id or not self._view_holds_slot(ov):
                    continue
                if ov.phase == "download":
                    steal.append(oid)
                else:
                    blocked = True
                    break
        if not task or not downloader or not view:
            return False
        if blocked:
            self.logger.warn(
                f"[{view.title}] Resume skipped — another download is still resolving or extracting"
            )
            return False
        for other_id in steal:
            self.pause_task(other_id, advance=False)
        downloader.resume(task)
        with self._lock:
            v = self._task_views.get(task_id)
            if v:
                v.state = DownloadState.DOWNLOADING.value
                v.status_message = DownloadState.DOWNLOADING.value
                self.emit("task_update", asdict(v))
        self.logger.info(f"[{view.title}] Download resumed")
        return True

    def cancel_task(self, task_id: str) -> bool:
        with self._lock:
            task = self._tasks.get(task_id)
            downloader = self._downloaders.get(task_id)
            pending_view = self._task_views.get(task_id)
            game = self._task_meta.get(task_id)

        if not pending_view:
            return False

        title = pending_view.title

        if pending_view.phase == "queued":
            dest_path = self._dest_path_for_task(task_id, pending_view)
            cleanup_paths = self._purge_download_persistence(
                game_id=pending_view.game_id,
                task_id=task_id,
                dest_path=dest_path,
            )
            for path in cleanup_paths:
                self._schedule_part_cleanup(path, remove_final=True)
            self.logger.info(f"[{title}] Removed from download queue")
            self._remove_task(task_id, advance=False)
            self._refresh_queue_positions()
            return True

        if not task or not downloader:
            if pending_view.phase in ("resolving", "rate_limit", "error"):
                with self._lock:
                    self._cancelled_pending.add(task_id)
                try:
                    from anker.gate_bridge import abort_pending_gate

                    abort_pending_gate("Download verification cancelled.")
                except Exception:
                    pass
                dest_path = self._dest_path_for_task(task_id, pending_view)
                cleanup_paths = self._purge_download_persistence(
                    game_id=pending_view.game_id,
                    task_id=task_id,
                    dest_path=dest_path,
                )
                for path in cleanup_paths:
                    self._schedule_part_cleanup(path, remove_final=True)
                self.logger.info(f"[{title}] Download cancelled by user (pending)")
                self._remove_task(task_id)
                return True
            if pending_view.phase in ("download", "extract"):
                dest_path = self._dest_path_for_task(task_id, pending_view)
                cleanup_paths = self._purge_download_persistence(
                    game_id=pending_view.game_id,
                    task_id=task_id,
                    dest_path=dest_path,
                )
                for path in cleanup_paths:
                    self._schedule_part_cleanup(path, remove_final=True)
                self.logger.info(f"[{title}] Download cancelled by user (cleanup)")
                self._remove_task(task_id)
                return True
            return False

        downloader.cancel(task)
        cleanup_paths = self._purge_download_persistence(
            game_id=pending_view.game_id,
            task_id=task_id,
            dest_path=task.dest_path,
        )
        for path in cleanup_paths:
            self._schedule_part_cleanup(path, remove_final=True)
        self.logger.info(f"[{title}] Download cancelled by user")
        self._remove_task(task_id)
        return True

    def _schedule_part_cleanup(self, dest_path: str, *, remove_final: bool = False) -> None:
        if not dest_path:
            return
        self._cleanup_part_files(dest_path)

        def worker() -> None:
            time.sleep(0.6)
            self._cleanup_part_files(dest_path, retries=10)
            if remove_final:
                DownloadService._remove_incomplete_archive(dest_path)

        threading.Thread(target=worker, name="part-cleanup", daemon=True).start()
        if remove_final:
            DownloadService._remove_incomplete_archive(dest_path)

    @staticmethod
    def _dest_path_from_part_filename(filename: str) -> str | None:
        if ".part.progress" in filename:
            return filename.split(".part.progress", 1)[0]
        if filename.endswith(".part"):
            return filename[:-5]
        marker = ".part.part"
        idx = filename.rfind(marker)
        if idx > 0:
            return filename[:idx]
        return None

    def _recovery_payload(self, item: QueuedDownload) -> dict[str, Any]:
        downloaded, total = partial_bytes_for_dest(item.dest_path, item.connections)
        return {
            "dest_path": item.dest_path,
            "game_id": item.game_id,
            "title": item.title,
            "image_url": item.image_url,
            "connections": item.connections,
            "downloaded": downloaded,
            "total_size": total,
            "disk_bytes": part_disk_bytes(item.dest_path),
        }

    def _begin_resume_queued_item(self, item: QueuedDownload) -> None:
        partial_bytes, total_known = partial_bytes_for_dest(item.dest_path, item.connections)
        if partial_bytes:
            self.logger.info(
                f"[{item.title}] Resuming download ({format_bytes(partial_bytes)} saved)"
            )
        else:
            disk = part_disk_bytes(item.dest_path)
            if disk:
                self.logger.info(
                    f"[{item.title}] Resuming interrupted download "
                    f"({format_bytes(disk)} reserved on disk, "
                    f"{format_bytes(partial_bytes)} verified bytes)"
                )
            else:
                self.logger.info(f"[{item.title}] Resuming interrupted download")
        game = GameResult(
            game_id=item.game_id,
            title=item.title,
            image_url=item.image_url,
        )
        pending_id = f"resume-{item.game_id}-{uuid.uuid4().hex[:8]}"
        self._start_resolve_thread(
            pending_id,
            game,
            force_dest=item.dest_path,
            force_connections=item.connections,
            status_message="Resuming — getting fresh download link...",
            partial_bytes=partial_bytes,
            partial_total=total_known,
        )

    def list_recovery_downloads(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._recovery_payloads)

    def resume_interrupted_download(self, dest_path: str) -> dict[str, Any]:
        dest_path = (dest_path or "").strip()
        if not dest_path:
            return {"ok": False, "error": "Missing dest_path"}
        with self._lock:
            item = self._recovery_hold.pop(dest_path, None)
            if item:
                self._recovery_payloads = [
                    entry
                    for entry in self._recovery_payloads
                    if entry.get("dest_path") != dest_path
                ]
        if not item:
            return {"ok": False, "error": "Interrupted download not found"}
        self._begin_resume_queued_item(item)
        self.emit("download_recovery", {"items": self.list_recovery_downloads()})
        return {"ok": True}

    def discard_interrupted_download(self, dest_path: str) -> dict[str, Any]:
        dest_path = (dest_path or "").strip()
        if not dest_path:
            return {"ok": False, "error": "Missing dest_path"}
        with self._lock:
            item = self._recovery_hold.pop(dest_path, None)
            if item:
                self._recovery_payloads = [
                    entry
                    for entry in self._recovery_payloads
                    if entry.get("dest_path") != dest_path
                ]
        if not item:
            return {"ok": False, "error": "Interrupted download not found"}
        self._queue_store().remove(dest_path)
        self._cleanup_part_files(dest_path)
        self._schedule_part_cleanup(dest_path, remove_final=True)
        self.logger.info(f"[{item.title}] Partial download deleted — disk space reclaimed")
        self.emit("download_recovery", {"items": self.list_recovery_downloads()})
        self._advance_queue()
        return {"ok": True}

    def _protected_download_dest_paths(self) -> set[str]:
        protected: set[str] = set()
        for item in self._queue_store().load():
            if item.dest_path:
                protected.add(os.path.normpath(item.dest_path))
        with self._lock:
            for task in self._tasks.values():
                protected.add(os.path.normpath(task.dest_path))
            for view in self._task_views.values():
                if view.dest_path:
                    protected.add(os.path.normpath(view.dest_path))
        return protected

    def _sweep_download_folder_residue(self) -> None:
        part_removed = self._sweep_orphaned_part_files()
        archive_removed = self._sweep_abandoned_archives()
        if part_removed:
            self.logger.info(f"Removed {part_removed} orphaned partial download file(s)")
        if archive_removed:
            self.logger.info(f"Removed {archive_removed} failed download archive(s)")

    def _sweep_abandoned_archives(self) -> int:
        """Delete finished-looking archives that are corrupt or never completed."""
        root = self.root_dir
        if not root or not os.path.isdir(root):
            return 0

        protected = self._protected_download_dest_paths()
        with self._lock:
            for item in self._recovery_hold.values():
                if item.dest_path:
                    protected.add(os.path.normpath(item.dest_path))
            for view in self._task_views.values():
                if view.dest_path:
                    protected.add(os.path.normpath(view.dest_path))

        removed = 0
        try:
            entries = os.listdir(root)
        except OSError:
            return 0

        for entry in entries:
            if not entry or entry.startswith("."):
                continue
            full = os.path.join(root, entry)
            if not os.path.isfile(full):
                continue
            if not is_download_archive_path(full):
                continue

            dest_path = os.path.normpath(full)
            if dest_path in protected:
                continue

            part_path = dest_path + ".part"
            progress_path = part_path + ".progress"
            if os.path.isfile(part_path) or os.path.isfile(progress_path):
                continue

            if not archive_is_broken(dest_path):
                continue

            try:
                os.remove(dest_path)
                removed += 1
                self.logger.info(
                    f"Removed incomplete download: {os.path.basename(dest_path)}"
                )
            except OSError as exc:
                self.logger.warn(
                    f"Could not remove incomplete download {entry}: {exc}"
                )
        return removed

    def _sweep_orphaned_part_files(self) -> int:
        """Delete partial fragments left behind by cancelled or abandoned downloads."""
        root = self.root_dir
        if not root or not os.path.isdir(root):
            return 0

        protected = self._protected_download_dest_paths()
        with self._lock:
            for item in self._recovery_hold.values():
                if item.dest_path:
                    protected.add(os.path.normpath(item.dest_path))
        by_dest: dict[str, list[str]] = {}
        try:
            entries = os.listdir(root)
        except OSError:
            return 0

        for entry in entries:
            dest_base = self._dest_path_from_part_filename(entry)
            if not dest_base:
                continue
            full = os.path.join(root, entry)
            if not os.path.isfile(full):
                continue
            dest_path = os.path.normpath(
                dest_base
                if os.path.isabs(dest_base)
                else os.path.join(root, dest_base)
            )
            by_dest.setdefault(dest_path, []).append(full)

        removed = 0
        for dest_path, paths in by_dest.items():
            if dest_path in protected:
                continue
            final_archive = os.path.isfile(dest_path) and os.path.getsize(dest_path) > 0
            active_part = os.path.isfile(dest_path + ".part")
            for path in paths:
                try:
                    os.remove(path)
                    removed += 1
                except OSError:
                    pass
            if final_archive and not active_part:
                continue
            if os.path.isfile(dest_path):
                try:
                    os.remove(dest_path)
                    removed += 1
                except OSError:
                    pass
        return removed

    @staticmethod
    def _cleanup_part_files(dest_path: str, retries: int = 8) -> None:
        """Remove single- and multi-connection partial download files."""
        import glob
        import time

        part_path = dest_path + ".part"
        progress_path = part_path + ".progress"
        part_dir = os.path.dirname(part_path) or "."
        part_name = os.path.basename(part_path)

        def collect() -> list[str]:
            paths: list[str] = []
            if os.path.isfile(progress_path):
                paths.append(progress_path)
            progress_tmp = progress_path + ".tmp"
            if os.path.isfile(progress_tmp):
                paths.append(progress_tmp)
            for match in glob.glob(glob.escape(progress_path) + ".*.tmp"):
                if match not in paths:
                    paths.append(match)
            if os.path.isfile(part_path):
                paths.append(part_path)
            try:
                for entry in os.listdir(part_dir):
                    if entry.startswith(part_name + ".part"):
                        paths.append(os.path.join(part_dir, entry))
            except OSError:
                pass
            for match in glob.glob(glob.escape(part_path) + ".part*"):
                if match not in paths:
                    paths.append(match)
            return paths

        for attempt in range(retries):
            failed: list[str] = []
            for path in collect():
                try:
                    if os.path.isfile(path):
                        os.remove(path)
                except OSError:
                    failed.append(path)
            if not failed:
                break
            if attempt < retries - 1:
                time.sleep(0.15 * (attempt + 1))

    @staticmethod
    def _remove_incomplete_archive(dest_path: str) -> None:
        """Drop a failed/partial final file only when explicitly cancelling — never after a completed download."""
        if not dest_path or not os.path.isfile(dest_path):
            return
        part_path = dest_path + ".part"
        progress_path = part_path + ".progress"
        if os.path.isfile(part_path) or os.path.isfile(progress_path):
            return
        try:
            os.remove(dest_path)
        except OSError:
            pass

    def _on_download_complete(self, task_id: str) -> None:
        with self._lock:
            task = self._tasks.get(task_id)
            game = self._task_meta.get(task_id)
            view = self._task_views.get(task_id)
        if not task or not game or not view:
            return

        self._queue_store().remove(task.dest_path)
        self._schedule_part_cleanup(task.dest_path)
        self._start_extraction_for_game(game, task.dest_path, task_id=task_id)

    def _start_extraction_for_game(
        self,
        game: GameResult,
        archive_path: str,
        *,
        task_id: str | None = None,
    ) -> None:
        tid = (task_id or archive_path).strip()
        if not tid or not archive_path:
            return

        install_dir = os.path.join(
            self.root_dir,
            LibraryManager.safe_folder_name(game.title),
        )
        extract_usage = disk_usage_for_path(install_dir)
        with self._lock:
            view = self._task_views.get(tid)
            if view is None:
                view = TaskView(
                    task_id=tid,
                    title=game.title,
                    game_id=game.game_id,
                    image_url=game.image_url,
                    state=DownloadState.DOWNLOADING.value,
                    phase="extract",
                    dest_path=archive_path,
                    install_dir=install_dir,
                )
                self._task_views[tid] = view
                self._task_meta[tid] = game
            else:
                view.phase = "extract"
                view.install_dir = install_dir
            view.extract_message = "Preparing extraction..."
            view.disk_free = int(extract_usage["free_bytes"])
            view.disk_total = int(extract_usage["total_bytes"])
            payload = asdict(view)
        self.emit("task_update", payload)
        self.logger.info(f"[{game.title}] Starting extraction → {install_dir}")

        # Extraction can eat tens of GB; refresh free space at most once a second
        # so the UI shows disk pressure without a stat() call per progress line.
        last_disk_poll = [0.0]

        def on_extract_progress(current: int, total: int, message: str) -> None:
            self.logger.info(f"[{game.title}] {message}")
            now = time.time()
            usage = None
            if now - last_disk_poll[0] >= 1.0:
                last_disk_poll[0] = now
                try:
                    usage = disk_usage_for_path(install_dir)
                except OSError:
                    usage = None
            with self._lock:
                v = self._task_views.get(tid)
                if not v:
                    return
                v.extract_current = current
                v.extract_total = total
                v.extract_message = message
                if usage:
                    v.disk_free = int(usage["free_bytes"])
                    v.disk_total = int(usage["total_bytes"])
                payload = asdict(v)
            self.emit("task_update", payload)

        def worker() -> None:
            try:
                if not os.path.isfile(archive_path):
                    raise RuntimeError(
                        f"Archive missing before extract: {os.path.basename(archive_path)}"
                    )
                size_mb = os.path.getsize(archive_path) // (1024 * 1024)
                self.logger.info(
                    f"[{game.title}] Extracting archive ({size_mb} MB) → {install_dir}"
                )
                extract_archive(
                    archive_path,
                    install_dir,
                    on_progress=on_extract_progress,
                    remove_store_junk=self.store.is_anker,
                )
                # Rename/store-junk cleanup runs inside extract_archive before we scan
                # for executables so EXE picker paths match the final folder layout.
                if os.path.exists(archive_path):
                    os.remove(archive_path)
                    self.logger.info(
                        f"[{game.title}] Removed archive {os.path.basename(archive_path)}"
                    )
                self._schedule_part_cleanup(archive_path)
                exes = scan_executables(install_dir)
                self.logger.info(f"[{game.title}] Found {len(exes)} EXE file(s)")
                self._post_extract(tid, game, install_dir, exes)
            except Exception as exc:
                if os.path.isfile(archive_path):
                    self.logger.warn(
                        f"[{game.title}] Extract failed — archive kept at {archive_path}"
                    )
                self.logger.error(f"[{game.title}] Extract failed: {exc}")
                with self._lock:
                    v = self._task_views.get(tid)
                    if v:
                        v.phase = "error"
                        v.state = DownloadState.ERROR.value
                        v.error = str(exc)
                        self.emit("task_update", asdict(v))

        threading.Thread(target=worker, daemon=True).start()

    def _remove_task(self, task_id: str, *, advance: bool = True) -> None:
        """Drop a finished task from the active list and clear it from the UI."""
        with self._lock:
            self._tasks.pop(task_id, None)
            self._task_views.pop(task_id, None)
            self._task_meta.pop(task_id, None)
            self._downloaders.pop(task_id, None)
        self.emit("task_update", {"task_id": task_id, "removed": True})
        if advance:
            self._advance_queue()

    def _post_extract(
        self,
        task_id: str,
        game: GameResult,
        install_dir: str,
        exes: list[str],
    ) -> None:
        with self._lock:
            v = self._task_views.get(task_id)
            if v:
                v.phase = "done"
                v.extract_current = v.extract_total
                v.extract_message = "Extraction complete"
                try:
                    usage = disk_usage_for_path(install_dir)
                    v.disk_free = int(usage["free_bytes"])
                    v.disk_total = int(usage["total_bytes"])
                except OSError:
                    pass
                v.state = DownloadState.COMPLETED.value
                self.emit("task_update", asdict(v))

        if not exes:
            entry = LibraryEntry.create(
                title=game.title,
                game_id=game.game_id,
                install_dir=install_dir,
                image_url=game.image_url,
            )
            self.library.add(entry)
            self._schedule_cover_cache(entry)
            self._notify_trainer_entry(entry)
            self.emit("library_updated", {})
            self.logger.info(f"[{game.title}] Done — added to library")
            self._remove_task(task_id)
            return

        default_exe = pick_best_exe(exes)
        entry = LibraryEntry.create(
            title=game.title,
            game_id=game.game_id,
            install_dir=install_dir,
            image_url=game.image_url,
            exe_path=default_exe,
        )
        self.library.add(entry)
        self._schedule_cover_cache(entry)
        self._notify_trainer_entry(entry)
        self.emit("library_updated", {})
        self.logger.info(
            f"[{game.title}] Added to library"
            + (f" with {os.path.basename(default_exe)}" if default_exe else "")
        )

        pending = ExePickerPending(
            task_id=task_id,
            title=game.title,
            game_id=game.game_id,
            image_url=game.image_url,
            install_dir=install_dir,
            exes=exes,
            default_exe=default_exe,
            entry_id=entry.entry_id,
        )
        self._pending_exe[task_id] = pending
        self._save_pending_exe_to_disk()
        self.emit("exe_picker", {
            "task_id": task_id,
            "entry_id": entry.entry_id,
            "title": game.title,
        })
        self._remove_task(task_id)

    def list_pending_exe_details(self) -> list[dict[str, Any]]:
        return [
            detail
            for task_id in self.list_pending_exe()
            if (detail := self.get_pending_exe(task_id))
        ]

    def confirm_exe(
        self,
        task_id: str,
        exe_path: str,
    ) -> dict[str, Any]:
        pending = self._pending_exe.pop(task_id, None)
        if not pending:
            return {"ok": False, "error": "No pending EXE picker for this task."}
        if exe_path not in pending.exes:
            return {"ok": False, "error": "Invalid EXE selection."}

        entry = None
        if pending.entry_id:
            entry = self.library.get(pending.entry_id)
        if not entry:
            entry = self.library.find_by_install_dir(pending.install_dir)

        if entry:
            self.library.update_exe(entry.entry_id, exe_path)
        else:
            entry = LibraryEntry.create(
                title=pending.title,
                game_id=pending.game_id,
                install_dir=pending.install_dir,
                image_url=pending.image_url,
                exe_path=exe_path,
            )
            self.library.add(entry)
            self._schedule_cover_cache(entry)
            self._notify_trainer_entry(entry)

        self.emit("library_updated", {})
        self._pending_exe_store().remove(task_id)
        self.logger.info(
            f"[{pending.title}] PLAY executable set to {os.path.basename(exe_path)}"
        )
        return {"ok": True, "entry_id": entry.entry_id}

    def skip_exe_picker(self, task_id: str) -> dict[str, Any]:
        pending = self._pending_exe.pop(task_id, None)
        if not pending:
            return {"ok": False, "error": "No pending EXE picker for this task."}
        self._pending_exe_store().remove(task_id)
        self.logger.info(
            f"[{pending.title}] EXE picker dismissed — game stays in library"
        )
        return {"ok": True}

    def rescan_exes(self, task_id: str) -> dict[str, Any]:
        pending = self._pending_exe.get(task_id)
        if not pending:
            return {"ok": False, "error": "No pending EXE picker."}
        exes = scan_executables(pending.install_dir)
        pending.exes = exes
        pending.default_exe = pick_best_exe(exes) if exes else ""
        self._save_pending_exe_to_disk()
        return self.get_pending_exe(task_id) or {"ok": False}

    def _library_entry_dict(self, entry: LibraryEntry) -> dict[str, Any]:
        from banner_cache import library_banner_url
        from cover_cache import library_cover_url

        data = asdict(entry)
        data["image_url"] = library_cover_url(entry.entry_id, entry.image_url)
        data["banner_url"] = library_banner_url(entry.entry_id)
        trainers = getattr(self, "trainers", None)
        if trainers:
            data.update(trainers.status_for(entry))
        else:
            data.update(
                {
                    "trainer_available": False,
                    "trainer_downloaded": False,
                    "trainer_fetching": False,
                    "trainer_name": "",
                }
            )
        return data

    def _schedule_cover_cache(self, entry: LibraryEntry) -> None:
        def worker() -> None:
            from cover_cache import cache_cover_for_entry

            if entry.image_url and cache_cover_for_entry(
                entry.entry_id,
                entry.image_url,
                logger=self.logger,
            ):
                self.emit("library_updated", {})
            self._schedule_banner_cache(entry)

        threading.Thread(
            target=worker,
            name=f"cover-{entry.entry_id[:8]}",
            daemon=True,
        ).start()

    def _schedule_banner_cache(self, entry: LibraryEntry) -> None:
        def worker() -> None:
            from banner_cache import cache_banner_for_entry

            if cache_banner_for_entry(entry, self.store, logger=self.logger):
                self.emit("library_updated", {})

        threading.Thread(
            target=worker,
            name=f"banner-{entry.entry_id[:8]}",
            daemon=True,
        ).start()

    def _warm_library_cover_cache(self) -> None:
        entries = list(self.library.entries)

        def worker() -> None:
            from cover_cache import warm_library_covers

            count = warm_library_covers(entries, logger=self.logger)
            if count:
                self.logger.info(f"Cached {count} library cover(s) for offline use")
                self.emit("library_updated", {})

        threading.Thread(
            target=worker,
            name="library-cover-warm",
            daemon=True,
        ).start()

    def _warm_library_banner_cache(self) -> None:
        entries = list(self.library.entries)

        def worker() -> None:
            from banner_cache import warm_library_banners

            count = warm_library_banners(entries, self.store, logger=self.logger)
            if count:
                self.logger.info(f"Cached {count} library banner(s) for Big Picture")
                self.emit("library_updated", {})

        threading.Thread(
            target=worker,
            name="library-banner-warm",
            daemon=True,
        ).start()

    def list_library(self) -> dict[str, Any]:
        return {
            "entries": [self._library_entry_dict(e) for e in self.library.entries],
            "load_warning": self.library.load_warning,
            "library_path": self.settings.library_path,
            "heal_summary": self._last_library_heal,
        }

    def reconcile_library(self, *, manual: bool = False) -> dict[str, Any]:
        """Smart sync: restore corrupt backup, merge legacy, auto-import from disk."""
        heal = self._auto_heal_library(silent=not manual)
        self._last_library_heal = {
            **asdict(heal),
            "entry_count": len(self.library.entries),
        }
        payload = {
            "ok": True,
            "manual": manual,
            **self._last_library_heal,
            "entry_count": len(self.library.entries),
            "load_warning": self.library.load_warning,
        }
        return payload

    def scan_missing_library(self) -> dict[str, Any]:
        """Optional manual re-scan — same heal pass, returns candidate preview."""
        result = self.reconcile_library(manual=True)
        candidates = scan_download_folder(
            self.root_dir,
            self.library.known_install_dirs(),
        )
        result["count"] = len(candidates)
        result["candidates"] = [
            {
                "title": c.title,
                "install_dir": c.install_dir,
                "game_id": c.game_id,
                "exe_count": c.exe_count,
                "suggested_exe": c.suggested_exe,
            }
            for c in candidates
        ]
        return result

    def backfill_library_artwork(self, *, force: bool = False) -> dict[str, Any]:
        updated = 0
        for entry in list(self.library.entries):
            if not force and not should_refresh_artwork(entry.image_url):
                continue
            match = resolve_library_artwork(
                self.store,
                entry.title,
                entry.game_id,
                self.logger,
                install_dir=entry.install_dir,
            )
            if not match or not match.image_url:
                continue
            if entry.image_url and entry.image_url == match.image_url:
                continue
            if match.title:
                entry.title = match.title
            if match.game_id:
                entry.game_id = match.game_id
            entry.image_url = match.image_url
            updated += 1
            from cover_cache import cache_cover_for_entry, remove_cached_cover

            remove_cached_cover(entry.entry_id)
            cache_cover_for_entry(entry.entry_id, entry.image_url, logger=self.logger)
        if updated:
            self.library.save()
            self.emit("library_updated", {})
            self.logger.info(f"Refreshed cover art for {updated} library game(s)")
        self._warm_library_cover_cache()
        return {"ok": True, "updated": updated}

    def _resolve_import_artwork(
        self,
        cand: ScanCandidate,
    ) -> tuple[str, str, str]:
        """Return (title, game_id, image_url) for a scan candidate."""
        match = resolve_library_artwork(
            self.store,
            cand.title,
            cand.game_id,
            self.logger,
            install_dir=cand.install_dir,
        )
        if not match:
            return cand.title, cand.game_id, ""
        title = match.title or cand.title
        game_id = match.game_id or cand.game_id
        return title, game_id, match.image_url or ""

    def import_library_candidates(
        self,
        install_dirs: list[str] | None = None,
        import_all: bool = False,
    ) -> dict[str, Any]:
        candidates = scan_download_folder(
            self.root_dir,
            self.library.known_install_dirs(),
        )
        if import_all:
            selected = candidates
        else:
            wanted = {os.path.normcase(os.path.abspath(p)) for p in (install_dirs or [])}
            selected = [
                c
                for c in candidates
                if os.path.normcase(os.path.abspath(c.install_dir)) in wanted
            ]

        imported: list[str] = []
        for cand in selected:
            if self.library.find_by_install_dir(cand.install_dir):
                continue
            title, game_id, image_url = self._resolve_import_artwork(cand)
            exes = scan_executables(cand.install_dir)
            exe_path = pick_best_exe(exes)
            entry = LibraryEntry.create(
                title=title,
                game_id=game_id,
                install_dir=cand.install_dir,
                exe_path=exe_path,
                image_url=image_url,
            )
            self.library.add(entry)
            self._schedule_cover_cache(entry)
            self._notify_trainer_entry(entry)
            imported.append(entry.title)
            self.logger.info(f"Imported to library: {entry.title}")

        if imported:
            self.emit("library_updated", {})
        return {"ok": True, "imported": imported, "count": len(imported)}

    def _auto_heal_library(self, *, silent: bool = False) -> LibraryHealResult:
        """Startup/manual library self-heal — no user action required."""
        result = LibraryHealResult()
        was_corrupt = bool(self.library.load_warning)

        if was_corrupt:
            if self.library.try_restore_from_backup():
                result.restored_backup = True
                result.recovered_corrupt = True
                self.logger.info("Restored library.json from automatic backup")

        legacy_path = os.path.join(self.root_dir, "library.json")
        merged = self.library.merge_from_file(legacy_path)
        if merged:
            result.merged_legacy = merged
            self.logger.info(
                f"Merged {merged} game(s) from legacy library in download folder"
            )

        imported = self._auto_import_from_download_folder()
        result.auto_imported = imported

        if was_corrupt and self.library.entries:
            self.library.clear_load_warning()
            result.recovered_corrupt = True
        elif was_corrupt and (result.restored_backup or imported or merged):
            self.library.clear_load_warning()
            result.recovered_corrupt = True
        elif was_corrupt and not self.library.entries:
            self.library.load_warning = "library_corrupt_unrecoverable"

        needs_art = any(
            should_refresh_artwork(e.image_url) for e in self.library.entries
        )
        if needs_art or imported:
            result.artwork_queued = True
            threading.Thread(
                target=self.backfill_library_artwork,
                kwargs={"force": bool(imported)},
                name="library-artwork-backfill",
                daemon=True,
            ).start()

        if result.changed:
            self.emit("library_updated", {})
            self.emit("library_healed", {
                **asdict(result),
                "silent": silent,
                "entry_count": len(self.library.entries),
            })

        return result

    def _auto_import_from_download_folder(self) -> int:
        candidates = scan_download_folder(
            self.root_dir,
            self.library.known_install_dirs(),
        )
        imported = 0
        for cand in candidates:
            if self.library.find_by_install_dir(cand.install_dir):
                continue
            exes = scan_executables(cand.install_dir)
            entry = LibraryEntry.create(
                title=cand.title,
                game_id=cand.game_id,
                install_dir=cand.install_dir,
                exe_path=pick_best_exe(exes),
            )
            self.library.add(entry)
            self._schedule_cover_cache(entry)
            self._notify_trainer_entry(entry)
            imported += 1
            self.logger.info(f"Auto-imported to library: {entry.title}")
        return imported

    def _reconcile_startup_library(self) -> None:
        """Deprecated — use _auto_heal_library()."""
        self._auto_heal_library(silent=True)

    def _reconcile_pending_exe_with_library(self) -> None:
        """Ensure pending EXE picks have a library row (upgrade path)."""
        changed = False
        for task_id, pending in list(self._pending_exe.items()):
            entry = self.library.find_by_install_dir(pending.install_dir)
            if entry:
                pending.entry_id = entry.entry_id
                if not entry.exe_path and pending.default_exe:
                    self.library.update_exe(entry.entry_id, pending.default_exe)
                continue

            default_exe = pending.default_exe or pick_best_exe(pending.exes)
            entry = LibraryEntry.create(
                title=pending.title,
                game_id=pending.game_id,
                install_dir=pending.install_dir,
                image_url=pending.image_url,
                exe_path=default_exe,
            )
            self.library.add(entry)
            self._schedule_cover_cache(entry)
            self._notify_trainer_entry(entry)
            pending.entry_id = entry.entry_id
            changed = True
            self.logger.info(
                f"Recovered library entry for pending EXE picker: {pending.title}"
            )

        if changed:
            self._save_pending_exe_to_disk()
            self.emit("library_updated", {})

    def launch_game(self, entry_id: str) -> dict[str, Any]:
        entry = self.library.get(entry_id)
        if not entry:
            return {"ok": False, "error": "Game not found."}
        if not entry.exe_path or not os.path.isfile(entry.exe_path):
            return {"ok": False, "error": "no_exe", "needs_picker": True}
        try:
            launch_play_target(entry.exe_path)
            if self.settings.trainer_auto_run:
                self._autorun_trainer(entry)
            return {"ok": True}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def launch_trainer(self, entry_id: str) -> dict[str, Any]:
        entry = self.library.get(entry_id)
        if not entry:
            return {"ok": False, "error": "Game not found."}
        if not self.settings.trainer_on_library and not self.settings.trainer_auto_run:
            return {"ok": False, "error": "Trainers are disabled in Settings."}
        try:
            result = self.trainers.launch_for_entry(
                entry,
                fetch_if_needed=True,
            )
            if result.get("ok"):
                result.update(self.trainers.status_for(entry))
            return result
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def _autorun_trainer(self, entry: LibraryEntry) -> None:
        def worker() -> None:
            try:
                time.sleep(1.2)
                self.trainers.launch_for_entry(
                    entry,
                    fetch_if_needed=self.settings.trainer_auto_fetch,
                )
            except Exception as exc:
                self.logger.info(f"Auto-run trainer failed for {entry.title}: {exc}")

        threading.Thread(
            target=worker,
            name=f"trainer-autorun-{entry.entry_id[:8]}",
            daemon=True,
        ).start()

    def _notify_trainer_entry(self, entry: LibraryEntry) -> None:
        trainers = getattr(self, "trainers", None)
        if not trainers:
            return
        if not (
            self.settings.trainer_auto_fetch
            or self.settings.trainer_on_library
            or self.settings.trainer_auto_run
        ):
            return
        trainers.notify_entry(
            entry,
            download=self.settings.trainer_auto_fetch,
            probe=self.settings.trainer_auto_fetch or self.settings.trainer_on_library,
        )

    def _sync_trainers(self) -> None:
        trainers = getattr(self, "trainers", None)
        if not trainers:
            return
        if not (
            self.settings.trainer_auto_fetch
            or self.settings.trainer_on_library
            or self.settings.trainer_auto_run
        ):
            return
        trainers.scan_library(
            list(self.library.entries),
            download=self.settings.trainer_auto_fetch,
            probe=self.settings.trainer_auto_fetch or self.settings.trainer_on_library,
        )

    def get_entry_exes(self, entry_id: str) -> dict[str, Any]:
        entry = self.library.get(entry_id)
        if not entry:
            return {"ok": False, "error": "Game not found."}
        if not os.path.isdir(entry.install_dir):
            return {"ok": False, "error": "Install folder missing."}
        exes = scan_executables(entry.install_dir)
        return {
            "ok": True,
            "title": entry.title,
            "install_dir": entry.install_dir,
            "image_url": self._library_entry_dict(entry)["image_url"],
            "exes": [
                {"path": p, "label": display_name(p, entry.install_dir)} for p in exes
            ],
            "current_exe": entry.exe_path,
        }

    def set_entry_exe(
        self,
        entry_id: str,
        exe_path: str,
    ) -> dict[str, Any]:
        entry = self.library.get(entry_id)
        if not entry:
            return {"ok": False, "error": "Game not found."}
        self.library.update_exe(entry_id, exe_path)
        self.emit("library_updated", {})
        return {"ok": True}

    def delete_entry(self, entry_id: str) -> dict[str, Any]:
        from path_utils import remove_tree

        entry = self.library.get(entry_id)
        if not entry:
            return {"ok": False, "error": "Game not found."}

        install_dir = os.path.abspath(entry.install_dir)
        if os.path.isdir(install_dir):
            try:
                remove_tree(install_dir)
                self.logger.info(f"Deleted game folder: {install_dir}")
            except OSError as exc:
                self.logger.error(f"Folder delete failed for {install_dir}: {exc}")
                return {
                    "ok": False,
                    "error": f"Could not delete folder: {exc}",
                }
        elif install_dir:
            self.logger.warn(f"Install folder not found during delete: {install_dir}")

        trainers = getattr(self, "trainers", None)
        if trainers:
            trainers.remove_for_entry(entry.entry_id, entry.title)

        self.library.remove(entry_id)
        self.emit("library_updated", {})
        return {"ok": True}

    @staticmethod
    def format_task_line(view: TaskView) -> str:
        if view.total_size > 0:
            pct = min(100.0, view.downloaded / view.total_size * 100)
        else:
            pct = 0.0
        speed = format_bytes(int(view.speed)) + "/s"
        return f"{pct:.1f}% | {format_bytes(view.downloaded)} / {format_bytes(view.total_size)} | {speed}"
