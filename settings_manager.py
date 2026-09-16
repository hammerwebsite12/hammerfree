"""Persistent app settings (download folder, connections, UI prefs)."""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from typing import Any

from app_paths import app_root_dir, settings_file_path


def _normalize_language(value: Any) -> str:
    lang = str(value or "").strip().lower()
    return lang if lang in SUPPORTED_LANGUAGES else DEFAULT_LANGUAGE

MIN_CONNECTIONS = 1
MAX_CONNECTIONS = 64
DEFAULT_CONNECTIONS = 8

SUPPORTED_LANGUAGES = ("en", "zh", "es", "tl")
DEFAULT_LANGUAGE = "en"
APP_VERSION = "2.7.5-beta"
APP_TITLE = "QuickPlay 2.7.5 beta"

STORE_SERVER1 = "server1"
STORE_SERVER2 = "server2"
DEFAULT_STORE = STORE_SERVER1
SUPPORTED_STORES = (STORE_SERVER1, STORE_SERVER2)


def _normalize_store(value: Any) -> str:
    store = str(value or "").strip().lower()
    return store if store in SUPPORTED_STORES else DEFAULT_STORE


@dataclass
class AppSettings:
    download_dir: str
    connections: int = DEFAULT_CONNECTIONS
    show_logs: bool = True
    verification_window_full: bool = False
    defender_exclusion: bool = True
    defender_status: str = ""
    disable_announcement_on_startup: bool = False
    language: str = DEFAULT_LANGUAGE
    hardware_snapshot_submitted: bool = False
    first_license_check_done: bool = False
    store: str = DEFAULT_STORE
    controller_enabled: bool = False
    allow_big_picture: bool = False


class SettingsManager:
    def __init__(self, path: str | None = None, default_download_dir: str | None = None) -> None:
        self.path = path or settings_file_path()
        self._default_download_dir = default_download_dir or app_root_dir()
        self._settings, needs_save = self._load()
        if needs_save:
            self.save()

    def _default_settings(self) -> AppSettings:
        return AppSettings(download_dir=os.path.abspath(self._default_download_dir))

    def _load(self) -> tuple[AppSettings, bool]:
        if not os.path.isfile(self.path):
            return self._default_settings(), True

        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return self._default_settings(), True

        if not isinstance(raw, dict):
            return self._default_settings(), True

        download_dir = raw.get("download_dir") or self._default_download_dir
        connections = int(raw.get("connections", DEFAULT_CONNECTIONS))
        connections = max(MIN_CONNECTIONS, min(MAX_CONNECTIONS, connections))
        settings = AppSettings(
            download_dir=os.path.abspath(download_dir),
            connections=connections,
            show_logs=bool(raw.get("show_logs", True)),
            verification_window_full=bool(raw.get("verification_window_full", False)),
            defender_exclusion=bool(raw.get("defender_exclusion", True)),
            defender_status=str(raw.get("defender_status", "")),
            disable_announcement_on_startup=bool(
                raw.get("disable_announcement_on_startup", False)
            ),
            language=_normalize_language(raw.get("language")),
            hardware_snapshot_submitted=bool(raw.get("hardware_snapshot_submitted", False)),
            first_license_check_done=bool(raw.get("first_license_check_done", False)),
            store=_normalize_store(raw.get("store")),
            controller_enabled=bool(raw.get("controller_enabled", False)),
            allow_big_picture=bool(raw.get("allow_big_picture", False)),
        )

        needs_save = any(
            key not in raw
            for key in (
                "first_license_check_done",
                "hardware_snapshot_submitted",
                "language",
                "disable_announcement_on_startup",
                "store",
                "verification_window_full",
                "controller_enabled",
                "allow_big_picture",
            )
        )
        return settings, needs_save

    def save(self) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(asdict(self._settings), handle, indent=2)

    @property
    def download_dir(self) -> str:
        return self._settings.download_dir

    @property
    def library_path(self) -> str:
        # Library lives in the stable app folder (next to settings.json) so the
        # installed-games list survives changing the download folder. Each entry
        # stores an absolute install_dir, so games remain launchable anywhere.
        return os.path.join(app_root_dir(), "library.json")

    @property
    def connections(self) -> int:
        return self._settings.connections

    @property
    def show_logs(self) -> bool:
        return self._settings.show_logs

    @property
    def verification_window_full(self) -> bool:
        return self._settings.verification_window_full

    @property
    def verification_window_mode(self) -> str:
        if self._settings.verification_window_full:
            return "full"
        return "hidden"

    @property
    def defender_exclusion(self) -> bool:
        return self._settings.defender_exclusion

    @property
    def defender_status(self) -> str:
        return self._settings.defender_status

    @property
    def disable_announcement_on_startup(self) -> bool:
        return self._settings.disable_announcement_on_startup

    @property
    def language(self) -> str:
        return self._settings.language

    @property
    def hardware_snapshot_submitted(self) -> bool:
        return self._settings.hardware_snapshot_submitted

    @property
    def first_license_check_done(self) -> bool:
        return self._settings.first_license_check_done

    @property
    def store(self) -> str:
        return self._settings.store

    @property
    def controller_enabled(self) -> bool:
        return self._settings.controller_enabled

    def set_store(self, store: str) -> None:
        self._settings.store = _normalize_store(store)
        self.save()

    def mark_first_license_check_done(self) -> None:
        self._settings.first_license_check_done = True
        self.save()

    def set_hardware_snapshot_submitted(self, submitted: bool = True) -> None:
        self._settings.hardware_snapshot_submitted = bool(submitted)
        self.save()

    def set_defender_status(self, message: str) -> None:
        self._settings.defender_status = message
        self.save()

    def update(self, **kwargs: Any) -> AppSettings:
        if "download_dir" in kwargs and kwargs["download_dir"]:
            path = os.path.abspath(str(kwargs["download_dir"]).strip())
            os.makedirs(path, exist_ok=True)
            self._settings.download_dir = path
        if "connections" in kwargs:
            self._settings.connections = max(
                MIN_CONNECTIONS,
                min(MAX_CONNECTIONS, int(kwargs["connections"])),
            )
        if "show_logs" in kwargs:
            self._settings.show_logs = bool(kwargs["show_logs"])
        if "verification_window_full" in kwargs:
            self._settings.verification_window_full = bool(kwargs["verification_window_full"])
        if "defender_exclusion" in kwargs:
            self._settings.defender_exclusion = bool(kwargs["defender_exclusion"])
        if "disable_announcement_on_startup" in kwargs:
            self._settings.disable_announcement_on_startup = bool(
                kwargs["disable_announcement_on_startup"]
            )
        if "language" in kwargs:
            self._settings.language = _normalize_language(kwargs["language"])
        if "store" in kwargs:
            self._settings.store = _normalize_store(kwargs["store"])
        if "controller_enabled" in kwargs:
            self._settings.controller_enabled = bool(kwargs["controller_enabled"])
        if "allow_big_picture" in kwargs:
            self._settings.allow_big_picture = bool(kwargs["allow_big_picture"])
        self.save()
        return self._settings

    def to_dict(self) -> dict[str, Any]:
        from store_manager import STORE_OPTIONS

        data = asdict(self._settings)
        data["library_path"] = self.library_path
        data["min_connections"] = MIN_CONNECTIONS
        data["max_connections"] = MAX_CONNECTIONS
        data["default_connections"] = DEFAULT_CONNECTIONS
        data["supported_languages"] = list(SUPPORTED_LANGUAGES)
        data["store_options"] = list(STORE_OPTIONS)
        data["app_version"] = APP_VERSION
        data["app_title"] = APP_TITLE
        return data
