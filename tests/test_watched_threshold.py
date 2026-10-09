from pathlib import Path

import pytest
from textual.widgets import Button

from vlcq.database import Database
from vlcq.tui import HistoricalCoverage, VLCQApp


@pytest.mark.asyncio
async def test_clear_watched_button_uses_eighty_percent_boundary(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    paths = [root / f"{name}.mkv" for name in ("below", "boundary", "above", "unknown")]
    for path in paths:
        path.write_bytes(b"video")
    database = Database(tmp_path / "state.sqlite3")
    for path, coverage in zip(paths, (79_999, 80_000, 80_001)):
        database.merge_coverage(path, [(0, coverage)], 100_000)
    app = VLCQApp(database=database, root=root, no_vlc=True)
    app.queue.add(paths)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        assert app.watched_percent == 80
        assert str(app.query_one("#queue-clear", Button).label) == "Clear watched"
        for path, watched in zip(paths, (False, True, True, False)):
            assert app._history_watched(path) is watched
        rendered = HistoricalCoverage(80, watched=app._history_watched(paths[1])).renderable
        assert any("green" in str(span.style) for span in rendered.spans)
        await pilot.click("#queue-clear")
        await pilot.click("#clear-confirm")
        await pilot.pause()
        assert [entry.path for entry in app.queue.entries()] == [paths[0], paths[3]]
        assert all(path.exists() for path in paths)
        assert database.history_for(paths[1], root=root).coverage_watched()
    database.close()
