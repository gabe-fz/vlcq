from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .database import Database
from .models import QueueEntry
from .paths import deduplicate_natural, is_beneath, natural_key, validate_video


class ActivePlaybackError(RuntimeError):
    """Raised when queue mutation would abandon owned active playback."""


@dataclass
class QueueUndoSnapshot:
    rows: list[dict[str, int | str]]
    ordered_ids: list[int]
    fingerprints: dict[int, tuple[int, int, int, int]]


class QueueService:
    def __init__(self, database: Database) -> None:
        self.database = database
        self.root = database.get_root()
        self._undo: QueueUndoSnapshot | None = None
        self._undo_expired = False

    def _invalidate_undo(self) -> None:
        if self._undo is not None:
            self._undo_expired = True
        self._undo = None

    def _replace_undo(self, snapshot: QueueUndoSnapshot) -> None:
        # A successful removal replaces the previous undo with a newer,
        # actionable snapshot.  There is no useful expired state to expose in
        # that case because the new operation can be undone immediately.
        self._undo = snapshot
        self._undo_expired = False

    def open(self, root: str | Path) -> Path:
        self.root = self.database.set_root(root)
        self._invalidate_undo()
        return self.root

    def entries(self) -> list[QueueEntry]:
        entries = self.database.queue_entries()
        changed = False
        for entry in entries:
            if not entry.path.is_file() and entry.state != "missing":
                self.database.set_state(entry.id, "missing")
                changed = True
        return self.database.queue_entries() if changed else entries

    def _validated(self, paths: Sequence[str | Path]) -> list[Path]:
        if self.root is None:
            raise ValueError("open a library root first")
        valid = [validate_video(path, self.root) for path in paths]
        return deduplicate_natural(valid)

    def add(self, paths: Sequence[str | Path]) -> list[QueueEntry]:
        valid = self._validated(paths)
        self.database.append_entries(valid)
        self._invalidate_undo()
        return self.entries()

    add_to_end = add

    def play_next(self, paths: Sequence[str | Path]) -> list[QueueEntry]:
        valid = self._validated(paths)
        self.database.play_next_entries(valid)
        self._invalidate_undo()
        return self.entries()

    def current(self) -> QueueEntry | None:
        current_id = self.database.get_current_id()
        return next((entry for entry in self.entries() if entry.id == current_id), None)

    def resume_target_index(self) -> int | None:
        """Resolve the persisted unfinished queue item by identity.

        The current pointer is deliberately resolved against entry ids rather
        than a saved list position.  A completed pointer is not resumable, so
        the first still-unfinished entry is used as the documented fallback.
        """
        entries = self.entries()
        current_id = self.database.get_current_id()
        ordered = [
            index
            for index, entry in enumerate(entries)
            if entry.id == current_id
        ] + [
            index
            for index, entry in enumerate(entries)
            if entry.id != current_id
        ]
        for index in ordered:
            entry = entries[index]
            if entry.state == "completed":
                continue
            history = self.database.history_for(entry.path, root=self.root)
            if history is not None and history.completion_observed:
                continue
            return index
        return None

    def play_now(self, index: int) -> QueueEntry:
        entries = self.entries()
        entry = entries[index]
        current = self.current()
        if current and current.id != entry.id and current.state in {"playing", "paused"}:
            self.database.set_state(current.id, "queued")
        self.database.set_current(entry.id)
        self.database.set_state(entry.id, "playing")
        self._invalidate_undo()
        return next(item for item in self.entries() if item.id == entry.id)

    def next(self, completed: bool = False) -> QueueEntry | None:
        self._invalidate_undo()
        entries = self.entries()
        current = self.current()
        start = -1
        if current:
            start = next(i for i, item in enumerate(entries) if item.id == current.id)
            # Missing/failed entries are already terminal; retain that useful
            # diagnosis while advancing rather than relabeling it as skipped.
            if current.state not in {"missing", "failed", "completed", "skipped"}:
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
        self._invalidate_undo()
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
        if not 0 <= index < len(entries):
            raise IndexError("queue index out of range")
        target = max(0, min(len(entries) - 1, index + delta))
        item = entries.pop(index)
        entries.insert(target, item)
        self.database.reorder([entry.id for entry in entries])
        self._invalidate_undo()

    def _snapshot(self, selected: list[QueueEntry]) -> QueueUndoSnapshot:
        all_entries = self.entries()
        selected_ids = {entry.id for entry in selected}
        selected = [entry for entry in all_entries if entry.id in selected_ids]
        rows: list[dict[str, int | str]] = []
        fingerprints: dict[int, tuple[int, int, int, int]] = {}
        for entry in selected:
            rows.append(
                {
                    "id": entry.id,
                    "queue_id": self.database.active_queue_id(),
                    "position": entry.position,
                    "media_id": entry.media_id,
                    "state": entry.state,
                }
            )
            fingerprints[entry.id] = self.database.media_fingerprint(entry.media_id)
        return QueueUndoSnapshot(
            rows=rows,
            ordered_ids=[entry.id for entry in all_entries],
            fingerprints=fingerprints,
        )

    def remove(self, index: int, *, stop_confirmed: bool = False) -> None:
        entries = self.entries()
        if not 0 <= index < len(entries):
            raise IndexError("queue index out of range")
        entry = entries[index]
        current = self.current()
        if (
            current is not None
            and current.id == entry.id
            and current.state in {"playing", "paused"}
            and not stop_confirmed
        ):
            raise ActivePlaybackError("stop VLC playback before removing the active entry")
        if current is not None and current.id == entry.id and stop_confirmed:
            self.database.set_state(entry.id, "stopped")
        self._replace_undo(self._snapshot([entry]))
        self.database.remove_entries([entry.id])

    def clear_completed(self, *, stop_confirmed: bool = False) -> None:
        selected = [entry for entry in self.entries() if entry.state == "completed"]
        if not selected:
            return
        current = self.current()
        if (
            current is not None
            and current in selected
            and current.state in {"playing", "paused"}
            and not stop_confirmed
        ):
            raise ActivePlaybackError("stop VLC playback before clearing the active entry")
        if current is not None and current in selected and stop_confirmed:
            self.database.set_state(current.id, "stopped")
        self._replace_undo(self._snapshot(selected))
        self.database.remove_entries([entry.id for entry in selected])

    def clear_all(self, *, stop_confirmed: bool = False) -> None:
        selected = self.entries()
        if not selected:
            return
        current = self.current()
        if (
            current is not None
            and current.state in {"playing", "paused"}
            and not stop_confirmed
        ):
            raise ActivePlaybackError("stop VLC playback before clearing the active entry")
        if current is not None and current.state in {"playing", "paused"} and stop_confirmed:
            self.database.set_state(current.id, "stopped")
        self._replace_undo(self._snapshot(selected))
        self.database.remove_entries([entry.id for entry in selected])

    def undo(self) -> bool:
        snapshot = self._undo
        if snapshot is None:
            return False
        try:
            if self.root is None:
                raise RuntimeError("open a library root first")
            for row in snapshot.rows:
                path_row = self.database.connection.execute(
                    "SELECT path FROM media WHERE id=?", (int(row["media_id"]),)
                ).fetchone()
                if path_row is None:
                    raise RuntimeError("undo media record is missing")
                path = Path(path_row[0]).resolve(strict=False)
                if not is_beneath(path, self.root):
                    raise ValueError("undo refused a path outside the library root")
                try:
                    stat = path.stat()
                except FileNotFoundError:
                    # Missing media is restored as a visible missing queue row.
                    continue
                fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
                if fingerprint != snapshot.fingerprints[int(row["id"])]:
                    raise ValueError("undo refused a replaced media file")
            self.database.restore_entries(snapshot.rows, snapshot.ordered_ids)
        except (OSError, RuntimeError, ValueError):
            self._undo = None
            raise
        self._undo = None
        return True

    @property
    def undo_available(self) -> bool:
        return self._undo is not None

    @property
    def undo_expired(self) -> bool:
        return self._undo_expired

    def consume_undo_expired(self) -> bool:
        """Return and clear the one-shot UI notification for expired undo."""
        expired = self._undo_expired
        self._undo_expired = False
        return expired

    def invalidate_undo(self) -> None:
        self._invalidate_undo()

    def sort_natural(self) -> None:
        entries = sorted(self.entries(), key=lambda entry: natural_key(entry.path.name))
        self.database.reorder([entry.id for entry in entries])
        self._invalidate_undo()

    def retry(self, index: int) -> QueueEntry:
        entry = self.entries()[index]
        if not entry.path.is_file():
            self.database.set_state(entry.id, "missing")
            raise FileNotFoundError("video is missing")
        return self.play_now(index)

    def update_progress(
        self,
        path: Path,
        position_ms: int,
        duration_ms: int,
        completed: bool = False,
        *,
        trustworthy: bool = True,
        resume_position_ms: int | None = None,
        allow_resume_reset: bool = False,
    ) -> None:
        self.database.merge_progress(
            path.resolve(),
            position_ms,
            duration_ms,
            completed,
            trustworthy=trustworthy,
            resume_position_ms=resume_position_ms,
            allow_resume_reset=allow_resume_reset,
        )
