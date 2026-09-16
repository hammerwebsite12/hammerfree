"""Download and install official Microsoft game runtime redistributables (Windows)."""

from __future__ import annotations

import os
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable

import requests

from defender_utils import is_windows

ProgressCallback = Callable[[dict[str, Any]], None]
LineLogCallback = Callable[[str], None]

# Official Microsoft CDN / aka.ms — no third-party bundles.
_REDIST_PACKAGES: tuple[dict[str, Any], ...] = (
    {
        "id": "vcredist2008_x86",
        "name": "Visual C++ 2008 SP1 (x86)",
        "url": "https://download.microsoft.com/download/5/D/8/5D8C65CB-C849-4025-8E95-C3966CAFD8AE/vcredist_x86.exe",
        "args": ("/q",),
        "timeout": 1200,
    },
    {
        "id": "vcredist2008_x64",
        "name": "Visual C++ 2008 SP1 (x64)",
        "url": "https://download.microsoft.com/download/5/D/8/5D8C65CB-C849-4025-8E95-C3966CAFD8AE/vcredist_x64.exe",
        "args": ("/q",),
        "timeout": 1200,
    },
    {
        "id": "vcredist2010_x86",
        "name": "Visual C++ 2010 (x86)",
        "url": "https://download.microsoft.com/download/1/6/5/165255E7-1014-4D0A-B094-B6A430A6BFFC/vcredist_x86.exe",
        "args": ("/q", "/norestart",),
    },
    {
        "id": "vcredist2010_x64",
        "name": "Visual C++ 2010 (x64)",
        "url": "https://download.microsoft.com/download/1/6/5/165255E7-1014-4D0A-B094-B6A430A6BFFC/vcredist_x64.exe",
        "args": ("/q", "/norestart",),
    },
    {
        "id": "vcredist2012_x86",
        "name": "Visual C++ 2012 (x86)",
        "url": "https://download.microsoft.com/download/1/6/B/16B06F60-3B20-4FF2-B699-5E9B7962F9AE/VSU_4/vcredist_x86.exe",
        "args": ("/install", "/quiet", "/norestart",),
    },
    {
        "id": "vcredist2012_x64",
        "name": "Visual C++ 2012 (x64)",
        "url": "https://download.microsoft.com/download/1/6/B/16B06F60-3B20-4FF2-B699-5E9B7962F9AE/VSU_4/vcredist_x64.exe",
        "args": ("/install", "/quiet", "/norestart",),
    },
    {
        "id": "vcredist2013_x86",
        "name": "Visual C++ 2013 (x86)",
        "url": "https://download.visualstudio.microsoft.com/download/pr/10912113/5da66ddebb0ad32ebd4b922fd82e8e25/vcredist_x86.exe",
        "args": ("/q",),
    },
    {
        "id": "vcredist2013_x64",
        "name": "Visual C++ 2013 (x64)",
        "url": "https://download.visualstudio.microsoft.com/download/pr/10912041/cee5d6bca2ddbcd039da727bf4acb48a/vcredist_x64.exe",
        "args": ("/q",),
    },
    {
        "id": "vcredist2015_2022_x86",
        "name": "Visual C++ 2015–2022 (x86)",
        "url": "https://aka.ms/vs/17/release/vc_redist.x86.exe",
        "args": ("/install", "/quiet", "/norestart",),
    },
    {
        "id": "vcredist2015_2022_x64",
        "name": "Visual C++ 2015–2022 (x64)",
        "url": "https://aka.ms/vs/17/release/vc_redist.x64.exe",
        "args": ("/install", "/quiet", "/norestart",),
    },
    {
        "id": "directx_june2010",
        "name": "DirectX End-User Runtime (June 2010)",
        # Offline redist (~96 MB). dxwebsetup.exe is a tiny stub that re-downloads silently and often hangs headless.
        "url": "https://download.microsoft.com/download/8/4/a/84a35bf1-dafe-4ae8-82af-ad2ae20b6b14/directx_Jun2010_redist.exe",
        "args": ("/Q",),
        "timeout": 2400,
        "wait_images": ("DXSETUP.exe", "directx_Jun2010_redist.exe"),
        "install_hint": "Running DirectX setup (large download + install, up to 40 min)…",
    },
)

