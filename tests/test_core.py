from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vlcq.database import Database
from vlcq.paths import (
    PathError,
    common_root,
    file_uri_to_path,
    list_folder,
    natural_key,
    validate_video,
)
from vlcq.queue import QueueService


def touch(path: Path, data: bytes = b"video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


def test_folder_browsing_is_non_recursive_and_natural(tmp_path: Path) -> None:
    touch(tmp_path / "episode10.mkv")
    touch(tmp_path / "episode2.mkv")
    touch(tmp_path / "season" / "episode1.mp4")
    entries = list_folder(tmp_path)
    assert [e.name for e in entries] == ["season", "episode2.mkv", "episode10.mkv"]
    assert all(e.path.parent == tmp_path for e in entries)
    assert natural_key("e9.mkv") < natural_key("e10.mkv")


def test_validation_root_confinement_uri_and_common_root(tmp_path: Path) -> None:
    root = tmp_path / "show"
    one = touch(root / "s1" / "one.mkv")
    two = touch(root / "s2" / "two.mp4")
    assert validate_video(one, root) == one.resolve()
    assert common_root([one, two]) == root.resolve()
    assert common_root([one]) == one.parent.resolve()
    assert file_uri_to_path(one.as_uri()) == one.resolve()
    for bad in (
        "https://example/x.mkv",
        "file://remote/x.mkv",
        one.as_uri() + "?x=1",
        "file:///tmp/bad%ZZ.mkv",
    ):
        with pytest.raises(PathError):
            file_uri_to_path(bad)
    with pytest.raises(PathError):
        validate_video(touch(tmp_path / "outside.mkv"), root)
    with pytest.raises(PathError):
        validate_video(touch(root / "note.txt"), root)


def test_database_queue_progress_and_export(tmp_path: Path) -> None:
    root = tmp_path / "show"
    first = touch(root / "e1.mkv", b"1")
    second = touch(root / "e2.mkv", b"2")
    db = Database(tmp_path / "state.sqlite3")
    queue = QueueService(db)
    assert db.path.stat().st_mode & 0o777 == 0o600
    for sidecar in (
        db.path.with_name(db.path.name + "-wal"),
        db.path.with_name(db.path.name + "-shm"),
    ):
        if sidecar.exists():
            assert sidecar.stat().st_mode & 0o777 == 0o600
    queue.open(root)
    assert queue.entries() == []
    queue.add([second, first, first])
    assert [e.path.name for e in queue.entries()] == ["e1.mkv", "e2.mkv"]
    queue.play_now(0)
    queue.update_progress(first, 10_000, 20_000)
    queue.update_progress(first, 0, 20_000)
    assert db.progress_for(first)["position_ms"] == 10_000
    queue.next()
    assert queue.entries()[0].state == "skipped"
    assert queue.current().path == second.resolve()
    outside = touch(tmp_path / "private" / "other.mkv")
    db.merge_progress(outside, 9_000, 10_000)
    exported = db.export_progress(root)
    assert list(exported) == ["e1.mkv"]
    assert json.dumps(exported)
    other_root = tmp_path / "other-show"
    other_root.mkdir()
    queue.open(other_root)
    assert queue.entries() == []
    queue.open(root)
    assert queue.entries() == []  # opening a root creates a new active queue
    db.close()


def test_newer_database_is_preserved_and_rejected(tmp_path: Path) -> None:
    path = tmp_path / "future.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA user_version=99")
    connection.close()
    with pytest.raises(RuntimeError, match="newer"):
        Database(path)
    connection = sqlite3.connect(path)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 99
    connection.close()


def test_replacement_does_not_inherit_progress(tmp_path: Path) -> None:
    root = tmp_path / "show"
    video = touch(root / "e1.mkv", b"old")
    db = Database(tmp_path / "db.sqlite3")
    q = QueueService(db)
    q.open(root)
    q.add([video])
    q.update_progress(video, 5_000, 10_000)
    video.write_bytes(b"replacement-longer")
    q.add([video])
    assert db.progress_for(video)["position_ms"] == 0
