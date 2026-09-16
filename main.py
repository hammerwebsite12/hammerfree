"""QuickPlay — Web UI launcher (pywebview + FastAPI)."""

from __future__ import annotations

from worker_tls import install_worker_tls_guard

install_worker_tls_guard()

import os
import socket
import subprocess
import sys


def _ensure_ssl_bundle() -> None:
    """PyInstaller onefile: ensure requests/urllib can verify HTTPS in the frozen EXE."""
    try:
        import certifi
    except ImportError:
        return
    ca = certifi.where()
    os.environ.setdefault("SSL_CERT_FILE", ca)
    os.environ.setdefault("REQUESTS_CA_BUNDLE", ca)


_ensure_ssl_bundle()

import threading
import time
import urllib.error
import urllib.request

import uvicorn
import webview

from app_paths import icon_path
from backend.server import app, init_backend
from single_instance import acquire_single_instance, notify_already_running
from win_elevate import ensure_admin_or_exit
from defender_utils import warn_defender_exclusion_on_startup
from license_manager import perform_first_startup_license_check
from anker.gate_resolver_worker import is_gate_worker_argv, run_gate_worker_cli


def find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(url: str, timeout: float = 5.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            urllib.request.urlopen(url, timeout=0.3)
            return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.05)
    return False


class NativeApi:
    """Exposed to the web UI via pywebview.api."""

    def pick_folder(self, initial: str = "") -> str:
        if not webview.windows:
            return ""
        directory = initial if initial and os.path.isdir(initial) else ""
        result = webview.windows[0].create_file_dialog(
            webview.FOLDER_DIALOG,
            directory=directory,
        )
        if result and len(result) > 0:
            return result[0]
        return ""

    def open_url(self, url: str) -> bool:
        """Open an http(s) link in the user's default browser."""
        target = (url or "").strip()
        if not target.startswith(("http://", "https://")):
            return False
        try:
            subprocess.Popen(
                ["cmd", "/c", "start", "", target],
                shell=False,
            )
            return True
        except OSError:
            return False

    def resolve_gate(self, gate_url: str) -> dict:
        """Open Server 2 download gate verification (main UI thread)."""
        target = (gate_url or "").strip()
        if not target.startswith("http"):
            return {"ok": False, "error": "Invalid gate URL."}
        from anker.gate_bridge import complete_gate_resolve
        from anker.gate_resolver_worker import start_gate_overlay

        def on_complete(url_gate: str, url: str, error: str) -> None:
            complete_gate_resolve(url_gate, result_url=url, error=error)

        start_gate_overlay(target, 120.0, on_complete)
        return {"ok": True}

    resolve_anker_gate = resolve_gate


def main() -> None:
    ensure_admin_or_exit()
    # Only elevated instances reach this point (the launcher relaunches and
    # exits above), so the mutex never races with the UAC relaunch.
    if not acquire_single_instance():
        notify_already_running()
        sys.exit(0)
    init_backend()
    from backend.server import get_settings

    settings = get_settings()
    perform_first_startup_license_check(settings)
    warn_defender_exclusion_on_startup(
        settings.download_dir,
        settings.defender_exclusion,
        settings_manager=settings,
    )
    port = find_free_port()
    host = "127.0.0.1"
    url = f"http://{host}:{port}"

    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="warning",
        access_log=False,
    )
    server = uvicorn.Server(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    if not wait_for_server(url):
        print("Failed to start local server.", file=sys.stderr)
        sys.exit(1)

    from settings_manager import APP_TITLE

    webview.create_window(
        APP_TITLE,
        url,
        width=1180,
        height=760,
        min_size=(720, 520),
        background_color="#0b0a0a",
        js_api=NativeApi(),
    )
    app_icon = icon_path()
    webview.start(icon=app_icon)
    server.should_exit = True


if __name__ == "__main__":
    import multiprocessing as mp

    mp.freeze_support()
    if is_gate_worker_argv():
        raise SystemExit(run_gate_worker_cli(sys.argv[2]))
    main()