_SUCCESS_EXIT_CODES = frozenset({0, 1638, 3010})  # OK, already installed, restart suggested


@dataclass
class RedistJobState:
    running: bool = False
    index: int = 0
    total: int = len(_REDIST_PACKAGES)
    current_name: str = ""
    message: str = ""
    log: list[str] = field(default_factory=list)
    error: str = ""
    finished: bool = False


_lock = threading.Lock()
_state = RedistJobState()
_thread: threading.Thread | None = None


def redist_status() -> dict[str, Any]:
    with _lock:
        return {
            "running": _state.running,
            "index": _state.index,
            "total": _state.total,
            "current_name": _state.current_name,
            "message": _state.message,
            "log": list(_state.log[-40:]),
            "error": _state.error,
            "finished": _state.finished,
            "available": is_windows(),
        }


def start_redist_install(
    on_progress: ProgressCallback | None = None,
    on_line_log: LineLogCallback | None = None,
) -> tuple[bool, str]:
    if not is_windows():
        return False, "Windows only"

    global _thread
    with _lock:
        if _state.running:
            return False, "already_running"
        _state.running = True
        _state.finished = False
        _state.error = ""
        _state.index = 0
        _state.log.clear()
        _state.message = "Starting…"

    def _emit(payload: dict[str, Any]) -> None:
        if on_progress:
            on_progress(payload)

    def _line(line: str, *, update_message: bool = True) -> None:
        with _lock:
            _state.log.append(line)
            if update_message:
                _state.message = line
        if on_line_log:
            on_line_log(line)
        _emit(redist_status())

    def _run() -> None:
        try:
            _run_all(_line)
        except Exception as exc:
            with _lock:
                _state.error = str(exc)
                _state.message = f"Failed: {exc}"
                _state.log.append(_state.message)
            if on_line_log:
                on_line_log(_state.message)
            _emit(redist_status())
        finally:
            with _lock:
                _state.running = False
                _state.finished = True
                if not _state.error:
                    _state.message = "All runtime installers finished."
                _emit(redist_status())

    _thread = threading.Thread(target=_run, name="redist-install", daemon=True)
    _thread.start()
    _line("Starting runtime install (11 packages)…")
    return True, "started"


def _run_all(line: Callable[..., None]) -> None:
    total = len(_REDIST_PACKAGES)
    cache_dir = os.path.join(tempfile.gettempdir(), "QuickPlay_redist")
    os.makedirs(cache_dir, exist_ok=True)
    line(f"Cache folder: {cache_dir}", update_message=False)

    for idx, pkg in enumerate(_REDIST_PACKAGES, start=1):
        name = pkg["name"]
        with _lock:
            _state.index = idx
            _state.current_name = name
        line(f"[{idx}/{total}] {name}")

        ok, detail = _install_one(pkg, cache_dir, line)
        line(f"    {'OK' if ok else 'WARN'}: {detail}", update_message=False)

    line("All runtime installers finished.", update_message=False)


