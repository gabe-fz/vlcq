from __future__ import annotations

import os
import sqlite3
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import HistoryProjection, QueueEntry
from .paths import canonical_root, is_beneath

SCHEMA_VERSION = 2
_VALID_STATES = frozenset(
    {"queued", "playing", "paused", "stopped", "skipped", "completed", "missing", "failed"}
)


class Database:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise RuntimeError("refusing a symlinked database location")
        if not parent_existed:
            os.chmod(self.path.parent, 0o700)
        self.connection = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA busy_timeout=5000")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self._migrate()
        self._secure_files()

    def _secure_files(self) -> None:
        for candidate in (
            self.path,
            self.path.with_name(self.path.name + "-wal"),
            self.path.with_name(self.path.name + "-shm"),
        ):
            if candidate.exists() and not candidate.is_symlink():
                os.chmod(candidate, 0o600)

    def _transactional_schema_change(self, statements: Iterable[str], version: int) -> None:
        """Apply DDL without replacing or partially upgrading the database.

        SQLite DDL participates in an explicit transaction.  In particular,
        do not use ``executescript`` here: it may commit an outer transaction
        before running the script, which would make an injected migration
        failure impossible to roll back as one unit.
        """
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            for statement in statements:
                self.connection.execute(statement)
            self.connection.execute(f"PRAGMA user_version={version}")
            self.connection.execute("COMMIT")
        except BaseException:
            try:
                self.connection.execute("ROLLBACK")
            except sqlite3.DatabaseError:
                pass
            raise

    def _create_schema(self) -> None:
        self._transactional_schema_change(
            (
                "CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL)",
                (
                    "CREATE TABLE queues("
                    "id INTEGER PRIMARY KEY, root TEXT NOT NULL, "
                    "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
                ),
                (
                    "CREATE TABLE media("
                    "id INTEGER PRIMARY KEY, path TEXT NOT NULL, "
                    "device INTEGER NOT NULL, inode INTEGER NOT NULL, size INTEGER NOT NULL, "
                    "mtime_ns INTEGER NOT NULL, position_ms INTEGER NOT NULL DEFAULT 0, "
                    "duration_ms INTEGER NOT NULL DEFAULT 0, "
                    "completion_observed INTEGER NOT NULL DEFAULT 0, "
                    "first_observed TEXT NOT NULL, last_observed TEXT NOT NULL, "
                    "resume_position_ms INTEGER, last_played_at TEXT, "
                    "UNIQUE(path, device, inode, size, mtime_ns))"
                ),
                (
                    "CREATE TABLE queue_entries("
                    "id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL REFERENCES queues(id), "
                    "position INTEGER NOT NULL, media_id INTEGER NOT NULL REFERENCES media(id), "
                    "state TEXT NOT NULL DEFAULT 'queued', UNIQUE(queue_id, position))"
                ),
            ),
            SCHEMA_VERSION,
        )

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError("database was created by a newer vlcq")
        if version == 0:
            self._create_schema()
        elif version == 1:
            self._transactional_schema_change(
                (
                    "ALTER TABLE media ADD COLUMN resume_position_ms INTEGER",
                    "ALTER TABLE media ADD COLUMN last_played_at TEXT",
                ),
                SCHEMA_VERSION,
            )

    def close(self) -> None:
        self._secure_files()
        self.connection.close()
        self._secure_files()

    def _active_queue_id(self) -> int:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='active_queue'"
        ).fetchone()
        if not row:
            raise RuntimeError("open a library root first")
        return int(row[0])

    def active_queue_id(self) -> int:
        return self._active_queue_id()

    def set_root(self, root: str | Path) -> Path:
        value = canonical_root(root)
        current = self.get_root()
        if current == value:
            return value
        now = datetime.now(UTC).isoformat()
        cursor = self.connection.execute(
            "INSERT INTO queues(root,created_at,updated_at) VALUES(?,?,?)",
            (str(value), now, now),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("failed to create queue")
        self.connection.execute(
            "INSERT INTO settings(key,value) VALUES('active_queue',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(cursor.lastrowid),),
        )
        self.set_current(None)
        self.set_selected(None)
        return value

    def get_root(self) -> Path | None:
        row = self.connection.execute(
            "SELECT q.root FROM queues q JOIN settings s ON s.key='active_queue' "
            "AND q.id=CAST(s.value AS INTEGER)"
        ).fetchone()
        return Path(row[0]) if row else None

    def _entry_belongs_to_active_queue(self, entry_id: int) -> bool:
        try:
            queue_id = self._active_queue_id()
        except RuntimeError:
            return False
        row = self.connection.execute(
            "SELECT 1 FROM queue_entries WHERE id=? AND queue_id=?",
            (entry_id, queue_id),
        ).fetchone()
        return row is not None

    def set_current(self, entry_id: int | None) -> None:
        """Set the legacy pointer directly; open/transition paths validate it."""
        if entry_id is None:
            self.connection.execute("DELETE FROM settings WHERE key='current_entry'")
        else:
            self.connection.execute(
                "INSERT INTO settings(key,value) VALUES('current_entry',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(entry_id),),
            )

    def get_current_id(self) -> int | None:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='current_entry'"
        ).fetchone()
        if row is None:
            return None
        try:
            entry_id = int(row[0])
        except (TypeError, ValueError):
            entry_id = None
        if entry_id is None or not self._entry_belongs_to_active_queue(entry_id):
            self.connection.execute("DELETE FROM settings WHERE key='current_entry'")
            return None
        return entry_id

    def set_selected(self, entry_id: int | None) -> None:
        """Persist the highlighted entry only when it belongs to the active queue."""
        if entry_id is None:
            self.connection.execute("DELETE FROM settings WHERE key='selected_entry'")
        else:
            if not self._entry_belongs_to_active_queue(entry_id):
                raise ValueError("selected entry does not belong to the active queue")
            self.connection.execute(
                "INSERT INTO settings(key,value) VALUES('selected_entry',?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(entry_id),),
            )

    # Explicit aliases keep the identity semantics clear at call sites.
    set_selected_id = set_selected
    set_selected_entry = set_selected

    def get_selected_id(self) -> int | None:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='selected_entry'"
        ).fetchone()
        if row is None:
            return None
        try:
            entry_id = int(row[0])
        except (TypeError, ValueError):
            entry_id = None
        if entry_id is None or not self._entry_belongs_to_active_queue(entry_id):
            self.connection.execute("DELETE FROM settings WHERE key='selected_entry'")
            return None
        return entry_id

    get_selected = get_selected_id
    get_selected_entry_id = get_selected_id

    def transition_current(self, entry_id: int | None, state: str = "playing") -> None:
        """Atomically move current playback and normalize stale transient rows."""
        if state not in _VALID_STATES:
            raise ValueError(f"unknown queue state: {state}")
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            queue_id = self._active_queue_id()
            if entry_id is not None:
                row = self.connection.execute(
                    "SELECT 1 FROM queue_entries WHERE id=? AND queue_id=?",
                    (entry_id, queue_id),
                ).fetchone()
                if row is None:
                    raise ValueError("current entry does not belong to the active queue")
                self.connection.execute(
                    "UPDATE queue_entries SET state='queued' "
                    "WHERE queue_id=? AND id<>? AND state IN ('playing','paused','stopped')",
                    (queue_id, entry_id),
                )
                self.connection.execute(
                    "UPDATE queue_entries SET state=? WHERE id=? AND queue_id=?",
                    (state, entry_id, queue_id),
                )
                self.connection.execute(
                    "INSERT INTO settings(key,value) VALUES('current_entry',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (str(entry_id),),
                )
            else:
                self.connection.execute(
                    "UPDATE queue_entries SET state='queued' "
                    "WHERE queue_id=? AND state IN ('playing','paused','stopped')",
                    (queue_id,),
                )
                self.connection.execute("DELETE FROM settings WHERE key='current_entry'")
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def normalize_active_queue(self) -> None:
        """Repair stale identities and transient rows without changing content."""
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            queue_id = self._active_queue_id()
            current_row = self.connection.execute(
                "SELECT value FROM settings WHERE key='current_entry'"
            ).fetchone()
            current_id: int | None = None
            if current_row is not None:
                try:
                    candidate = int(current_row[0])
                except (TypeError, ValueError):
                    candidate = None
                if candidate is not None:
                    valid = self.connection.execute(
                        "SELECT 1 FROM queue_entries WHERE id=? AND queue_id=?",
                        (candidate, queue_id),
                    ).fetchone()
                    if valid is not None:
                        current_id = candidate
                if current_id is None:
                    self.connection.execute("DELETE FROM settings WHERE key='current_entry'")

            selected_row = self.connection.execute(
                "SELECT value FROM settings WHERE key='selected_entry'"
            ).fetchone()
            if selected_row is not None:
                try:
                    selected_id = int(selected_row[0])
                except (TypeError, ValueError):
                    selected_id = None
                valid = (
                    selected_id is not None
                    and self.connection.execute(
                        "SELECT 1 FROM queue_entries WHERE id=? AND queue_id=?",
                        (selected_id, queue_id),
                    ).fetchone()
                    is not None
                )
                if not valid:
                    self.connection.execute("DELETE FROM settings WHERE key='selected_entry'")

            if current_id is None:
                self.connection.execute(
                    "UPDATE queue_entries SET state='queued' "
                    "WHERE queue_id=? AND state IN ('playing','paused','stopped')",
                    (queue_id,),
                )
            else:
                self.connection.execute(
                    "UPDATE queue_entries SET state='queued' "
                    "WHERE queue_id=? AND id<>? AND state IN ('playing','paused','stopped')",
                    (queue_id, current_id),
                )
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    @staticmethod
    def _canonical_file(path: Path) -> tuple[Path, os.stat_result]:
        canonical = path.expanduser().resolve(strict=True)
        return canonical, canonical.stat()

    def ensure_media(self, path: Path) -> int:
        canonical, stat = self._canonical_file(path)
        now = datetime.now(UTC).isoformat()
        values = (str(canonical), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        row = self.connection.execute(
            "SELECT id FROM media WHERE path=? AND device=? AND inode=? AND size=? AND mtime_ns=?",
            values,
        ).fetchone()
        if row:
            return int(row[0])
        cursor = self.connection.execute(
            "INSERT INTO media(path,device,inode,size,mtime_ns,first_observed,last_observed) "
            "VALUES(?,?,?,?,?,?,?)",
            (*values, now, now),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("failed to create media record")
        return int(cursor.lastrowid)

    def add_entry(self, path: Path) -> int:
        media_id = self.ensure_media(path)
        queue_id = self._active_queue_id()
        row = self.connection.execute(
            "SELECT q.id,q.media_id FROM queue_entries q JOIN media m ON m.id=q.media_id "
            "WHERE q.queue_id=? AND m.path=?",
            (queue_id, str(path.resolve())),
        ).fetchone()
        if row:
            if int(row["media_id"]) != media_id:
                self.connection.execute(
                    "UPDATE queue_entries SET media_id=?,state='queued' WHERE id=?",
                    (media_id, row["id"]),
                )
            return int(row["id"])
        position = int(
            self.connection.execute(
                "SELECT COALESCE(MAX(position),-1)+1 FROM queue_entries WHERE queue_id=?",
                (queue_id,),
            ).fetchone()[0]
        )
        cursor = self.connection.execute(
            "INSERT INTO queue_entries(queue_id,position,media_id,state) VALUES(?,?,?,'queued')",
            (queue_id, position, media_id),
        )
        if cursor.lastrowid is None:
            raise RuntimeError("failed to create queue entry")
        return int(cursor.lastrowid)

    def queue_entries(self) -> list[QueueEntry]:
        try:
            queue_id = self._active_queue_id()
        except RuntimeError:
            return []
        rows = self.connection.execute(
            "SELECT q.id,q.position,q.media_id,m.path,q.state FROM queue_entries q "
            "JOIN media m ON m.id=q.media_id WHERE q.queue_id=? ORDER BY q.position",
            (queue_id,),
        ).fetchall()
        return [
            QueueEntry(
                int(r["id"]),
                int(r["position"]),
                int(r["media_id"]),
                Path(r["path"]),
                str(r["state"]),
            )
            for r in rows
        ]

    def set_state(self, entry_id: int, state: str) -> None:
        """Set a row directly for compatibility repair and terminal observations."""
        if state not in _VALID_STATES:
            raise ValueError(f"unknown queue state: {state}")
        self.connection.execute("UPDATE queue_entries SET state=? WHERE id=?", (state, entry_id))

    def _reorder_in_transaction(self, ordered_ids: list[int]) -> None:
        for offset, entry_id in enumerate(ordered_ids):
            self.connection.execute(
                "UPDATE queue_entries SET position=? WHERE id=?", (-offset - 1, entry_id)
            )
        for offset, entry_id in enumerate(ordered_ids):
            self.connection.execute(
                "UPDATE queue_entries SET position=? WHERE id=?", (offset, entry_id)
            )

    def reorder(self, ordered_ids: list[int]) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            self._reorder_in_transaction(ordered_ids)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def append_entries(self, paths: list[Path]) -> None:
        """Atomically append canonical, already-validated paths.

        Existing canonical paths are left in place, which makes repeated Add
        actions idempotent and preserves the active row and unselected order.
        """
        queue_id = self._active_queue_id()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing = {
                str(row["path"]): row
                for row in self.connection.execute(
                    "SELECT q.id,q.media_id,q.state,m.path FROM queue_entries q "
                    "JOIN media m ON m.id=q.media_id WHERE q.queue_id=?",
                    (queue_id,),
                )
            }
            position = int(
                self.connection.execute(
                    "SELECT COALESCE(MAX(position),-1)+1 FROM queue_entries WHERE queue_id=?",
                    (queue_id,),
                ).fetchone()[0]
            )
            for path in paths:
                media_id = self.ensure_media(path)
                key = str(path.resolve())
                row = existing.get(key)
                if row is not None:
                    if int(row["media_id"]) != media_id and row["state"] not in {"playing", "paused"}:
                        self.connection.execute(
                            "UPDATE queue_entries SET media_id=?,state='queued' WHERE id=?",
                            (media_id, row["id"]),
                        )
                    continue
                cursor = self.connection.execute(
                    "INSERT INTO queue_entries(queue_id,position,media_id,state) VALUES(?,?,?,'queued')",
                    (queue_id, position, media_id),
                )
                if cursor.lastrowid is None:
                    raise RuntimeError("failed to create queue entry")
                position += 1
                # Keep duplicate operands idempotent within the same batch.
                existing[key] = {"id": cursor.lastrowid, "media_id": media_id, "state": "queued"}
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def play_next_entries(self, paths: list[Path]) -> None:
        """Atomically move selected identities into the Play next block."""
        queue_id = self._active_queue_id()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            rows = self.connection.execute(
                "SELECT q.id,q.position,q.media_id,m.path,q.state FROM queue_entries q "
                "JOIN media m ON m.id=q.media_id WHERE q.queue_id=? ORDER BY q.position",
                (queue_id,),
            ).fetchall()
            by_path = {str(row["path"]): row for row in rows}
            current_id = self.get_current_id()
            active_id = next(
                (
                    int(row["id"])
                    for row in rows
                    if int(row["id"]) == current_id
                    and str(row["state"]) in {"playing", "paused"}
                ),
                None,
            )
            selected_ids: list[int] = []
            for path in paths:
                media_id = self.ensure_media(path)
                key = str(path.resolve())
                row = by_path.get(key)
                if row is not None:
                    if int(row["id"]) == active_id:
                        continue
                    selected_ids.append(int(row["id"]))
                    if int(row["media_id"]) != media_id and row["state"] not in {"playing", "paused"}:
                        self.connection.execute(
                            "UPDATE queue_entries SET media_id=?,state='queued' WHERE id=?",
                            (media_id, row["id"]),
                        )
                else:
                    cursor = self.connection.execute(
                        "INSERT INTO queue_entries(queue_id,position,media_id,state) VALUES(?,?,?,'queued')",
                        (queue_id, len(rows) + len(selected_ids), media_id),
                    )
                    if cursor.lastrowid is None:
                        raise RuntimeError("failed to create queue entry")
                    selected_ids.append(int(cursor.lastrowid))
            # Paths are naturally ordered by QueueService.  Keep all other
            # entries relative to one another and do not move the active item.
            selected_set = set(selected_ids)
            remaining = [int(row["id"]) for row in rows if int(row["id"]) not in selected_set]
            if active_id is not None and active_id in remaining:
                insertion = remaining.index(active_id) + 1
                ordered = remaining[:insertion] + selected_ids + remaining[insertion:]
            else:
                ordered = selected_ids + remaining
            self._reorder_in_transaction(ordered)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def remove(self, entry_id: int) -> None:
        self.remove_entries([entry_id])

    def remove_entries(self, entry_ids: list[int]) -> None:
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            queue_id = self._active_queue_id()
            placeholders = ",".join("?" for _ in entry_ids)
            if entry_ids:
                parameters: tuple[object, ...] = (queue_id, *entry_ids)
                self.connection.execute(
                    f"DELETE FROM queue_entries WHERE queue_id=? AND id IN ({placeholders})",
                    parameters,
                )
                self.connection.execute(
                    f"DELETE FROM settings WHERE key IN ('current_entry','selected_entry') "
                    f"AND CAST(value AS INTEGER) IN ({placeholders})",
                    entry_ids,
                )
            self._reorder_in_transaction([e.id for e in self.queue_entries()])
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def media_fingerprint(self, media_id: int) -> tuple[int, int, int, int]:
        row = self.connection.execute(
            "SELECT device,inode,size,mtime_ns FROM media WHERE id=?", (media_id,)
        ).fetchone()
        if row is None:
            raise RuntimeError("media record not found")
        return (int(row["device"]), int(row["inode"]), int(row["size"]), int(row["mtime_ns"]))

    def restore_entries(self, rows: list[dict[str, int | str]], ordered_ids: list[int]) -> None:
        """Restore a previously removed set in one transaction."""
        queue_id = self._active_queue_id()
        self.connection.execute("BEGIN IMMEDIATE")
        try:
            existing_ids = {
                int(row["id"])
                for row in self.connection.execute(
                    "SELECT id FROM queue_entries WHERE queue_id=?", (queue_id,)
                )
            }
            if existing_ids.intersection(int(row["id"]) for row in rows):
                raise RuntimeError("undo snapshot conflicts with the current queue")
            for row in rows:
                if int(row["queue_id"]) != queue_id:
                    raise RuntimeError("undo snapshot belongs to another library")
                self.connection.execute(
                    "INSERT INTO queue_entries(id,queue_id,position,media_id,state) "
                    "VALUES(?,?,?,?,?)",
                    (
                        int(row["id"]),
                        queue_id,
                        -1000000 - len(ordered_ids) - int(row["id"]),
                        int(row["media_id"]),
                        str(row["state"]),
                    ),
                )
            all_ids = {
                int(row["id"])
                for row in self.connection.execute(
                    "SELECT id FROM queue_entries WHERE queue_id=?", (queue_id,)
                )
            }
            if set(ordered_ids) != all_ids:
                raise RuntimeError("undo snapshot does not describe the current queue")
            self._reorder_in_transaction(ordered_ids)
            self.connection.execute("COMMIT")
        except BaseException:
            self.connection.execute("ROLLBACK")
            raise

    def merge_progress(
        self,
        path: Path,
        position_ms: int,
        duration_ms: int,
        completed: bool = False,
        *,
        resume_position_ms: int | None = None,
        trustworthy: bool = True,
        allow_resume_reset: bool = False,
        played_at: str | None = None,
    ) -> None:
        """Merge an observation without conflating resume and furthest history.

        The default remains compatible with the v1 API: the supplied position
        increases the historical maximum.  A zero observation never erases a
        known resume point unless the caller explicitly authorizes a reset.
        """
        media_id = self.ensure_media(path)
        now = played_at or datetime.now(UTC).isoformat()
        position = max(0, int(position_ms))
        duration = max(0, int(duration_ms))
        candidate = position if resume_position_ms is None else max(0, int(resume_position_ms))
        row = self.connection.execute(
            "SELECT resume_position_ms FROM media WHERE id=?", (media_id,)
        ).fetchone()
        existing_resume = row[0] if row is not None else None
        resume_update: int | None = None
        update_resume = False
        if trustworthy and (allow_resume_reset or candidate > 0 or existing_resume is None):
            resume_update = candidate
            update_resume = True
        if update_resume:
            self.connection.execute(
                "UPDATE media SET position_ms=MAX(position_ms,?), duration_ms=MAX(duration_ms,?), "
                "completion_observed=MAX(completion_observed,?), last_observed=?, "
                "resume_position_ms=?, last_played_at=? WHERE id=?",
                (position, duration, int(completed), now, resume_update, now, media_id),
            )
        elif trustworthy:
            self.connection.execute(
                "UPDATE media SET position_ms=MAX(position_ms,?), duration_ms=MAX(duration_ms,?), "
                "completion_observed=MAX(completion_observed,?), last_observed=?, "
                "last_played_at=? WHERE id=?",
                (position, duration, int(completed), now, now, media_id),
            )
        else:
            self.connection.execute(
                "UPDATE media SET position_ms=MAX(position_ms,?), duration_ms=MAX(duration_ms,?), "
                "completion_observed=MAX(completion_observed,?), last_observed=? WHERE id=?",
                (position, duration, int(completed), now, media_id),
            )

    def set_resume_position(
        self,
        path: Path,
        position_ms: int,
        duration_ms: int = 0,
        *,
        completed: bool = False,
        played_at: str | None = None,
    ) -> None:
        """Record a confirmed resume point, including an intentional zero."""
        self.merge_progress(
            path,
            position_ms,
            duration_ms,
            completed,
            resume_position_ms=position_ms,
            allow_resume_reset=True,
            played_at=played_at,
        )

    def progress_for(self, path: Path) -> dict[str, Any]:
        media_id = self.ensure_media(path.resolve())
        row = self.connection.execute(
            "SELECT position_ms,duration_ms,completion_observed,last_observed,"
            "resume_position_ms,last_played_at,first_observed FROM media WHERE id=?",
            (media_id,),
        ).fetchone()
        if row is None:
            raise RuntimeError("media record disappeared")
        return dict(row)

    @staticmethod
    def _projection(row: sqlite3.Row, path: Path) -> HistoryProjection:
        return HistoryProjection(
            path=path,
            media_id=int(row["id"]),
            position_ms=int(row["position_ms"]),
            resume_position_ms=(
                None if row["resume_position_ms"] is None else int(row["resume_position_ms"])
            ),
            duration_ms=int(row["duration_ms"]),
            completion_observed=bool(row["completion_observed"]),
            first_observed=str(row["first_observed"]) if row["first_observed"] else None,
            last_observed=str(row["last_observed"]) if row["last_observed"] else None,
            last_played_at=str(row["last_played_at"]) if row["last_played_at"] else None,
        )

    def history_for(self, path: Path, root: str | Path | None = None) -> HistoryProjection | None:
        """Return history only for the file's current fingerprint.

        This method intentionally never calls ``ensure_media``.  Browsing a
        never-played file therefore cannot create a media row or timestamp.
        """
        projections = self.history_for_paths([path], root=root)
        return projections.get(path.expanduser().resolve(strict=False))

    def history_for_identities(
        self,
        identities: Iterable[tuple[Path, tuple[int, int, int, int]]],
    ) -> dict[Path, HistoryProjection]:
        """Read history for already-validated identities without filesystem I/O.

        Callers that validate files asynchronously can hand the owning SQLite
        thread only canonical paths and fingerprints.  Keeping the SELECT
        separate from validation prevents rendering from doing blocking stat
        calls while also preserving the connection's thread ownership.
        """
        result: dict[Path, HistoryProjection] = {}
        columns = (
            "id,path,position_ms,duration_ms,completion_observed,first_observed,"
            "last_observed,resume_position_ms,last_played_at"
        )
        for path, fingerprint in identities:
            row = self.connection.execute(
                f"SELECT {columns} FROM media WHERE path=? AND device=? AND inode=? "
                "AND size=? AND mtime_ns=?",
                (str(path), *fingerprint),
            ).fetchone()
            if row is not None:
                result[path] = self._projection(row, path)
        return result

    def history_for_paths(
        self, paths: Iterable[Path], *, root: str | Path | None = None
    ) -> dict[Path, HistoryProjection]:
        """Bulk, read-only history lookup keyed by canonical current paths."""
        base = canonical_root(root) if root is not None else None
        identities: list[tuple[Path, tuple[int, int, int, int]]] = []
        for raw_path in paths:
            path = Path(raw_path).expanduser().resolve(strict=False)
            if base is not None and not is_beneath(path, base):
                continue
            try:
                stat = path.stat()
            except (OSError, RuntimeError):
                continue
            if not path.is_file():
                continue
            identities.append(
                (path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns))
            )
        return self.history_for_identities(identities)

    # Descriptive alias used by rendering code and external integrations.
    def history_projection(
        self, paths: Iterable[Path], *, root: str | Path | None = None
    ) -> dict[Path, HistoryProjection]:
        return self.history_for_paths(paths, root=root)

    def export_progress(self, root: str | Path) -> dict[str, dict[str, Any]]:
        base = canonical_root(root)
        records: dict[str, dict[str, Any]] = {}
        for row in self.connection.execute(
            "SELECT path,position_ms,duration_ms,completion_observed,last_observed FROM media "
            "WHERE position_ms>0 OR completion_observed=1"
        ):
            path = Path(row["path"])
            try:
                canonical = path.resolve(strict=False)
            except RuntimeError:
                continue
            if not is_beneath(canonical, base):
                continue
            relative = canonical.relative_to(base).as_posix()
            duration = int(row["duration_ms"])
            position = int(row["position_ms"])
            records[relative] = {
                "positionMs": position,
                "durationMs": duration,
                "watchedPercent": min(100, round(position * 100 / duration)) if duration else 0,
                "completionObserved": bool(row["completion_observed"]),
                "observedAt": row["last_observed"],
            }
        return dict(sorted(records.items()))
