"""FastAPI backend for PlayZip web UI."""

from __future__ import annotations

import asyncio
import json
import queue
import sys
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from dataclasses import asdict

from app_paths import app_root_dir, icon_path, settings_file_path, web_dir
from defender_utils import is_windows, try_apply_defender_exclusion
from disk_space import disk_usage_for_path
from download_logger import DownloadLogger
from download_service import DownloadService
from announcement import fetch_announcement
from landing_page import resolve_landing_page_url
from playzip_api import PlayZipClient
from settings_manager import (
    APP_BRAND,
    APP_TITLE,
    APP_VERSION,
    MAX_CONNECTIONS,
    MIN_CONNECTIONS,
    SettingsManager,
)
from store_manager import StoreManager, store_label

# Shared app state (initialized before uvicorn starts)
_service: DownloadService | None = None
_store: StoreManager | None = None
_client: PlayZipClient | None = None
_logger: DownloadLogger | None = None
_settings: SettingsManager | None = None
_event_queue: queue.Queue[tuple[str, dict[str, Any]]] = queue.Queue(maxsize=500)


def get_service() -> DownloadService:
    if _service is None:
        raise RuntimeError("DownloadService not initialized")
    return _service


def get_store() -> StoreManager:
    if _store is None:
        raise RuntimeError("StoreManager not initialized")
    return _store


def get_client() -> PlayZipClient:
    return get_store().get_client()


def get_logger() -> DownloadLogger:
    if _logger is None:
        raise RuntimeError("DownloadLogger not initialized")
    return _logger


def get_settings() -> SettingsManager:
    if _settings is None:
        raise RuntimeError("SettingsManager not initialized")
    return _settings


def _migrate_library_if_needed(app_root: str, new_library_path: str) -> None:
    import os
    import shutil

    old_path = os.path.join(app_root, "library.json")
    if os.path.isfile(old_path) and not os.path.isfile(new_library_path):
        os.makedirs(os.path.dirname(new_library_path) or ".", exist_ok=True)
        shutil.copy2(old_path, new_library_path)


def init_backend() -> None:
    global _service, _store, _client, _logger, _settings

    root = app_root_dir()

    _logger = DownloadLogger()
    _logger.info("=== QuickPlay starting ===")
    _logger.info(f"App folder: {root}")

    _settings = SettingsManager(
        path=settings_file_path(),
        default_download_dir=root,
    )
    _migrate_library_if_needed(root, _settings.library_path)
    _logger.info(f"Download folder: {_settings.download_dir}")

    def on_event(event_type: str, payload: dict[str, Any]) -> None:
        if event_type == "gate_progress" and _service is not None:
            _service.on_gate_verify_progress(payload)
        try:
            _event_queue.put_nowait((event_type, payload))
        except queue.Full:
            pass

    _store = StoreManager(
        settings=_settings,
        logger=_logger,
        on_rate_limit=None,
        on_snapshot_recorded=_settings.set_hardware_snapshot_submitted,
        on_event=on_event,
    )
    if _store.ensure_startup_store():
        _logger.info(
            f"Using alternate store: {store_label(_store.active_store)} "
            "(primary store unreachable)"
        )

    _service = DownloadService(
        settings=_settings,
        logger=_logger,
        store=_store,
        on_event=on_event,
    )
    _store.set_rate_limit_callback(_service._on_rate_limit_tick)
    _store.set_resolve_status_callback(_service._on_resolve_status_tick)
    _client = _service.client
    _service.resume_pending_downloads()

    if _settings.defender_exclusion and is_windows():
        ok, msg = try_apply_defender_exclusion(_settings.download_dir, enabled=True)
        _settings.set_defender_status(msg)
        if ok:
            _logger.info(f"Defender exclusion: {msg}")
        else:
            _logger.warn(f"Defender exclusion: {msg}")
        from win_elevate import runtime_extract_dir

        rt = runtime_extract_dir()
        if rt:
            ok_rt, msg_rt = try_apply_defender_exclusion(rt, enabled=True)
            if ok_rt:
                _logger.info(f"Defender runtime cache: {msg_rt}")

    from gamepad_input import start_gamepad_bridge

    start_gamepad_bridge()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    yield


app = FastAPI(title="QuickPlay", lifespan=lifespan)

_favicon_path = icon_path()


@app.get("/favicon.ico", include_in_schema=False)
def favicon() -> FileResponse:
    if not _favicon_path:
        raise HTTPException(status_code=404)
    return FileResponse(_favicon_path, media_type="image/x-icon")


_web_path = web_dir()
if _web_path and __import__("os").path.isdir(_web_path):
    app.mount("/static", StaticFiles(directory=_web_path), name="static")