def _download_file(url: str, dest: str, line: Callable[..., None], timeout_dl: int = 600) -> None:
    if os.path.isfile(dest) and os.path.getsize(dest) >= 1024:
        size_mb = os.path.getsize(dest) // (1024 * 1024)
        line(f"    Using cached installer ({size_mb} MB)", update_message=False)
        return

    filename = os.path.basename(dest)
    line(f"    Downloading {filename}…")
    downloaded = 0
    last_logged_mb = -1
    last_logged_pct = -1
    with requests.get(url, stream=True, timeout=(30, timeout_dl)) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length") or 0)
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=256 * 1024):
                if not chunk:
                    continue
                fh.write(chunk)
                downloaded += len(chunk)
                if total > 0:
                    pct = min(100, int(downloaded * 100 / total))
                    if pct >= last_logged_pct + 10:
                        last_logged_pct = pct
                        mb = downloaded // (1024 * 1024)
                        line(f"    Downloaded {mb} MB ({pct}%)…")
                else:
                    mb = downloaded // (1024 * 1024)
                    if mb > last_logged_mb and mb % 5 == 0 and mb > 0:
                        last_logged_mb = mb
                        line(f"    Downloaded {mb} MB…")
    if downloaded < 1024 * 1024:
        line(f"    Download complete ({downloaded // 1024} KB)", update_message=False)
    else:
        size_mb = downloaded // (1024 * 1024)
        line(f"    Download complete ({size_mb} MB)", update_message=False)


def _process_running(image_name: str) -> bool:
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    result = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image_name}", "/NH"],
        capture_output=True,
        text=True,
        creationflags=creationflags,
        timeout=30,
    )
    out = (result.stdout or "").lower()
    return image_name.lower() in out and "no tasks" not in out


def _wait_for_processes(
    image_names: tuple[str, ...],
    timeout_sec: int,
    line: Callable[..., None],
) -> None:
    line("    Waiting for setup to finish…")
    start = time.monotonic()
    heartbeat = 15.0
    while time.monotonic() - start < timeout_sec:
        active = [n for n in image_names if _process_running(n)]
        if not active:
            line("    Setup process finished.", update_message=False)
            return
        elapsed = int(time.monotonic() - start)
        line(f"    …still installing ({elapsed}s): {', '.join(active)}")
        time.sleep(heartbeat)
    line("    Setup still running after wait (may complete in background).", update_message=False)


def _run_installer(
    dest: str,
    args: list[str],
    timeout_run: int,
    line: Callable[..., None],
    hint: str = "",
) -> int:
    creationflags = subprocess.CREATE_NO_WINDOW if hasattr(subprocess, "CREATE_NO_WINDOW") else 0
    default_hint = f"Running installer (up to {timeout_run // 60} min; legacy VC++ can be slow)…"
    line(hint or default_hint)
    proc = subprocess.Popen(
        [dest, *args],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=creationflags,
    )
    start = time.monotonic()
    heartbeat = 15.0
    while True:
        try:
            return proc.wait(timeout=heartbeat)
        except subprocess.TimeoutExpired:
            elapsed = int(time.monotonic() - start)
            if elapsed >= timeout_run:
                proc.kill()
                proc.wait(timeout=30)
                line(f"    Installer timed out after {elapsed}s", update_message=False)
                return -1
            line(f"    …still installing ({elapsed}s)")


def _install_one(pkg: dict[str, Any], cache_dir: str, line: Callable[..., None]) -> tuple[bool, str]:
    url = pkg["url"]
    filename = url.rsplit("/", 1)[-1].split("?")[0] or f"{pkg['id']}.exe"
    dest = os.path.join(cache_dir, filename)
    timeout_run = int(pkg.get("timeout", 600))

    timeout_dl = int(pkg.get("download_timeout", 1200 if timeout_run > 900 else 600))
    _download_file(url, dest, line, timeout_dl=timeout_dl)
    args = list(pkg.get("args") or ())
    code = _run_installer(
        dest,
        args,
        timeout_run,
        line,
        hint=str(pkg.get("install_hint") or ""),
    )
    wait_images = pkg.get("wait_images")
    if wait_images:
        _wait_for_processes(tuple(wait_images), timeout_run, line)
    if code in _SUCCESS_EXIT_CODES:
        return True, f"exit {code}"
    if code == -1:
        return False, "timed out"
    return False, f"exit {code} (installer may still have applied fixes)"


def list_redist_packages() -> list[dict[str, str]]:
    return [{"id": p["id"], "name": p["name"]} for p in _REDIST_PACKAGES]
