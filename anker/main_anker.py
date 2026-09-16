"""QuickPlay Anker Test — pywebview + FastAPI (AnkerGames store + dedicated Workers)."""

from __future__ import annotations

import os
import sys

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if getattr(sys, "frozen", False):
    _bundle = getattr(sys, "_MEIPASS", _REPO_ROOT)
    if _bundle not in sys.path:
        sys.path.insert(0, _bundle)
elif _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("QUICKPLAY_APP_NAME", "QuickPlay Anker Test")
os.environ.setdefault("QUICKPLAY_STORE", "ankergames")

# Patches must run before backend/download_service import PlayZipClient.
from anker.patch_store import apply as _apply_store
from anker.patch_app import APP_NAME, apply as _apply_app

_apply_store()
_apply_app()

import socket
import subprocess
import sys
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


def main() -> None:
    ensure_admin_or_exit()
    if not acquire_single_instance():
        notify_already_running()
        sys.exit(0)
    init_backend()
    from backend.server import get_logger, get_settings

    logger = get_logger()
    logger.info(f"=== {APP_NAME} starting (AnkerGames) ===")

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

    webview.create_window(
        APP_NAME,
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
    main()