class DownloadRequest(BaseModel):
    game_id: str
    title: str
    image_url: str = ""
    store_size_bytes: int | None = None


class ExeChoice(BaseModel):
    exe_path: str


class SettingsUpdate(BaseModel):
    download_dir: str | None = None
    connections: int | None = Field(default=None, ge=MIN_CONNECTIONS, le=MAX_CONNECTIONS)
    show_logs: bool | None = None
    verification_window_full: bool | None = None
    defender_exclusion: bool | None = None
    disable_announcement_on_startup: bool | None = None
    language: str | None = None
    store: str | None = None
    controller_enabled: bool | None = None
    allow_big_picture: bool | None = None


@app.get("/")
async def index():
    index_path = __import__("os").path.join(web_dir(), "index.html")
    if not __import__("os").path.isfile(index_path):
        raise HTTPException(500, "Web UI files not found")
    return FileResponse(index_path)


@app.get("/api/config")
async def api_config():
    s = get_settings()
    return {
        "app_root": app_root_dir(),
        "download_dir": s.download_dir,
        "version": APP_VERSION,
        "app_brand": APP_BRAND,
        "app_title": APP_TITLE,
        "platform": sys.platform,
        "is_windows": is_windows(),
        "library_path": s.library_path,
    }


@app.get("/api/activation/startup")
async def api_activation_startup():
    from license_manager import consume_startup_activation

    data = consume_startup_activation()
    if not data:
        return {"show": False}
    return {"show": True, **data}


@app.get("/api/landing-page")
async def api_landing_page():
    return {"url": resolve_landing_page_url()}


@app.get("/api/announcement")
async def api_announcement():
    text = fetch_announcement()
    return {"message": text or ""}


@app.get("/api/categories")
async def api_categories():
    return {"categories": get_store().get_categories(), "store": get_store().active_store}


@app.get("/api/settings")
async def api_get_settings():
    return get_settings().to_dict()


@app.get("/api/redistributables/status")
async def api_redist_status():
    from redist_installer import redist_status

    return redist_status()


@app.get("/api/redistributables/packages")
async def api_redist_packages():
    from redist_installer import list_redist_packages, redist_status

    return {
        "packages": list_redist_packages(),
        **{k: redist_status()[k] for k in ("available", "running", "finished")},
    }


@app.post("/api/redistributables/install")
async def api_redist_install():
    from redist_installer import redist_status, start_redist_install

    def on_progress(payload: dict[str, Any]) -> None:
        try:
            _event_queue.put_nowait(("redist_progress", payload))
        except queue.Full:
            pass

    def on_line_log(message: str) -> None:
        get_logger().info(f"[Runtimes] {message}")

    ok, msg = start_redist_install(on_progress, on_line_log=on_line_log)
    if not ok:
        raise HTTPException(status_code=409 if msg == "already_running" else 400, detail=msg)
    return {"started": True, "status": redist_status()}


@app.get("/api/gamepad/state")
async def api_gamepad_state():
    from gamepad_input import get_latest_state

    return get_latest_state()


@app.put("/api/settings")
async def api_update_settings(body: SettingsUpdate):
    svc = get_service()
    settings = get_settings()
    store_mgr = get_store()
    updates = body.model_dump(exclude_none=True)
    if not updates:
        return settings.to_dict()

    old_dir = settings.download_dir
    old_store = settings.store
    settings.update(**updates)

    if "store" in updates and settings.store != old_store:
        store_mgr.set_store(settings.store, persist=True, reason="user_setting")

    if settings.download_dir != old_dir or body.defender_exclusion is not None:
        if settings.defender_exclusion and is_windows():
            ok, msg = try_apply_defender_exclusion(settings.download_dir, True)
            settings.set_defender_status(msg)
            if ok:
                get_logger().info(f"Defender exclusion: {msg}")
            else:
                get_logger().warn(f"Defender exclusion: {msg}")
        elif not settings.defender_exclusion:
            settings.set_defender_status("Defender exclusion disabled in settings.")

    svc.apply_settings()
    get_logger().info(
        f"Settings updated — folder={settings.download_dir}, "
        f"connections={settings.connections}, show_logs={settings.show_logs}, "
        f"verification_window_full={settings.verification_window_full}, "
        f"store={settings.store}"
    )
    return settings.to_dict()


@app.post("/api/store/refresh-sessions")
async def api_refresh_store_sessions():
    get_store().invalidate_cached_clients()
    return {"ok": True}


