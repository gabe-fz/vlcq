from __future__ import annotations

import os
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .models import QueueEntry
from .paths import canonical_root, is_beneath

SCHEMA_VERSION = 1


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

    def _migrate(self) -> None:
        version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
        if version > SCHEMA_VERSION:
            raise RuntimeError("database was created by a newer vlcq")
        if version == 0:
            self.connection.executescript("""
                BEGIN IMMEDIATE;
                CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE queues(
                    id INTEGER PRIMARY KEY, root TEXT NOT NULL,
                    created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE media(
                    id INTEGER PRIMARY KEY, path TEXT NOT NULL,
                    device INTEGER NOT NULL, inode INTEGER NOT NULL,
                    size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
                    position_ms INTEGER NOT NULL DEFAULT 0,
                    duration_ms INTEGER NOT NULL DEFAULT 0,
                    completion_observed INTEGER NOT NULL DEFAULT 0,
                    first_observed TEXT NOT NULL, last_observed TEXT NOT NULL,
                    UNIQUE(path, device, inode, size, mtime_ns));
                CREATE TABLE queue_entries(
                    id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL REFERENCES queues(id),
                    position INTEGER NOT NULL,
                    media_id INTEGER NOT NULL REFERENCES media(id),
                    state TEXT NOT NULL DEFAULT 'queued',
                    UNIQUE(queue_id, position));
                PRAGMA user_version=1;
                COMMIT;
            """)

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
        return value

    def get_root(self) -> Path | None:
        row = self.connection.execute(
            "SELECT q.root FROM queues q JOIN settings s ON s.key='active_queue' "
            "AND q.id=CAST(s.value AS INTEGER)"
        ).fetchone()
        return Path(row[0]) if row else None

    def set_current(self, entry_id: int | None) -> None:
        if entry_id is None:
            self.connection.execute("DELETE FROM settings WHERE key='current_entry'")
        else:
            self.connection.execute(
                "INSERT INTO settings(key,value) VALUES('current_entry',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (str(entry_id),),
            )

    def get_current_id(self) -> int | None:
        row = self.connection.execute(
            "SELECT value FROM settings WHERE key='current_entry'"
        ).fetchone()
        return int(row[0]) if row else None

    def ensure_media(self, path: Path) -> int:
        stat = path.stat()
        now = datetime.now(UTC).isoformat()
        values = (str(path), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
        row = self.connection.execute(
            "SELECT id FROM media WHERE path=? AND device=? AND inode=? AND size=? AND mtime_ns=?",
            values,
        ).fetchone()
        if row:
            return int(row[0])
        cursor = self.connection.execute(
            "INSERT INTO media(path,device,inode,size,mtime_ns,first_observed,last_observed) VALUES(?,?,?,?,?,?,?)",
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
            (queue_id, str(path)),
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
        self.connection.execute("UPDATE queue_entries SET state=? WHERE id=?", (state, entry_id))

    def reorder(self, ordered_ids: list[int]) -> None:
        with self.connection:
            for offset, entry_id in enumerate(ordered_ids):
                self.connection.execute(
                    "UPDATE queue_entries SET position=? WHERE id=?", (-offset - 1, entry_id)
                )
            for offset, entry_id in enumerate(ordered_ids):
                self.connection.execute(
                    "UPDATE queue_entries SET position=? WHERE id=?", (offset, entry_id)
                )

    def remove(self, entry_id: int) -> None:
        self.connection.execute("DELETE FROM queue_entries WHERE id=?", (entry_id,))
        self.reorder([e.id for e in self.queue_entries()])

    def merge_progress(
        self, path: Path, position_ms: int, duration_ms: int, completed: bool = False
    ) -> None:
        media_id = self.ensure_media(path)
        now = datetime.now(UTC).isoformat()
        self.connection.execute(
            "UPDATE media SET position_ms=MAX(position_ms,?), duration_ms=MAX(duration_ms,?), completion_observed=MAX(completion_observed,?), last_observed=? WHERE id=?",
            (max(0, position_ms), max(0, duration_ms), int(completed), now, media_id),
        )

    def progress_for(self, path: Path) -> dict[str, Any]:
        media_id = self.ensure_media(path.resolve())
        row = self.connection.execute(
            "SELECT position_ms,duration_ms,completion_observed,last_observed FROM media WHERE id=?",
            (media_id,),
        ).fetchone()
        return dict(row)

    def export_progress(self, root: str | Path) -> dict[str, dict[str, Any]]:
        base = canonical_root(root)
        records: dict[str, dict[str, Any]] = {}
        for row in self.connection.execute(
            "SELECT path,position_ms,duration_ms,completion_observed,last_observed FROM media WHERE position_ms>0 OR completion_observed=1"
        ):
            path = Path(row["path"])
            if not is_beneath(path, base):
                continue
            relative = path.relative_to(base).as_posix()
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
