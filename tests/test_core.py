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
    touch(tmp_path / ".hidden.mkv")
    touch(tmp_path / "notes.txt")
    (tmp_path / ".secret").mkdir()
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


def test_selected_entry_is_identity_validated_and_cleared_safely(tmp_path: Path) -> None:
    root = tmp_path / "show"
    other_root = tmp_path / "other"
    first = touch(root / "one.mkv")
    second = touch(root / "two.mkv")
    other = touch(other_root / "other.mkv")
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add([first, second])
    entries = queue.entries()

    db.set_selected(entries[1].id)
    assert db.get_selected_id() == entries[1].id
    queue.move(1, -1)
    assert db.get_selected_id() == entries[1].id

    queue.remove(0)
    assert db.get_selected_id() is None

    queue.add([first])
    other_queue = QueueService(db)
    other_queue.open(other_root)
    other_queue.add([other])
    other_entry = other_queue.entries()[0]
    queue.open(root)
    db.connection.execute(
        "INSERT INTO settings(key,value) VALUES('selected_entry',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(other_entry.id),),
    )
    queue.open(root)
    assert db.get_selected_id() is None

    queue.open(other_root)
    assert db.get_selected_id() is None
    db.close()


def test_open_repairs_stale_transient_rows_without_changing_queue_content(
    tmp_path: Path,
) -> None:
    root = tmp_path / "show"
    videos = [touch(root / name, name.encode()) for name in ("one.mkv", "two.mkv", "three.mkv")]
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    before = queue.entries()
    current = before[1]
    queue.update_progress(videos[0], 4_000, 10_000, completed=True)
    db.set_current(current.id)
    db.set_selected(before[2].id)
    for entry, state in zip(before, ("stopped", "stopped", "paused"), strict=True):
        db.set_state(entry.id, state)
    fingerprints = {entry.id: db.media_fingerprint(entry.media_id) for entry in before}
    content = {path: path.read_bytes() for path in videos}

    queue.open(root)

    after = queue.entries()
    assert [entry.id for entry in after] == [entry.id for entry in before]
    assert [entry.position for entry in after] == [entry.position for entry in before]
    assert [entry.state for entry in after] == ["queued", "stopped", "queued"]
    assert db.get_current_id() == current.id
    assert db.get_selected_id() == before[2].id
    assert {entry.id: db.media_fingerprint(entry.media_id) for entry in after} == fingerprints
    assert db.progress_for(videos[0])["completion_observed"] == 1
    assert {path: path.read_bytes() for path in videos} == content
    db.close()


def test_open_repairs_invalid_current_and_selected_identities(tmp_path: Path) -> None:
    root = tmp_path / "show"
    videos = [touch(root / name, name.encode()) for name in ("one.mkv", "two.mkv")]
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    entries = queue.entries()
    for entry in entries:
        db.set_state(entry.id, "stopped")
    db.connection.execute(
        "INSERT INTO settings(key,value) VALUES('current_entry','999999') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    db.connection.execute(
        "INSERT INTO settings(key,value) VALUES('selected_entry','999999') "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value"
    )
    content = {path: path.read_bytes() for path in videos}

    queue.open(root)

    assert db.get_current_id() is None
    assert db.get_selected_id() is None
    assert [entry.state for entry in queue.entries()] == ["queued", "queued"]
    assert {path: path.read_bytes() for path in videos} == content
    db.close()


def test_play_next_uses_front_when_saved_current_is_not_active(tmp_path: Path) -> None:
    root = tmp_path / "show"
    videos = [touch(root / name) for name in ("one.mkv", "two.mkv", "three.mkv")]
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    current = queue.play_now(1)
    current_path = current.path
    db.set_state(current.id, "stopped")

    queue.play_next([videos[2]])

    assert [entry.path.name for entry in queue.entries()] == [
        "three.mkv",
        "one.mkv",
        "two.mkv",
    ]
    assert queue.current() is not None and queue.current().path == current_path
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
