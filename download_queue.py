"""Persist in-progress downloads so they can resume after app restart."""

from __future__ import annotations

import json
import os
import time
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
class QueuedDownload:
    game_id: str
    title: str
    image_url: str
    url: str
    dest_path: str
    connections: int
    updated_at: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> QueuedDownload:
        return cls(
            game_id=str(raw.get("game_id", "")),
            title=str(raw.get("title", "")),
            image_url=str(raw.get("image_url", "")),
            url=str(raw.get("url", "")),
            dest_path=str(raw.get("dest_path", "")),
            connections=int(raw.get("connections", 8)),
            updated_at=float(raw.get("updated_at", 0)),
        )


class DownloadQueueStore:
    _LEGACY_NAME = ".playzip_downloads.json"
    _NAME = ".quickplay_downloads.json"

    def __init__(self, download_dir: str) -> None:
        self.path = os.path.join(download_dir, self._NAME)
        _migrate_legacy_file(os.path.join(download_dir, self._LEGACY_NAME), self.path)

    def load(self) -> list[QueuedDownload]:
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, list):
                return []
            return [QueuedDownload.from_dict(item) for item in raw if isinstance(item, dict)]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []

    def save_all(self, items: list[QueuedDownload]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = [item.to_dict() for item in items]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def upsert(self, item: QueuedDownload) -> None:
        item.updated_at = time.time()
        items = [entry for entry in self.load() if entry.dest_path != item.dest_path]
        items.append(item)
        self.save_all(items)

    def remove(self, dest_path: str) -> None:
        items = [entry for entry in self.load() if entry.dest_path != dest_path]
        self.save_all(items)

    def remove_by_game_id(self, game_id: str) -> list[str]:
        """Drop all resume records for a game; return dest paths that were removed."""
        removed_paths: list[str] = []
        remaining = []
        for entry in self.load():
            if entry.game_id == game_id:
                if entry.dest_path:
                    removed_paths.append(entry.dest_path)
            else:
                remaining.append(entry)
        self.save_all(remaining)
        return removed_paths
