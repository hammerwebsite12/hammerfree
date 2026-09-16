"""Persist pending EXE picker state across app restarts."""

from __future__ import annotations

import html
import json
import os
import tempfile
from dataclasses import asdict, dataclass
from typing import Any


def _migrate_legacy_file(old_path: str, new_path: str) -> None:
    """Rename a pre-rebrand data file to its new name, once."""
    try:
        if os.path.isfile(old_path) and not os.path.exists(new_path):
            os.replace(old_path, new_path)
    except OSError:
        pass


@dataclass
class StoredExePicker:
    task_id: str
    title: str
    game_id: str
    image_url: str
    install_dir: str
    exes: list[str]
    default_exe: str
    entry_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> StoredExePicker:
        return cls(
            task_id=str(raw.get("task_id", "")),
            title=html.unescape(str(raw.get("title", ""))),
            game_id=str(raw.get("game_id", "")),
            image_url=str(raw.get("image_url", "")),
            install_dir=str(raw.get("install_dir", "")),
            exes=[str(p) for p in raw.get("exes", []) if p],
            default_exe=str(raw.get("default_exe", "")),
            entry_id=str(raw.get("entry_id", "")),
        )


class PendingExeStore:
    _LEGACY_NAME = ".playzip_pending_exe.json"
    _NAME = ".quickplay_pending_exe.json"

    def __init__(self, download_dir: str) -> None:
        self.path = os.path.join(download_dir, self._NAME)
        _migrate_legacy_file(os.path.join(download_dir, self._LEGACY_NAME), self.path)

    def load(self) -> dict[str, StoredExePicker]:
        if not os.path.isfile(self.path):
            return {}
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, list):
                return {}
            items: dict[str, StoredExePicker] = {}
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                parsed = StoredExePicker.from_dict(entry)
                if parsed.task_id and parsed.install_dir:
                    items[parsed.task_id] = parsed
            return items
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return {}

    def save_all(self, items: dict[str, StoredExePicker]) -> None:
        directory = os.path.dirname(self.path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = [item.to_dict() for item in items.values()]
        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix="pending_exe.",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def remove(self, task_id: str) -> None:
        items = self.load()
        if task_id in items:
            del items[task_id]
            self.save_all(items)
