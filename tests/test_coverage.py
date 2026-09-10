from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vlcq.database import Database
from vlcq.progress import (
    PlaybackCoverageAccumulator,
    PlaybackObservation,
    clipped_coverage_ms,
    coverage_is_watched,
    coverage_percentage,
    merge_intervals,
)


def sample(
    second: float,
    position_ms: int,
    *,
    path: Path = Path("/library/video.mkv"),
    rate: float | None = 1.0,
    state: str = "playing",
    generation: int = 1,
    latency: float = 0.0,
) -> PlaybackObservation:
    return PlaybackObservation(
        generation,
        path,
        "42",
        state,
        position_ms,
        60_000,
        rate,
        second,
        second + latency,
    )


def test_interval_union_clipping_floor_and_raw_threshold() -> None:
    assert merge_intervals([(20, 30), (0, 10), (10, 20), (5, 8), (20, 30)]) == ((0, 30),)
    sparse = merge_intervals([(0, 10_000), (20_000, 30_000), (5_000, 10_000)])
    assert sparse == ((0, 10_000), (20_000, 30_000))
    assert clipped_coverage_ms(sparse, 25_000) == 15_000
    assert clipped_coverage_ms(sparse, 0) == 20_000
    assert coverage_percentage(59_999, 60_000) == 99
    assert coverage_percentage(60_000, 60_000) == 100
    assert coverage_percentage(1, 0) is None
    assert coverage_is_watched(54_000, 60_000)
    assert not coverage_is_watched(53_999, 60_000)
    with pytest.raises(ValueError):
        coverage_is_watched(1, 1, threshold=0)


def test_accumulator_qualifies_five_seconds_and_rejects_preview_seek_and_gap() -> None:
    accumulator = PlaybackCoverageAccumulator()
    for second in range(5):
        assert accumulator.add(sample(float(second), second * 1000)).ranges == ()
    evidence = accumulator.add(sample(5.0, 5_000))
    assert evidence.qualified
    assert evidence.ranges == ((0, 5_000),)
    assert accumulator.add(sample(6.0, 6_000)).ranges == ((5_000, 6_000),)

    accumulator.reset()
    for second in range(4):
        accumulator.add(sample(float(second), second * 1000))
    assert not accumulator.qualified
    assert accumulator.add(sample(4.0, 50_000)).ranges == ()
    assert not accumulator.qualified
    assert accumulator.add(sample(10.0, 51_000)).ranges == ()


def test_accumulator_handles_rate_quantization_and_resets_uncertain_samples() -> None:
    slow = PlaybackCoverageAccumulator()
    for second in range(7):
        evidence = slow.add(sample(float(second), second * 500, rate=0.5))
    assert evidence.qualified
    assert clipped_coverage_ms(evidence.ranges) > 0

    quantized = PlaybackCoverageAccumulator()
    quantized.add(sample(0.0, 0))
    assert quantized.add(sample(0.4, 0)).initialized
    assert quantized.add(sample(1.0, 1_000)).ranges == ()

    for invalid in (
        sample(2.0, 2_000, rate=None),
        sample(2.0, 2_000, state="paused"),
        sample(2.0, 2_000, latency=3.0),
        sample(9.0, 9_000),
        sample(2.0, 1_000),
    ):
        quantized.add(invalid)
        assert not quantized.qualified


