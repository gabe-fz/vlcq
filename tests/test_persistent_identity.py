from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path

import pytest

from vlcq import identity
from vlcq.cli import main
from vlcq.database import Database, DatabaseMigrationError
from vlcq.identity import FileIdentity, file_identity
from vlcq.ipc import ControllerBusy, ControllerLock
from vlcq.queue import QueueService
from vlcq.recovery import recover_history
from vlcq.tui import _file_identity


@pytest.fixture
def library(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    monkeypatch.setattr(identity, "volume_uuid", lambda path: "volume-a")
    root = (tmp_path / "library").resolve()
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"original video")
    return root, video, tmp_path / "history.sqlite3"


def seed(root: Path, video: Path, path: Path) -> tuple[int, int]:
    db = Database(path)
    db.set_root(root)
    entry = db.add_entry(video)
    db.set_current(entry)
    db.set_selected(entry)
    db.merge_progress(video, 90_000, 100_000)
    db.merge_coverage(video, [(0, 30_000), (40_000, 90_000)], 100_000)
    media_id = db.queue_entries()[0].media_id
    db.close()
    return entry, media_id


def legacy(path: Path, *, renumber: bool = True) -> None:
    with sqlite3.connect(path) as connection:
        connection.execute("ALTER TABLE media DROP COLUMN volume_uuid")
        connection.execute("PRAGMA user_version=4")
        if renumber:
            connection.execute("UPDATE media SET device=device+100")


def test_restart_device_renumber_preserves_history_queue_and_new_writes(library) -> None:
    root, video, path = library
    entry, media_id = seed(*library)
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE media SET device=device+100")
    db = Database(path)
    before = dict(db.connection.execute("SELECT * FROM media").fetchone())
    projection = db.history_for(video, root)
    assert projection is not None and projection.media_id == media_id
    assert projection.coverage_ms == 80_000
    assert db.export_progress(root)[video.name]["coverageMs"] == 80_000
    # Same worker-produced identity used by Files; lookup itself is read-only.
    validated = _file_identity(video, root)
    assert validated is not None
    assert db.history_for_identities([validated])[video] == projection
    assert dict(db.connection.execute("SELECT * FROM media").fetchone()) == before
    assert db.get_current_id() == db.get_selected_id() == entry
    assert db.ensure_media(video) == media_id
    assert db.add_entry(video) == entry
    db.merge_coverage(video, [(90_000, 95_000)], 100_000)
    assert db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    assert db.history_for(video).coverage_ms == 85_000
    db.close()
    db = Database(path)
    assert db.history_for(video).coverage_ms == 85_000
    db.close()


@pytest.mark.parametrize("volume", ["volume-b", None])
def test_volume_change_or_unavailable_uuid_never_inherits(library, monkeypatch, volume) -> None:
    root, video, path = library
    seed(*library)
    monkeypatch.setattr(identity, "volume_uuid", lambda path: volume)
    db = Database(path)
    assert db.history_for(video, root) is None
    assert db.export_progress(root) == {}
    assert not db.media_fingerprint(1).matches(file_identity(video))
    if volume is None:
        with pytest.raises(RuntimeError, match="cannot validate saved media volume UUID"):
            db.ensure_media(video)
        assert db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == 1
    db.close()


@pytest.mark.parametrize("field", ["inode", "size", "mtime_ns"])
def test_replacement_metadata_is_still_rejected(library, field) -> None:
    root, video, path = library
    seed(*library)
    db = Database(path)
    db.connection.execute(f"UPDATE media SET {field}={field}+1")
    assert db.history_for(video, root) is None
    assert db.export_progress(root) == {}
    assert db.ensure_media(video) != 1
    db.close()


def test_rename_is_not_silently_associated(library) -> None:
    root, video, path = library
    seed(*library)
    renamed = video.with_name("renamed.mkv")
    video.rename(renamed)
    db = Database(path)
    assert db.history_for(renamed, root) is None
    assert db.export_progress(root) == {}
    assert db.connection.execute("SELECT COUNT(*) FROM coverage_ranges").fetchone()[0] == 2
    db.close()


def test_v4_migration_pins_only_exact_legacy_identity(library) -> None:
    root, video, path = library
    seed(*library)
    legacy(path, renumber=False)
    db = Database(path)
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 5
    assert db.media_fingerprint(1).volume_uuid == "volume-a"
    assert db.history_for(video, root).coverage_ms == 80_000
    db.close()


def test_v4_changed_device_requires_explicit_recovery(library) -> None:
    root, video, path = library
    seed(*library)
    legacy(path)
    db = Database(path)
    assert db.media_fingerprint(1).volume_uuid is None
    assert db.history_for(video, root) is None
    db.close()


