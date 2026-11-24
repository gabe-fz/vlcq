"""History follows a file written in place (downloads), not replacements."""
from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from vlcq.database import Database
from vlcq.identity import file_identity
from vlcq.queue import QueueService


def _grow(path: Path, data: bytes, *, bump_ns: int = 1_000_000_000) -> None:
    before = path.stat().st_mtime_ns
    with path.open("ab") as handle:
        handle.write(data)
    os.utime(path, ns=(before + bump_ns, before + bump_ns))


def test_progress_and_coverage_follow_a_growing_file(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "e1.mkv"
    video.write_bytes(b"partial")
    db = Database(tmp_path / "db.sqlite3")
    db.merge_progress(video, 60_000, 1_400_000)
    db.merge_coverage(video, [(0, 60_000)], 1_400_000)
    _grow(video, b"more data")
    db.merge_coverage(video, [(60_000, 1_400_000)], 1_400_000)
    db.merge_progress(video, 1_400_000, 1_400_000, completed=True)
    assert db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    history = db.history_for(video, root)
    assert history is not None
    assert db.coverage_ranges_for_media(1) == ((0, 1_400_000),)
    assert db.media_fingerprint(1).matches(file_identity(video.resolve()))
    db.close()


def test_preallocated_file_filled_in_place_keeps_one_history(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "e1.mkv"
    video.write_bytes(b"\0" * 64)
    db = Database(tmp_path / "db.sqlite3")
    db.merge_progress(video, 5_000, 100_000)
    with video.open("r+b") as handle:
        handle.write(b"piece")
    stat = video.stat()
    os.utime(video, ns=(stat.st_mtime_ns + 10**9, stat.st_mtime_ns + 10**9))
    db.merge_progress(video, 9_000, 100_000)
    rows = db.connection.execute("SELECT id,position_ms FROM media").fetchall()
    assert [(row[0], row[1]) for row in rows] == [(1, 9_000)]
    db.close()


def test_shrunk_or_older_file_still_starts_fresh(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "e1.mkv"
    video.write_bytes(b"a long original file")
    db = Database(tmp_path / "db.sqlite3")
    db.merge_progress(video, 5_000, 100_000)
    stat = video.stat()
    with video.open("r+b") as handle:
        handle.truncate(4)
    os.utime(video, ns=(stat.st_mtime_ns + 10**9, stat.st_mtime_ns + 10**9))
    assert db.ensure_media(video) != 1
    assert db.progress_for(video)["position_ms"] == 0
    db.close()


def test_open_consolidates_history_split_by_older_versions(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "e1.mkv"
    other = root / "e2.mkv"
    video.write_bytes(b"partial")
    other.write_bytes(b"other")
    path = tmp_path / "db.sqlite3"
    db = Database(path)
    queue = QueueService(db)
    queue.open(root)
    queue.add([video, other])
    db.merge_progress(video, 60_000, 1_400_000)
    db.merge_coverage(video, [(0, 60_000)], 1_400_000)
    partial = file_identity(video.resolve())
    _grow(video, b"rest")
    db.close()
    # Simulate an older vlcq that created a second row for the grown file.
    raw = sqlite3.connect(path)
    raw.execute(
        "INSERT INTO media(path,device,inode,size,mtime_ns,volume_uuid,position_ms,duration_ms,"
        "completion_observed,first_observed,last_observed,resume_position_ms,last_played_at) "
        "SELECT path,device,inode,size+4,mtime_ns+1000000000,volume_uuid,1400000,1400000,1,"
        "'2000-01-01','2999-01-01',0,'2999-01-01' FROM media WHERE id=1"
    )
    raw.execute("INSERT INTO coverage_ranges VALUES(3,600000,1400000)")
    raw.commit()
    raw.close()
    assert partial.size + 4 == video.stat().st_size

    db = Database(path)
    ids = [row[0] for row in db.connection.execute("SELECT id FROM media WHERE path=?", (str(video.resolve()),))]
    assert ids == [3]
    row = db.connection.execute("SELECT * FROM media WHERE id=3").fetchone()
    assert row["position_ms"] == 1_400_000
    assert row["completion_observed"] == 1
    assert row["resume_position_ms"] == 0
    assert row["first_observed"] == "2000-01-01"
    assert db.coverage_ranges_for_media(3) == ((0, 60_000), (600_000, 1_400_000))
    entries = db.connection.execute("SELECT media_id FROM queue_entries ORDER BY position").fetchall()
    assert [entry[0] for entry in entries] == [3, 2]
    assert db.connection.execute("PRAGMA foreign_key_check").fetchone() is None
    db.close()