def test_coverage_storage_is_idempotent_durable_and_replacement_safe(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"original")
    path = tmp_path / "state.sqlite3"
    db = Database(path)
    db.merge_progress(video, 55_000, 60_000, completed=True)
    assert db.history_for(video, root=root).coverage_ms is None  # type: ignore[union-attr]
    db.merge_coverage(video, [(0, 10_000), (20_000, 30_000)], 60_000)
    db.merge_coverage(video, [(5_000, 10_000), (20_000, 30_000)], 60_000)
    history = db.history_for(video, root=root)
    assert history is not None
    assert history.coverage_ranges == ((0, 10_000), (20_000, 30_000))
    assert history.coverage_ms == 20_000
    assert history.coverage_percentage() == 33
    exported = db.export_progress(root)["episode.mkv"]
    assert exported["completionObserved"] is True
    assert exported["coverageMs"] == 20_000
    assert exported["coveragePercent"] == 33
    assert exported["coverageWatched"] is False
    unknown = root / "unknown.mkv"
    unknown.write_bytes(b"unknown")
    db.initialize_coverage(unknown)
    unknown_export = db.export_progress(root)["unknown.mkv"]
    assert unknown_export["coverageMs"] == 0
    assert unknown_export["coveragePercent"] is None
    assert unknown_export["coverageWatched"] is None
    assert db.connection.execute("SELECT COUNT(*) FROM coverage_ranges").fetchone()[0] == 2
    db.close()

    reopened = Database(path)
    assert reopened.history_for(video, root=root).coverage_ms == 20_000  # type: ignore[union-attr]
    video.write_bytes(b"replacement with another fingerprint")
    assert reopened.history_for(video, root=root) is None
    assert set(reopened.export_progress(root)) == {"unknown.mkv"}
    reopened.close()


@pytest.mark.parametrize("legacy_version", [1, 2])
def test_legacy_migration_preserves_queue_identities_without_inferred_ranges(
    tmp_path: Path, legacy_version: int
) -> None:
    root = tmp_path / "library"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"video")
    path = tmp_path / "legacy.sqlite3"
    stat = video.stat()
    connection = sqlite3.connect(path)
    resume_columns = (
        ", resume_position_ms INTEGER, last_played_at TEXT" if legacy_version == 2 else ""
    )
    connection.executescript(
        f"""
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE queues(id INTEGER PRIMARY KEY, root TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
        CREATE TABLE media(id INTEGER PRIMARY KEY, path TEXT NOT NULL, device INTEGER NOT NULL, inode INTEGER NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, position_ms INTEGER NOT NULL DEFAULT 0, duration_ms INTEGER NOT NULL DEFAULT 0, completion_observed INTEGER NOT NULL DEFAULT 0, first_observed TEXT NOT NULL, last_observed TEXT NOT NULL {resume_columns}, UNIQUE(path,device,inode,size,mtime_ns));
        CREATE TABLE queue_entries(id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL REFERENCES queues(id), position INTEGER NOT NULL, media_id INTEGER NOT NULL REFERENCES media(id), state TEXT NOT NULL DEFAULT 'queued', UNIQUE(queue_id,position));
        INSERT INTO queues VALUES(1, '/placeholder', 'created', 'updated');
        INSERT INTO settings VALUES('active_queue','1');
        INSERT INTO settings VALUES('current_entry','7');
        INSERT INTO settings VALUES('selected_entry','7');
        """
    )
    connection.execute("UPDATE queues SET root=? WHERE id=1", (str(root.resolve()),))
    connection.execute(
        "INSERT INTO media(id,path,device,inode,size,mtime_ns,position_ms,duration_ms,"
        "completion_observed,first_observed,last_observed) "
        "VALUES(3,?,?,?,?,?,59000,60000,1,'first','last')",
        (str(video.resolve()), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns),
    )
    if legacy_version == 2:
        connection.execute(
            "UPDATE media SET resume_position_ms=17000,last_played_at='played' WHERE id=3"
        )
    connection.execute("INSERT INTO queue_entries VALUES(7,1,0,3,'stopped')")
    connection.execute(f"PRAGMA user_version={legacy_version}")
    connection.commit()
    connection.close()

    db = Database(path)
    assert db.connection.execute("PRAGMA user_version").fetchone()[0] == 4
    assert db.get_current_id() == 7
    assert db.get_selected_id() == 7
    history = db.history_for(video, root=root)
    assert history is not None
    assert history.position_ms == 59_000
    assert history.resume_position_ms == (17_000 if legacy_version == 2 else None)
    assert history.completion_observed
    assert history.coverage_ms is None
    assert db.connection.execute("SELECT COUNT(*) FROM coverage_ranges").fetchone()[0] == 0
    db.close()
