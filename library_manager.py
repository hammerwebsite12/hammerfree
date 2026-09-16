"""Persistent library storage for installed games."""

from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
import uuid
import html
import glob
from dataclasses import asdict, dataclass
from typing import Any


@dataclass
class LibraryEntry:
    entry_id: str
    title: str
    game_id: str
    install_dir: str
    exe_path: str = ""
    image_url: str = ""
    added_at: float = 0.0

    @staticmethod
    def create(
        title: str,
        game_id: str,
        install_dir: str,
        image_url: str = "",
        exe_path: str = "",
    ) -> "LibraryEntry":
        import time

        return LibraryEntry(
            entry_id=str(uuid.uuid4()),
            title=html.unescape(title),
            game_id=game_id,
            install_dir=install_dir,
            exe_path=exe_path,
            image_url=image_url,
            added_at=time.time(),
        )


class LibraryManager:
    def __init__(self, library_path: str) -> None:
        self.library_path = library_path
        self.entries: list[LibraryEntry] = []
        self.load_warning: str = ""
        self.load()

    @staticmethod
    def _normalize_dir(path: str) -> str:
        return os.path.normcase(os.path.abspath(path or ""))

    def known_install_dirs(self) -> set[str]:
        return {self._normalize_dir(e.install_dir) for e in self.entries if e.install_dir}

    def find_by_install_dir(self, install_dir: str) -> LibraryEntry | None:
        target = self._normalize_dir(install_dir)
        for entry in self.entries:
            if self._normalize_dir(entry.install_dir) == target:
                return entry
        return None

    def load(self) -> None:
        self.load_warning = ""
        self.entries = []
        if not os.path.exists(self.library_path):
            return
        try:
            with open(self.library_path, encoding="utf-8") as handle:
                raw = json.load(handle)
        except json.JSONDecodeError:
            self._backup_corrupt_file("decode_error")
            self.load_warning = "library_corrupt"
            return
        except OSError:
            self.load_warning = "library_read_failed"
            return

        if not isinstance(raw, dict):
            self._backup_corrupt_file("invalid_shape")
            self.load_warning = "library_corrupt"
            return

        games = raw.get("games", [])
        if not isinstance(games, list):
            self._backup_corrupt_file("invalid_games")
            self.load_warning = "library_corrupt"
            return

        parsed: list[LibraryEntry] = []
        for item in games:
            if not isinstance(item, dict):
                continue
            try:
                parsed.append(
                    LibraryEntry(
                        **{
                            **item,
                            "title": html.unescape(str(item.get("title", ""))),
                        }
                    )
                )
            except TypeError:
                continue
        self.entries = parsed

    def try_restore_from_backup(self) -> bool:
        """Load the newest valid corrupt backup into this library file."""
        directory = os.path.dirname(os.path.abspath(self.library_path)) or "."
        base = os.path.basename(self.library_path)
        pattern = os.path.join(directory, f"{base}.corrupt.*.bak")
        backups = sorted(glob.glob(pattern), reverse=True)
        for path in backups:
            trial = LibraryManager(path)
            if trial.load_warning or not trial.entries:
                continue
            self.entries = trial.entries
            self.load_warning = ""
            self.save()
            return True
        return False

    def clear_load_warning(self) -> None:
        self.load_warning = ""

    def _backup_corrupt_file(self, reason: str) -> None:
        try:
            stamp = uuid.uuid4().hex[:8]
            backup = f"{self.library_path}.corrupt.{reason}.{stamp}.bak"
            shutil.copy2(self.library_path, backup)
        except OSError:
            pass

    def save(self) -> None:
        directory = os.path.dirname(self.library_path) or "."
        os.makedirs(directory, exist_ok=True)
        payload = {"games": [asdict(entry) for entry in self.entries]}

        fd, tmp_path = tempfile.mkstemp(
            suffix=".tmp",
            prefix="library.",
            dir=directory,
            text=True,
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_path, self.library_path)
        except OSError:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
            raise

    def merge_from_file(self, other_path: str) -> int:
        """Import entries from another library.json (e.g. legacy download folder)."""
        if not other_path or not os.path.isfile(other_path):
            return 0
        if os.path.normcase(os.path.abspath(other_path)) == os.path.normcase(
            os.path.abspath(self.library_path)
        ):
            return 0

        other = LibraryManager(other_path)
        if other.load_warning:
            return 0

        added = 0
        known = self.known_install_dirs()
        for entry in other.entries:
            norm = self._normalize_dir(entry.install_dir)
            if not norm or norm in known:
                continue
            if not os.path.isdir(entry.install_dir):
                continue
            self.entries.append(entry)
            known.add(norm)
            added += 1
        if added:
            self.save()
        return added

    def add(self, entry: LibraryEntry) -> None:
        norm = self._normalize_dir(entry.install_dir)
        self.entries = [
            e
            for e in self.entries
            if e.game_id != entry.game_id
            and self._normalize_dir(e.install_dir) != norm
        ]
        self.entries.insert(0, entry)
        self.save()

    def update_exe(self, entry_id: str, exe_path: str) -> None:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                entry.exe_path = exe_path
                self.save()
                return

    def remove(self, entry_id: str) -> LibraryEntry | None:
        removed: LibraryEntry | None = None
        kept: list[LibraryEntry] = []
        for entry in self.entries:
            if entry.entry_id == entry_id:
                removed = entry
            else:
                kept.append(entry)
        if removed:
            self.entries = kept
            self.save()
        return removed

    def get(self, entry_id: str) -> LibraryEntry | None:
        for entry in self.entries:
            if entry.entry_id == entry_id:
                return entry
        return None

    def update_entry(
        self,
        entry_id: str,
        *,
        title: str | None = None,
        game_id: str | None = None,
        image_url: str | None = None,
    ) -> bool:
        for entry in self.entries:
            if entry.entry_id != entry_id:
                continue
            if title:
                entry.title = html.unescape(title)
            if game_id:
                entry.game_id = game_id
            if image_url:
                entry.image_url = image_url
            self.save()
            return True
        return False

    @staticmethod
    def safe_folder_name(title: str) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*]', "", title).strip()
        cleaned = re.sub(r"\s+", " ", cleaned)
        return cleaned or "Game"