def test_recovery_preview_is_read_only_apply_backs_up_and_preserves_history(library) -> None:
    root, video, path = library
    entry, media_id = seed(*library)
    legacy(path)
    with sqlite3.connect(path) as connection:
        old_media = connection.execute("SELECT * FROM media").fetchall()
        old_ranges = connection.execute("SELECT * FROM coverage_ranges").fetchall()
    preview = recover_history(path, root)
    assert preview["applied"] is False
    assert [row["mediaId"] for row in preview["candidates"]] == [media_id]
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT * FROM media").fetchall() == old_media
    result = recover_history(path, root, apply=True)
    assert result["applied"] is True
    backup = Path(result["backup"])
    assert backup.stat().st_mode & 0o777 == 0o600
    with sqlite3.connect(backup) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert connection.execute("SELECT * FROM media").fetchall() == old_media
        assert connection.execute("SELECT * FROM coverage_ranges").fetchall() == old_ranges
    db = Database(path)
    row = db.connection.execute("SELECT * FROM media").fetchone()
    assert tuple(row)[:-1] == old_media[0]
    assert row["volume_uuid"] == "volume-a"
    assert db.history_for(video, root).coverage_ms == 80_000
    assert db.get_current_id() == db.get_selected_id() == entry
    db.close()
    assert recover_history(path, root, apply=True) == {"applied": False, "candidates": []}


@pytest.mark.parametrize("case", ["missing", "replacement", "ambiguous", "symlink", "outside"])
def test_recovery_refuses_unsafe_or_ambiguous_matches(library, tmp_path, case) -> None:
    root, video, path = library
    seed(*library)
    legacy(path)
    if case == "missing":
        video.unlink()
    elif case == "replacement":
        video.write_bytes(b"different file")
    elif case == "ambiguous":
        with sqlite3.connect(path) as connection:
            connection.execute(
                "INSERT INTO media(path,device,inode,size,mtime_ns,first_observed,last_observed) "
                "SELECT path,device+1,inode,size,mtime_ns,first_observed,last_observed FROM media"
            )
    elif case == "symlink":
        moved = video.with_name("moved.mkv")
        video.rename(moved)
        video.symlink_to(moved)
    else:
        root = tmp_path / "other"
        root.mkdir()
    assert recover_history(path, root)["candidates"] == []
    assert recover_history(path, root, apply=True)["applied"] is False


def test_uuid_migration_failure_rolls_back_schema_and_rows(library, monkeypatch) -> None:
    _root, _video, path = library
    seed(*library)
    legacy(path, renumber=False)

    def fail(self):
        self.connection.execute("UPDATE media SET position_ms=0")
        raise RuntimeError("injected failure")

    monkeypatch.setattr(Database, "_pin_legacy_volumes", fail)
    with pytest.raises(DatabaseMigrationError, match="injected failure"):
        Database(path)
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
        assert "volume_uuid" not in {row[1] for row in connection.execute("PRAGMA table_info(media)")}
        assert connection.execute("SELECT position_ms FROM media").fetchone()[0] == 90_000


def test_queue_undo_accepts_stable_volume_after_device_change(library) -> None:
    root, _video, path = library
    entry, _media_id = seed(*library)
    db = Database(path)
    db.connection.execute("UPDATE media SET device=device+100")
    queue = QueueService(db)
    queue.open(root)
    queue.remove(0, stop_confirmed=True)
    assert queue.undo()
    assert queue.entries()[0].id == entry
    db.close()


def test_recovery_cli_preview_apply_and_controller_lock(library, capsys) -> None:
    root, _video, path = library
    seed(*library)
    legacy(path)
    args = ["--database", str(path), "recover-history", "--root", str(root)]
    assert main(args) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is False
    with ControllerLock(path.with_suffix(".lock")), pytest.raises(ControllerBusy):
        recover_history(path, root, apply=True)
    assert main([*args, "--apply"]) == 0
    assert json.loads(capsys.readouterr().out)["applied"] is True


def test_recovery_backup_includes_committed_wal(library) -> None:
    root, _video, path = library
    seed(*library)
    legacy(path)
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("UPDATE media SET position_ms=99000")
    writer.commit()
    assert path.with_name(path.name + "-wal").stat().st_size > 0
    try:
        result = recover_history(path, root, apply=True)
        with sqlite3.connect(result["backup"]) as backup:
            assert backup.execute("SELECT position_ms FROM media").fetchone()[0] == 99_000
    finally:
        writer.close()


def test_file_change_during_uuid_discovery_is_rejected(library, monkeypatch) -> None:
    _root, video, _path = library

    def discover(path):
        path.write_bytes(b"changed while validating")
        return "volume-a"

    monkeypatch.setattr(identity, "volume_uuid", discover)
    with pytest.raises(OSError, match="file changed during identity validation"):
        file_identity(video)


def test_device_fallback_is_conservative() -> None:
    stored = FileIdentity(1, 2, 3, 4)
    assert stored.matches(FileIdentity(1, 2, 3, 4))
    assert not stored.matches(FileIdentity(5, 2, 3, 4))
    assert not FileIdentity(1, 2, 3, 4, "a").matches(stored)


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin getattrlist integration")
def test_native_volume_uuid_is_stable_across_files_and_missing_is_unknown(tmp_path) -> None:
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.touch()
    second.touch()
    volume = identity.volume_uuid(first)
    assert volume is not None
    assert identity.volume_uuid(second) == volume
    assert identity.volume_uuid(tmp_path / "absent") is None
    assert first.stat().st_size == second.stat().st_size == 0
