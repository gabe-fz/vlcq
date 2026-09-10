from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .database import Database
from .models import QueueEntry
from .paths import VIDEO_EXTENSIONS, deduplicate_natural, is_beneath, natural_key, validate_video


class ActivePlaybackError(RuntimeError):
    """Raised when queue mutation would abandon owned active playback."""


@dataclass
class QueueUndoSnapshot:
    rows: list[dict[str, int | str]]
    ordered_ids: list[int]
    fingerprints: dict[int, tuple[int, int, int, int]]


class QueueService:
    def __init__(self, database: Database, watched_percent: int = 90) -> None:
        if not 1 <= int(watched_percent) <= 100:
            raise ValueError("watched threshold must be an integer from 1 through 100")
        self.database = database
        self.watched_percent = int(watched_percent)
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
        # Repair legacy/current databases every time an active queue is opened;
        # this is identity-only normalization and never touches media files.
        self.database.normalize_active_queue()
        self._invalidate_undo()
        return self.root

    def entries(self) -> list[QueueEntry]:
        entries = self.database.queue_entries()
        changed = False
        for entry in entries:
            # Diagnose missing files only when their canonical identity remains
            # inside the active root. Unsafe rows stay visible and unchanged
            # for recovery instead of being rewritten during a browse pass.
            try:
                canonical = entry.path.expanduser().resolve(strict=False)
                confined = self.root is not None and is_beneath(canonical, self.root)
            except (OSError, RuntimeError):
                confined = False
            if confined and not canonical.is_file() and entry.state != "missing":
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

    def selected(self) -> QueueEntry | None:
        selected_id = self.database.get_selected_id()
        return next((entry for entry in self.entries() if entry.id == selected_id), None)

    def select(self, entry_id: int | None) -> None:
        self.database.set_selected(entry_id)

    def set_current_state(self, entry_id: int, state: str) -> None:
        """Update the current row while preserving the queue invariant."""
        current = self.current()
        if current is None or current.id != entry_id:
            raise ValueError("state transition target is not the current queue entry")
        self.database.transition_current(entry_id, state)

    def resume_target_index(self) -> int | None:
        """Resolve the persisted unfinished queue item by identity.

        The current pointer is deliberately resolved against entry ids rather
        than a saved list position.  A completed pointer is not resumable, so
        the first still-unfinished entry is used as the documented fallback.
        """
        entries = self.entries()
        current_id = self.database.get_current_id()
        ordered = [index for index, entry in enumerate(entries) if entry.id == current_id] + [
            index for index, entry in enumerate(entries) if entry.id != current_id
        ]
        for index in ordered:
            entry = entries[index]
            if entry.state == "completed":
                continue
            history = self.database.history_for(entry.path, root=self.root)
            if history is not None and history.coverage_watched(self.watched_percent):
                continue
            return index
        return None

    def play_now(self, index: int) -> QueueEntry:
        entries = self.entries()
        entry = entries[index]
        self.database.transition_current(entry.id, "playing")
        self._invalidate_undo()
        return next(item for item in self.entries() if item.id == entry.id)

    def next(self, completed: bool = False, *, state: str = "playing") -> QueueEntry | None:
        if state not in {"playing", "paused", "stopped"}:
            raise ValueError("next state must be transient playback state")
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
            canonical: Path | None = None
            try:
                canonical = entry.path.expanduser().resolve(strict=False)
                safe_file = (
                    self.root is not None
                    and is_beneath(canonical, self.root)
                    and canonical.suffix.casefold() in VIDEO_EXTENSIONS
                    and canonical.is_file()
                )
            except (OSError, RuntimeError):
                safe_file = False
            if safe_file:
                self.database.transition_current(entry.id, state)
                return next(item for item in self.entries() if item.id == entry.id)
            try:
                confined = (
                    canonical is not None
                    and self.root is not None
                    and is_beneath(canonical, self.root)
                )
            except (OSError, RuntimeError):
                confined = False
            if confined and canonical is not None and not canonical.is_file():
                self.database.set_state(entry.id, "missing")
        self.database.transition_current(None)
        return None

    def reconcile_successor(
        self,
        current_id: int,
        successor_id: int,
        successor_state: str,
        *,
        progress_path: Path | None = None,
        position_ms: int = 0,
        duration_ms: int = 0,
        completed: bool = False,
        trustworthy: bool = True,
        resume_position_ms: int | None = None,
    ) -> QueueEntry:
        """Atomically adopt a VLC-started successor and record old-item history."""
        self.database.reconcile_successor_transition(
            current_id,
            successor_id,
            successor_state,
            progress_path=progress_path,
            position_ms=position_ms,
            duration_ms=duration_ms,
            completed=completed,
            trustworthy=trustworthy,
            resume_position_ms=resume_position_ms,
        )
        self._invalidate_undo()
        return next(entry for entry in self.entries() if entry.id == successor_id)

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
            self.set_current_state(entry.id, "stopped")
        self._replace_undo(self._snapshot([entry]))
        self.database.remove_entries([entry.id])

    def clear_completed(self, *, stop_confirmed: bool = False) -> None:
        selected = []
        for entry in self.entries():
            if entry.state == "completed":
                selected.append(entry)
                continue
            history = self.database.history_for(entry.path, root=self.root)
            if history is not None and history.coverage_watched(self.watched_percent):
                selected.append(entry)
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
            self.set_current_state(current.id, "stopped")
        self._replace_undo(self._snapshot(selected))
        self.database.remove_entries([entry.id for entry in selected])

    def clear_all(self, *, stop_confirmed: bool = False) -> None:
        selected = self.entries()
        if not selected:
            return
        current = self.current()
        if current is not None and current.state in {"playing", "paused"} and not stop_confirmed:
            raise ActivePlaybackError("stop VLC playback before clearing the active entry")
        if current is not None and current.state in {"playing", "paused"} and stop_confirmed:
            self.set_current_state(current.id, "stopped")
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

    def update_coverage(
        self,
        path: Path,
        ranges: Sequence[tuple[int, int]],
        duration_ms: int = 0,
    ) -> None:
        self.database.merge_coverage(path.resolve(), ranges, duration_ms)

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
