from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from .database import Database
from .models import QueueEntry
from .paths import deduplicate_natural, natural_key, validate_video


class QueueService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.root = database.get_root()

    def open(self, root: str | Path) -> Path:
        self.root = self.database.set_root(root)
        return self.root

    def entries(self) -> list[QueueEntry]:
        entries = self.database.queue_entries()
        changed = False
        for entry in entries:
            if not entry.path.is_file() and entry.state != "missing":
                self.database.set_state(entry.id, "missing")
                changed = True
        return self.database.queue_entries() if changed else entries

    def add(self, paths: Sequence[str | Path]) -> list[QueueEntry]:
        if self.root is None:
            raise ValueError("open a library root first")
        valid = [validate_video(path, self.root) for path in paths]
        for path in deduplicate_natural(valid):
            self.database.add_entry(path)
        return self.entries()

    def current(self) -> QueueEntry | None:
        current_id = self.database.get_current_id()
        return next((entry for entry in self.entries() if entry.id == current_id), None)

    def play_now(self, index: int) -> QueueEntry:
        entries = self.entries()
        entry = entries[index]
        current = self.current()
        if current and current.id != entry.id and current.state in {"playing", "paused"}:
            self.database.set_state(current.id, "queued")
        self.database.set_current(entry.id)
        self.database.set_state(entry.id, "playing")
        return next(item for item in self.entries() if item.id == entry.id)

    def next(self, completed: bool = False) -> QueueEntry | None:
        entries = self.entries()
        current = self.current()
        start = -1
        if current:
            start = next(i for i, item in enumerate(entries) if item.id == current.id)
            self.database.set_state(current.id, "completed" if completed else "skipped")
        for entry in entries[start + 1 :]:
            if entry.path.is_file():
                self.database.set_current(entry.id)
                self.database.set_state(entry.id, "playing")
                return next(item for item in self.entries() if item.id == entry.id)
            self.database.set_state(entry.id, "missing")
        self.database.set_current(None)
        return None

    def previous(self) -> QueueEntry | None:
        entries = self.entries()
        current = self.current()
        if not entries:
            return None
        index = next(
            (i for i, item in enumerate(entries) if current and item.id == current.id), len(entries)
        )
        return self.play_now(max(0, index - 1))

    def move(self, index: int, delta: int) -> None:
        entries = self.entries()
        target = max(0, min(len(entries) - 1, index + delta))
        item = entries.pop(index)
        entries.insert(target, item)
        self.database.reorder([entry.id for entry in entries])

    def remove(self, index: int) -> None:
        entry = self.entries()[index]
        if self.database.get_current_id() == entry.id:
            self.database.set_current(None)
        self.database.remove(entry.id)

    def clear_completed(self) -> None:
        for entry in self.entries():
            if entry.state == "completed":
                self.database.remove(entry.id)

    def clear_all(self) -> None:
        self.database.set_current(None)
        for entry in self.entries():
            self.database.remove(entry.id)

    def sort_natural(self) -> None:
        entries = sorted(self.entries(), key=lambda entry: natural_key(entry.path.name))
        self.database.reorder([entry.id for entry in entries])

    def retry(self, index: int) -> QueueEntry:
        entry = self.entries()[index]
        if not entry.path.is_file():
            self.database.set_state(entry.id, "missing")
            raise FileNotFoundError("video is missing")
        return self.play_now(index)

    def update_progress(
        self, path: Path, position_ms: int, duration_ms: int, completed: bool = False
    ) -> None:
        self.database.merge_progress(path.resolve(), position_ms, duration_ms, completed)
