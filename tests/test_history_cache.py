"""History reloads skip unchanged rows but never serve stale history."""

import sqlite3
from pathlib import Path

from vlcq.database import Database
from vlcq.tui import VLCQApp


def test_history_changes_since_tracks_own_writes_and_external_commits(tmp_path: Path) -> None:
    video = tmp_path / "e01.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    try:
        revision, changed = db.history_changes_since(-1)
        assert changed is None  # first sync: unknown baseline
        revision, changed = db.history_changes_since(revision)
        assert changed == set()
        db.merge_coverage(video, [(0, 10_000)], 60_000)
        revision, changed = db.history_changes_since(revision)
        assert changed == {video.resolve()}
        revision, changed = db.history_changes_since(revision)
        assert changed == set()
        other = sqlite3.connect(tmp_path / "db.sqlite3")
        try:
            other.execute("UPDATE media SET position_ms=position_ms+1")
            other.commit()
        finally:
            other.close()
        _revision, changed = db.history_changes_since(revision)
        assert changed is None
    finally:
        db.close()


async def _coverage_text(app: VLCQApp, video: Path) -> int | None:
    await app._load_history([video])
    return app._history_percentage(video)


async def test_cached_history_reload_reflects_new_progress(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    video = root / "e01.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    try:
        app = VLCQApp(root=root, database=db, no_vlc=True)
        assert await _coverage_text(app, video) is None
        db.merge_coverage(video, [(0, 30_000)], 60_000)
        assert await _coverage_text(app, video) == 50
        epoch = app._history_epoch
        assert await _coverage_text(app, video) == 50
        assert app._history_epoch == epoch  # unchanged reload is not a change
        other = sqlite3.connect(tmp_path / "db.sqlite3")
        try:
            other.execute("DELETE FROM coverage_ranges")
            other.execute("INSERT INTO coverage_ranges(media_id,start_ms,end_ms) SELECT id,0,60000 FROM media")
            other.commit()
        finally:
            other.close()
        assert await _coverage_text(app, video) == 100
        # Replacing the file changes its fingerprint, so old history disappears.
        video.write_bytes(b"different video bytes")
        assert await _coverage_text(app, video) is None
    finally:
        db.close()
