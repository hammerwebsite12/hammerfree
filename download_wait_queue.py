"""Persist games waiting for a free download slot (one active download at a time)."""

from __future__ import annotations

import json
import os
import time
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class WaitQueueItem:
    game_id: str
    title: str
    image_url: str
    task_id: str
    queued_at: float = 0.0
    dest_path: str = ""
    connections: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> WaitQueueItem:
        return cls(
            game_id=str(raw.get("game_id", "")),
            title=str(raw.get("title", "")),
            image_url=str(raw.get("image_url", "")),
            task_id=str(raw.get("task_id", "")),
            queued_at=float(raw.get("queued_at", 0)),
            dest_path=str(raw.get("dest_path", "")),
            connections=int(raw.get("connections", 0)),
        )


class DownloadWaitQueueStore:
    _NAME = ".quickplay_wait_queue.json"

    def __init__(self, download_dir: str) -> None:
        self.path = os.path.join(download_dir, self._NAME)

    def load(self) -> list[WaitQueueItem]:
        if not os.path.isfile(self.path):
            return []
        try:
            with open(self.path, encoding="utf-8") as handle:
                raw = json.load(handle)
            if not isinstance(raw, list):
                return []
            return [
                WaitQueueItem.from_dict(item)
                for item in raw
                if isinstance(item, dict)
            ]
        except (OSError, json.JSONDecodeError, TypeError, ValueError):
            return []

    def save_all(self, items: list[WaitQueueItem]) -> None:
        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        payload = [item.to_dict() for item in items]
        with open(self.path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2)

    def append(self, item: WaitQueueItem) -> None:
        item.queued_at = time.time()
        items = self.load()
        items = [
            entry
            for entry in items
            if entry.task_id != item.task_id and entry.game_id != item.game_id
        ]
        items.append(item)
        self.save_all(items)

    def remove(self, task_id: str) -> None:
        items = [entry for entry in self.load() if entry.task_id != task_id]
        self.save_all(items)

    def remove_by_game_id(self, game_id: str) -> None:
        items = [entry for entry in self.load() if entry.game_id != game_id]
        self.save_all(items)

    def pop_first(self) -> WaitQueueItem | None:
        items = self.load()
        if not items:
            return None
        first = items.pop(0)
        self.save_all(items)
        return first

    def contains_game(self, game_id: str) -> bool:
        return any(item.game_id == game_id for item in self.load())
