from pathlib import Path

import pytest

from vlcq.database import Database
from vlcq.tui import ConfirmDeleteWatched, VLCQApp


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
        await pilot.press("escape")
        assert watched.exists()
        await pilot.click("#files-delete-watched")
        await pilot.click("#delete-watched-confirm")
        await pilot.pause()
        assert not watched.exists()
        assert unwatched.exists()
        assert subtitle.exists()
    database.close()
