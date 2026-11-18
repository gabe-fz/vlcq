"""Regression tests for playback-history storage edge cases."""

from __future__ import annotations

import asyncio
import errno
import sqlite3
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import vlcq.database as database_module
from vlcq.controller import PlaybackController, ResumeOffer
from vlcq.database import SCHEMA_VERSION, Database
from vlcq.identity import FileIdentity, file_identity
from vlcq.models import VLCStatus
from vlcq.queue import QueueService


@pytest.fixture
def library(tmp_path: Path):
    root = tmp_path / "library"
    root.mkdir()
    video = root / "e01.mkv"
    video.write_bytes(b"video bytes")
    db = Database(tmp_path / "state.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    yield root, video.resolve(), db, queue
    db.close()


def _identity_as(monkeypatch: pytest.MonkeyPatch, device: int, volume: str | None) -> None:
    def fake(path: Path, stat=None) -> FileIdentity:  # type: ignore[no-untyped-def]
        real = file_identity(path, stat)
        return FileIdentity(device, real.inode, real.size, real.mtime_ns, volume)

    monkeypatch.setattr(database_module, "file_identity", fake)


def _row_count(db: Database) -> int:
    return int(db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0])


def _controller(queue: QueueService, video: Path) -> PlaybackController:
    queue.add([video])
    entry = queue.entries()[0]
    queue.database.transition_current(entry.id, "playing")
    process = SimpleNamespace(process=SimpleNamespace(returncode=None))
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.active_vlc_id = "4"
    return controller


def _playing(video: Path, position_ms: int) -> VLCStatus:
    now = time.monotonic()
    return VLCStatus("playing", position_ms, 60_000, video, "4", 1.0, now, now)


# Bug 1: a history-save failure must not retire a healthy VLC connection.
async def test_history_save_failure_keeps_vlc_connection(library, monkeypatch) -> None:
    _root, video, _db, queue = library
    controller = _controller(queue, video)
    controller.poll_retry_delay = 0
    controller._running = True
    polls = {"n": 0}

    class Client:
        async def status(self) -> VLCStatus:
            polls["n"] += 1
            if polls["n"] >= 5:
                controller._running = False
            return _playing(video, 5_000 + polls["n"] * 1_000)

        async def command(self, *_args, **_kwargs) -> VLCStatus:
            return VLCStatus("playing")

        async def close(self) -> None:
            raise AssertionError("connection must not be closed")

    controller.client = Client()  # type: ignore[assignment]

    async def nothing(**_kwargs) -> None:
        return None

    monkeypatch.setattr(controller, "_synchronize_playlist_window_locked", nothing)
    monkeypatch.setattr(controller, "_apply_automatic_subtitles_locked", nothing)

    def ambiguous(*_args, **_kwargs) -> None:
        raise RuntimeError("ambiguous media identity")

    monkeypatch.setattr(queue, "update_progress", ambiguous)
    real_sleep = asyncio.sleep
    monkeypatch.setattr("vlcq.controller.asyncio.sleep", lambda _delay: real_sleep(0))
    await asyncio.wait_for(controller._poll(), 5)

    assert polls["n"] >= 5
    assert controller.client is not None
    assert controller.last_error is None
    assert controller.observation_error is not None
    assert "ambiguous media identity" in controller.observation_error


async def test_history_failure_notice_clears_after_a_good_poll(library, monkeypatch) -> None:
    _root, video, _db, queue = library
    controller = _controller(queue, video)
    controller._running = True
    polls = {"n": 0}

    class Client:
        async def status(self) -> VLCStatus:
            polls["n"] += 1
            if polls["n"] >= 2:
                controller._running = False
            return _playing(video, polls["n"] * 1_000)

        async def close(self) -> None:
            return None

    controller.client = Client()  # type: ignore[assignment]

    async def nothing(**_kwargs) -> None:
        return None

    monkeypatch.setattr(controller, "_synchronize_playlist_window_locked", nothing)
    monkeypatch.setattr(controller, "_apply_automatic_subtitles_locked", nothing)
    original = queue.update_progress

    def fail_once(*args, **kwargs) -> None:
        if polls["n"] == 1:
            raise RuntimeError("locked")
        original(*args, **kwargs)

    monkeypatch.setattr(queue, "update_progress", fail_once)
    real_sleep = asyncio.sleep
    monkeypatch.setattr("vlcq.controller.asyncio.sleep", lambda _delay: real_sleep(0))
    await asyncio.wait_for(controller._poll(), 5)
    assert controller.observation_error is None