@app.get("/api/browse")
async def api_browse(
    page: int = Query(1, ge=1),
    sort: str = Query("views"),
    category: str = Query("all"),
):
    games, meta = await asyncio.to_thread(
        get_store().browse, page, sort, category
    )
    return {
        "games": [
            {"game_id": g.game_id, "title": g.title, "image_url": g.image_url}
            for g in games
        ],
        "page": page,
        "sort": sort,
        "category": category,
        **meta,
    }


@app.get("/api/search")
async def api_search(q: str = Query("")):
    games, meta = await asyncio.to_thread(get_store().search, q)
    return {
        "games": [
            {"game_id": g.game_id, "title": g.title, "image_url": g.image_url}
            for g in games
        ],
        "query": q,
        **meta,
    }


@app.get("/api/games/{game_id}/details")
async def api_game_details(
    game_id: str,
    title: str = Query(""),
    image_url: str = Query(""),
):
    def work():
        service = get_store().create_details_service(get_logger())
        return service.fetch(game_id, title=title, image_url=image_url)

    details = await asyncio.to_thread(work)
    payload = asdict(details)
    usage = disk_usage_for_path(get_settings().download_dir)
    from storage_requirements import evaluate_disk_space, required_disk_bytes

    payload["disk_free_bytes"] = usage["free_bytes"]
    payload["disk_total_bytes"] = usage["total_bytes"]
    payload["download_dir"] = usage["path"]
    space = evaluate_disk_space(int(usage["free_bytes"]), details.store_size_bytes)
    payload["required_bytes"] = space.get("required_bytes")
    payload["space_ok"] = space.get("ok")
    payload["space_unknown"] = space.get("unknown_size")
    return payload


@app.get("/api/storage")
async def api_storage(path: str = Query("")):
    target = path.strip() or get_settings().download_dir
    return disk_usage_for_path(target)


