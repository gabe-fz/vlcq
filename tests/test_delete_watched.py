from pathlib import Path

import pytest
from textual.widgets import Button, Label

from vlcq.database import Database
from vlcq.tui import ConfirmDeleteWatched, HistoricalCoverage, VLCQApp, render_filename


@pytest.mark.asyncio
async def test_delete_watched_confirmation(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    watched = root / "[watched].mkv"
    unwatched = root / "unwatched.mkv"
    subtitle = root / "[watched].srt"
    for path in (watched, unwatched, subtitle):
        path.write_bytes(b"content")
    database = Database(tmp_path / "state.sqlite3")
    database.merge_coverage(watched, [(0, 80_000)], 100_000)
    database.merge_coverage(unwatched, [(0, 79_999)], 100_000)
    app = VLCQApp(database=database, root=root, no_vlc=True)
    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.click("#files-delete-watched")
        assert isinstance(app.screen, ConfirmDeleteWatched)
        assert app.screen.paths == (watched,)
        assert app.screen.query_one(Label).renderable == render_filename(watched.name)
        assert "80%" in str(app.screen.query_one(HistoricalCoverage).renderable)
        assert app.screen.query(".subtitle-subitem")
        assert str(app.screen.query_one(".delete-path").renderable) == str(watched)
        await pilot.press("escape")
        assert watched.exists()
        await pilot.click("#files-delete-watched")
        await pilot.click("#delete-watched-confirm")
        await pilot.pause()
        assert not watched.exists()
        assert unwatched.exists()
        assert subtitle.exists()
    database.close()


@pytest.mark.asyncio
async def test_headers_pack_buttons_on_one_row(tmp_path: Path) -> None:
    database = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(database=database, root=tmp_path, no_vlc=True)
    async with app.run_test(size=(160, 30)) as pilot:
        await pilot.pause()
        delete = app.query_one("#files-delete-watched", Button)
        assert delete.display
        assert delete.region.y == app.query_one("#files-open", Button).region.y
        assert app.query_one("#queue-extra-help", Button).display
        for width in (60, 80, 100, 120, 160):
            await pilot.resize_terminal(width, 30)
            await pilot.pause()
            for section in ("files", "queue"):
                header = app.query_one(f"#{section}-header")
                for button in header.query(Button):
                    if button.display:
                        assert button.region.right <= width
                        assert button.region.y == header.region.y
    database.close()