# Bug 2: legacy unpinned history plus a later UUID-pinned row.
def test_renumbered_legacy_history_is_not_shadowed_by_a_new_row(library, monkeypatch) -> None:
    _root, video, db, _queue = library
    _identity_as(monkeypatch, 111, None)
    db.merge_progress(video, 60_000, 600_000)
    _identity_as(monkeypatch, 222, "volume-a")
    with pytest.raises(RuntimeError, match="recover-history"):
        db.merge_progress(video, 1_000, 600_000)
    assert _row_count(db) == 1
    assert db.connection.execute("SELECT position_ms FROM media").fetchone()[0] == 60_000


def test_existing_legacy_and_pinned_duplicates_resolve_to_pinned_row(
    library, monkeypatch
) -> None:
    root, video, db, _queue = library
    _identity_as(monkeypatch, 222, "volume-a")
    db.merge_progress(video, 30_000, 600_000)
    pinned_id = db.ensure_media(video)
    stat = video.stat()
    # A pre-fix database could already hold a legacy device-only row as well.
    db.connection.execute(
        "INSERT INTO media(path,device,inode,size,mtime_ns,position_ms,first_observed,"
        "last_observed) VALUES(?,?,?,?,?,?,?,?)",
        (str(video), 111, stat.st_ino, stat.st_size, stat.st_mtime_ns, 90_000, "t", "t"),
    )
    _identity_as(monkeypatch, 111, "volume-a")  # device number 111 comes back
    assert db.ensure_media(video) == pinned_id
    db.merge_progress(video, 40_000, 600_000)
    assert db.history_for(video).position_ms == 40_000
    assert db.export_progress(root)["e01.mkv"]["positionMs"] == 40_000
    assert _row_count(db) == 2


# Bug 3: a natural end with low coverage must not offer resume at the end.
def test_natural_completion_does_not_offer_resume_at_the_end(library) -> None:
    _root, video, db, _queue = library
    db.merge_coverage(video, [(0, 60_000)], 600_000)
    db.merge_progress(video, 598_000, 600_000, resume_position_ms=598_000)
    db.merge_progress(video, 598_000, 600_000, completed=True, trustworthy=False)
    offer = ResumeOffer(db.history_for(video))
    assert not offer.completed  # coverage is only 10%
    assert not offer.usable_resume
    assert offer.position_ms == 0


def test_rewatch_after_completion_still_offers_resume(library) -> None:
    _root, video, db, _queue = library
    db.merge_progress(video, 598_000, 600_000, completed=True)
    db.set_resume_position(video, 120_000, 600_000)
    offer = ResumeOffer(db.history_for(video))
    assert offer.usable_resume and offer.position_ms == 120_000


# Bug 4: duration follows the latest positive VLC reading.
def test_inflated_duration_estimate_does_not_stick(library) -> None:
    _root, video, db, _queue = library
    db.merge_coverage(video, [(0, 600_000)], 600_000)
    db.merge_progress(video, 1_000, 36_000_000)
    db.merge_progress(video, 2_000, 600_000)
    db.merge_progress(video, 3_000, 0)  # unknown length is ignored
    history = db.history_for(video)
    assert history.duration_ms == 600_000
    assert history.coverage_percentage() == 100
    db.merge_coverage(video, [], 36_000_000)
    db.merge_coverage(video, [], 600_000)
    assert db.history_for(video).duration_ms == 600_000