@app.get("/api/media/hls-manifest")
async def api_hls_manifest(url: str = Query("")):
    from media_proxy import fetch_hls_manifest

    if not url.strip():
        raise HTTPException(400, "Missing url")
    try:
        manifest = await asyncio.to_thread(
            fetch_hls_manifest,
            url.strip(),
            get_client().session,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Failed to load trailer: {exc}") from exc
    return StreamingResponse(
        iter([manifest.encode("utf-8")]),
        media_type="application/vnd.apple.mpegurl",
    )


@app.get("/api/media/segment")
async def api_media_segment(url: str = Query("")):
    from media_proxy import stream_media

    if not url.strip():
        raise HTTPException(400, "Missing url")
    try:
        stream, content_type = await asyncio.to_thread(
            stream_media,
            url.strip(),
            get_client().session,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(502, f"Failed to load media: {exc}") from exc
    return StreamingResponse(stream, media_type=content_type)


@app.get("/api/downloads/rate-limit")
async def api_rate_limit():
    remaining = get_client().rate_limit_remaining()
    return {"seconds": remaining}


@app.post("/api/downloads")
async def api_start_download(body: DownloadRequest):
    result = await asyncio.to_thread(
        get_service().start_download,
        body.game_id,
        body.title,
        body.image_url,
        body.store_size_bytes,
    )
    if not result.get("ok") and result.get("error") == "insufficient_disk_space":
        raise HTTPException(status_code=507, detail=result)
    return result


@app.get("/api/downloads/space-check")
async def api_download_space_check(
    game_id: str = Query(""),
    title: str = Query(""),
    image_url: str = Query(""),
    store_size_bytes: int | None = Query(None),
):
    if not game_id.strip():
        raise HTTPException(400, "game_id is required")
    return await asyncio.to_thread(
        get_service().check_download_space,
        game_id.strip(),
        title,
        image_url,
        store_size_bytes,
    )


@app.get("/api/downloads")
async def api_list_downloads():
    return {"tasks": get_service().list_tasks()}


@app.post("/api/downloads/{task_id}/pause")
async def api_pause(task_id: str):
    ok = get_service().pause_task(task_id)
    if not ok:
        raise HTTPException(404, "Task not found")
    return {"ok": True}


@app.post("/api/downloads/{task_id}/resume")
async def api_resume(task_id: str):
    ok = get_service().resume_task(task_id)
    if not ok:
        raise HTTPException(
            409,
            "Cannot resume yet — another download is still resolving or extracting.",
        )
    return {"ok": True}


@app.post("/api/downloads/{task_id}/retry")
async def api_retry_download(task_id: str):
    result = get_service().retry_download(task_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Retry failed"))
    return result


@app.post("/api/downloads/{task_id}/refresh-link")
async def api_refresh_download_link(task_id: str):
    result = get_service().refresh_download_link(task_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Refresh failed"))
    return result


@app.post("/api/downloads/{task_id}/cancel")
async def api_cancel(task_id: str):
    ok = get_service().cancel_task(task_id)
    if not ok:
        raise HTTPException(404, "Task not found")
    return {"ok": True}


class TaskIdBody(BaseModel):
    task_id: str = Field(..., min_length=1)


@app.post("/api/downloads/cancel")
async def api_cancel_body(payload: TaskIdBody):
    ok = get_service().cancel_task(payload.task_id)
    if not ok:
        raise HTTPException(404, "Task not found")
    return {"ok": True}


@app.get("/api/pending-exe")
async def api_pending_exe_list():
    svc = get_service()
    return {
        "pending": [
            svc.get_pending_exe(tid)
            for tid in svc.list_pending_exe()
            if svc.get_pending_exe(tid)
        ]
    }


@app.get("/api/pending-exe/{task_id}")
async def api_pending_exe(task_id: str):
    data = get_service().get_pending_exe(task_id)
    if not data:
        raise HTTPException(404, "No pending EXE picker")
    return data


@app.post("/api/pending-exe/{task_id}/confirm")
async def api_confirm_exe(task_id: str, body: ExeChoice):
    result = get_service().confirm_exe(task_id, body.exe_path)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed"))
    return result


@app.post("/api/pending-exe/{task_id}/skip")
async def api_skip_exe(task_id: str):
    result = get_service().skip_exe_picker(task_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed"))
    return result


@app.post("/api/pending-exe/{task_id}/rescan")
async def api_rescan_exe(task_id: str):
    result = get_service().rescan_exes(task_id)
    if isinstance(result, dict) and result.get("ok") is False:
        raise HTTPException(400, result.get("error", "Failed"))
    return result


@app.get("/api/library")
async def api_library():
    return get_service().list_library()


@app.get("/api/covers/{entry_id}")
async def api_library_cover(entry_id: str):
    from cover_cache import guess_media_type, read_cached_cover

    data = read_cached_cover(entry_id)
    if not data:
        raise HTTPException(404, "Cover not cached")
    return Response(
        content=data,
        media_type=guess_media_type(data),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.get("/api/banners/{entry_id}")
async def api_library_banner(entry_id: str):
    from banner_cache import guess_media_type, read_cached_banner

    data = read_cached_banner(entry_id)
    if not data:
        raise HTTPException(404, "Banner not cached")
    return Response(
        content=data,
        media_type=guess_media_type(data),
        headers={"Cache-Control": "public, max-age=31536000, immutable"},
    )


@app.post("/api/library/scan")
async def api_library_scan():
    return get_service().scan_missing_library()


@app.post("/api/library/reconcile")
async def api_library_reconcile():
    return get_service().reconcile_library(manual=True)


@app.post("/api/library/refresh-artwork")
async def api_library_refresh_artwork():
    return get_service().backfill_library_artwork()


class LibraryImportBody(BaseModel):
    install_dirs: list[str] = Field(default_factory=list)
    import_all: bool = False


@app.post("/api/library/import")
async def api_library_import(body: LibraryImportBody):
    result = get_service().import_library_candidates(
        install_dirs=body.install_dirs,
        import_all=body.import_all,
    )
    return result


@app.post("/api/library/{entry_id}/launch")
async def api_launch(entry_id: str):
    result = get_service().launch_game(entry_id)
    if not result.get("ok"):
        if result.get("needs_picker"):
            return {"ok": False, "needs_picker": True}
        raise HTTPException(400, result.get("error", "Launch failed"))
    return result


@app.get("/api/library/{entry_id}/exes")
async def api_library_exes(entry_id: str):
    result = get_service().get_entry_exes(entry_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed"))
    return result


@app.put("/api/library/{entry_id}/exe")
async def api_set_exe(entry_id: str, body: ExeChoice):
    result = get_service().set_entry_exe(entry_id, body.exe_path)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed"))
    return result


@app.delete("/api/library/{entry_id}")
async def api_delete_entry(entry_id: str):
    result = get_service().delete_entry(entry_id)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "Failed"))
    return result


@app.get("/api/logs")
async def api_logs():
    return {"entries": [{"level": l, "message": m, "time": t} for l, m, t in get_logger().history()]}


@app.get("/api/events")
async def api_events():
    """Server-Sent Events for live task/library updates."""

    async def stream():
        while True:
            try:
                event_type, payload = await asyncio.to_thread(
                    _event_queue.get, True, 25.0
                )
                data = json.dumps({"type": event_type, "payload": payload})
                yield f"data: {data}\n\n"
            except queue.Empty:
                yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
