"""Thread-safe download / extract activity logger (in-memory only, sanitized)."""

from __future__ import annotations

import threading
import traceback
from datetime import datetime
from typing import Callable

from log_sanitize import sanitize_log_message

LogCallback = Callable[[str, str, str], None]  # level, message, timestamp


class DownloadLogger:
    def __init__(self, on_log: LogCallback | None = None) -> None:
        self._on_log = on_log
        self._lock = threading.Lock()
        self._history: list[tuple[str, str, str]] = []

    def log(self, message: str, level: str = "INFO") -> None:
        safe_message = sanitize_log_message(message)
        timestamp = datetime.now().strftime("%H:%M:%S")
        entry = (level, safe_message, timestamp)
        with self._lock:
            self._history.append(entry)
        if self._on_log:
            self._on_log(level, safe_message, timestamp)

    def exception(self, message: str) -> None:
        detail = traceback.format_exc().strip()
        self.error(f"{message}\n{detail}")

    def info(self, message: str) -> None:
        self.log(message, "INFO")

    def warn(self, message: str) -> None:
        self.log(message, "WARN")

    def error(self, message: str) -> None:
        self.log(message, "ERROR")

    def debug(self, message: str) -> None:
        self.log(message, "DEBUG")

    def history(self) -> list[tuple[str, str, str]]:
        with self._lock:
            return list(self._history)