# Bug 5: a file changing mid-check is not a missing file.
async def test_transient_identity_change_does_not_mark_entry_missing(
    library, monkeypatch
) -> None:
    _root, video, _db, queue = library
    controller = _controller(queue, video)

    def stale(*_args, **_kwargs) -> None:
        raise OSError(errno.ESTALE, "file changed during identity validation")

    monkeypatch.setattr(queue, "update_progress", stale)
    await controller._observe(_playing(video, 5_000))
    assert queue.entries()[0].state == "playing"
    assert controller._history_failure is not None


async def test_deleted_file_is_still_marked_missing(library) -> None:
    _root, video, _db, queue = library
    controller = _controller(queue, video)
    status = _playing(video, 5_000)
    video.unlink()
    await controller._observe(status)
    assert queue.entries()[0].state == "missing"


# Bug 6: a different volume reusing a device number with identical metadata.
def test_different_volume_with_same_device_number_gets_its_own_row(
    library, monkeypatch
) -> None:
    _root, video, db, _queue = library
    _identity_as(monkeypatch, 333, "volume-a")
    db.merge_progress(video, 1_000, 10_000)
    _identity_as(monkeypatch, 333, "volume-b")
    db.merge_progress(video, 2_000, 10_000)
    assert _row_count(db) == 2
    assert db.history_for_identities([(video, database_module.file_identity(video))])[
        video
    ].position_ms == 2_000


def test_identity_index_still_rejects_true_duplicates(library) -> None:
    _root, video, db, _queue = library
    db.ensure_media(video)
    with pytest.raises(sqlite3.IntegrityError):
        db.connection.execute(
            "INSERT INTO media(path,device,inode,size,mtime_ns,volume_uuid,first_observed,"
            "last_observed) SELECT path,device+1,inode,size,mtime_ns,volume_uuid,'t','t' "
            "FROM media"
        )


def test_v5_database_migrates_without_losing_ids_ranges_or_queue(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    video = root / "e01.mkv"
    video.write_bytes(b"video bytes")
    path = tmp_path / "state.sqlite3"
    db = Database(path)
    queue = QueueService(db)
    queue.open(root)
    queue.add([video])
    db.merge_progress(video, 90_000, 100_000)
    db.merge_coverage(video, [(0, 30_000), (40_000, 90_000)], 100_000)
    media_id = db.ensure_media(video)
    before = dict(db.connection.execute("SELECT * FROM media").fetchone())
    db.close()

    # Recreate the v5 shape: inline device-based UNIQUE constraint.
    with sqlite3.connect(path) as connection:
        connection.execute("PRAGMA foreign_keys=OFF")
        connection.execute(
            "CREATE TABLE media_v5(id INTEGER PRIMARY KEY, path TEXT NOT NULL, "
            "device INTEGER NOT NULL, inode INTEGER NOT NULL, size INTEGER NOT NULL, "
            "mtime_ns INTEGER NOT NULL, position_ms INTEGER NOT NULL DEFAULT 0, "
            "duration_ms INTEGER NOT NULL DEFAULT 0, "
            "completion_observed INTEGER NOT NULL DEFAULT 0, "
            "first_observed TEXT NOT NULL, last_observed TEXT NOT NULL, "
            "resume_position_ms INTEGER, last_played_at TEXT, coverage_initialized_at TEXT, "
            "volume_uuid TEXT, UNIQUE(path, device, inode, size, mtime_ns))"
        )
        connection.execute("INSERT INTO media_v5 SELECT * FROM media")
        connection.execute("DROP TABLE media")
        connection.execute("ALTER TABLE media_v5 RENAME TO media")
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        connection.execute("PRAGMA user_version=5")

    db = Database(path)
    try:
        assert db.connection.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION
        assert dict(db.connection.execute("SELECT * FROM media").fetchone()) == before
        assert db.coverage_ranges_for_media(media_id) == ((0, 30_000), (40_000, 90_000))
        assert db.queue_entries()[0].media_id == media_id
        assert db.connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert db.connection.execute("PRAGMA foreign_key_check").fetchall() == []
        table_sql = db.connection.execute(
            "SELECT sql FROM sqlite_master WHERE name='media'"
        ).fetchone()[0]
        assert "UNIQUE" not in table_sql
    finally:
        db.close()
