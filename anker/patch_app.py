"""Anker test build patches — data paths and single-instance guard."""

from __future__ import annotations

import os

APP_NAME = "QuickPlay Anker Test"
SETTINGS_NAME = "settings_anker_test.json"
LIBRARY_NAME = "library_anker_test.json"
MUTEX_NAME = "Global\\QuickPlayAnkerTest_SingleInstance_Mutex"


def apply() -> None:
    import app_paths
    import single_instance

    root = app_paths.app_root_dir()

    def _settings_path() -> str:
        return os.path.join(root, SETTINGS_NAME)

    def _library_path(_self) -> str:
        return os.path.join(root, LIBRARY_NAME)

    app_paths.settings_file_path = _settings_path

    import settings_manager

    settings_manager.SettingsManager.library_path = property(_library_path)

    single_instance._MUTEX_NAME = MUTEX_NAME
    single_instance._WINDOW_TITLE = APP_NAME

    _orig_notify = single_instance.notify_already_running

    def _notify_anker() -> None:
        try:
            single_instance._focus_existing_window()
            from native_dialog import show_info

            show_info(
                f"{APP_NAME} is still running.\n\n"
                "Close the other window or switch to it.",
                title=APP_NAME,
            )
        except Exception:
            pass

    single_instance.notify_already_running = _notify_anker
