"""Explicit, backed-up recovery for pre-UUID history after device renumbering.

Old records contain no persistent volume identity. Matching path/inode/size/mtime
is strong evidence, not proof; this recovery is never applied automatically.
"""
from __future__ import annotations

import os
import sqlite3
import tempfile
from pathlib import Path
from typing import Any

from .database import Database
from .identity import file_identity
from .ipc import ControllerLock
from .paths import canonical_root, is_beneath


def _candidates(connection: sqlite3.Connection, root: Path) -> list[dict[str, Any]]:
    connection.row_factory = sqlite3.Row
    columns = {row[1] for row in connection.execute("PRAGMA table_info(media)")}
    if not {"id", "path", "device", "inode", "size", "mtime_ns"} <= columns:
        raise RuntimeError("unrecognized history database; no recovery attempted")
    result = []
    rows = connection.execute("SELECT * FROM media").fetchall()
    for row in rows:
        if "volume_uuid" in columns and row["volume_uuid"] is not None:
            continue
        path = Path(row["path"])
        try:
            if path.resolve(strict=True) != path or not is_beneath(path, root) or not path.is_file():
                continue
            current = file_identity(path)
        except (OSError, RuntimeError):
            continue
        if current.volume_uuid is None or current.device == int(row["device"]):
            continue
        if current[1:4] != (int(row["inode"]), int(row["size"]), int(row["mtime_ns"])):
            continue
        # Never choose between or combine duplicate histories, even if one has
        # no credit yet. A human must resolve these cases separately.
        matches = [
            other for other in rows
            if other["path"] == row["path"]
            and (int(other["inode"]), int(other["size"]), int(other["mtime_ns"])) == current[1:4]
        ]
        if len(matches) != 1:
            continue
        result.append({
            "mediaId": int(row["id"]), "path": str(path),
            "oldDevice": int(row["device"]), "device": current.device,
            "volumeUuid": current.volume_uuid,
        })
    return result


def recover_history(path: Path, root: Path, *, apply: bool = False) -> dict[str, Any]:
    """Preview without migration, or back up and explicitly pin eligible records.

    Queue identity, played ranges, positions, and timestamps are never rewritten.
    The caller must close the running application before applying.
    """
    path = path.expanduser().absolute()
    root = canonical_root(root)
    if path.is_symlink() or path.parent.is_symlink():
        raise RuntimeError("refusing a symlinked database location")
    if not apply:
        connection = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            return {"applied": False, "candidates": _candidates(connection, root)}
        finally:
            connection.close()
    with ControllerLock(path.with_suffix(".lock")):
        # Use SQLite backup, not a file copy: committed history may live in WAL.
        source = sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)
        try:
            candidates = _candidates(source, root)
            if not candidates:
                return {"applied": False, "candidates": []}
            fd, backup_name = tempfile.mkstemp(prefix=path.name + ".before-history-", dir=path.parent)
            os.close(fd)
            backup = sqlite3.connect(backup_name)
            try:
                source.backup(backup)
            finally:
                backup.close()
        finally:
            source.close()
        db = Database(path)
        try:
            db.connection.execute("BEGIN IMMEDIATE")
            try:
                # Revalidate after locking/migration; don't act on an old preview.
                current = _candidates(db.connection, root)
                if current != candidates:
                    raise RuntimeError("recovery candidates changed; preview again before applying")
                for candidate in current:
                    db.connection.execute(
                        "UPDATE media SET volume_uuid=? WHERE id=? AND volume_uuid IS NULL",
                        (candidate["volumeUuid"], candidate["mediaId"]),
                    )
                db.connection.execute("COMMIT")
            except BaseException:
                db.connection.execute("ROLLBACK")
                raise
        finally:
            db.close()
        return {"applied": True, "backup": backup_name, "candidates": candidates}
