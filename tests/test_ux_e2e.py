from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import ListView

from vlcq.database import Database
from vlcq.queue import QueueService
from vlcq.tui import VLCQApp


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40)])
async def test_mouse_workflow_at_compact_and_wide_sizes(tmp_path: Path, size: tuple[int, int]) -> None:
    root = tmp_path / "library"
    folder = root / "season"
    folder.mkdir(parents=True)
    first = folder / "episode1.mkv"
    second = folder / "episode2.mkv"
    first.write_bytes(b"one")
    second.write_bytes(b"two")

    db = Database(tmp_path / "state.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.update_progress(first, 1_000, 10_000)
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=size) as pilot:
        browser = app.query_one("#browser", ListView)
        await pilot.click(browser.children[0])  # mouse folder navigation
        await pilot.pause()
        assert app.browser_path == folder.resolve()

        await pilot.click("#browser-check-0")
        await pilot.click("#browser-add-play")
        await pilot.pause()
        assert app.queue.entries() == []  # Cancel has no queue side effects.
        await pilot.click("#resume-cancel")
        await pilot.click("#browser-add-play")
        await pilot.pause()
        await pilot.click("#resume-choice")
        await pilot.pause()
        assert app.queue.current() is not None

        # Mouse transport and queue controls target the active/highlighted object.
        await pilot.click("#player-pause")
        browser.scroll_to(y=browser.max_scroll_y, animate=False)
        await pilot.pause()
        await pilot.click("#browser-check-1")
        await pilot.click("#browser-add")
        await pilot.pause()
        queue_view = app.query_one("#queue", ListView)
        queue_view.index = 0
        before_undo = len(app.queue.entries())
        await pilot.click("#queue-up")
        await pilot.click("#queue-remove")
        await pilot.click("#queue-undo")
        assert len(app.queue.entries()) == before_undo

        await pilot.click("#queue-clear")
        await pilot.pause()
        await pilot.click("#clear-all-confirm")
        await pilot.pause()
        assert app.queue.entries() == []
        await pilot.click("#queue-undo")
        assert len(app.queue.entries()) == before_undo

        await pilot.click("#player-reconnect")
        assert "offline" in str(app.query_one("#status").renderable).lower() or "ready" in str(
            app.query_one("#status").renderable
        ).lower()
        await pilot.click("#app-quit")

    db.close()
