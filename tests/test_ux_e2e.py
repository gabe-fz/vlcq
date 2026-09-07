from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button, ListView

from vlcq.database import Database
from vlcq.queue import QueueService
from vlcq.tui import VLCQApp


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40), (80, 50)])
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

    async def choose_menu_action(pilot, selector: str, prefix: str) -> None:
        await pilot.click(selector)
        await pilot.pause()
        app.refresh_playback()  # Polling while a menu is open must not retarget it.
        await pilot.pause()
        button = next(button for button in app.screen.query(Button) if str(button.label).startswith(prefix))
        # The menu exposes only the action appropriate to this source.
        await pilot.click(button)
        await pilot.pause()

    async with app.run_test(size=size) as pilot:
        browser = app.query_one("#browser", ListView)
        await pilot.pause()
        await pilot.click("#files-toggle")
        await pilot.click("#files-toggle")
        await pilot.resize_terminal(80, 50)
        await pilot.resize_terminal(*size)
        await pilot.pause()
        await pilot.click(browser.children[0])  # mouse folder navigation
        await pilot.pause()
        assert app.browser_path == folder.resolve()

        await pilot.click(".browser-check")
        await choose_menu_action(pilot, "#files-actions", "Add & play")
        assert app.queue.entries() == []  # Cancel has no queue side effects.
        for selector in ("#resume-choice", "#start-over-choice", "#resume-cancel"):
            button = app.query_one(selector, Button)
            assert button.region.height == 1
            assert button.content_region.height == 1
        await pilot.click("#resume-cancel")
        await choose_menu_action(pilot, "#files-actions", "Add & play")
        await pilot.click("#resume-choice")
        await pilot.pause()
        assert app.queue.current() is not None

        # Mouse transport and queue controls target the active/highlighted object.
        await choose_menu_action(pilot, "#player-menu", "Pause / resume")
        browser.index = 1
        await pilot.click(".browser-check")
        await choose_menu_action(pilot, "#files-actions", "Add to end")
        queue_view = app.query_one("#queue", ListView)
        queue_view.focus()
        queue_view.index = 0
        before_undo = len(app.queue.entries())
        await choose_menu_action(pilot, "#queue-actions", "Move up")
        await choose_menu_action(pilot, "#queue-actions", "Remove from queue")
        await choose_menu_action(pilot, "#queue-actions", "Undo latest removal")
        assert len(app.queue.entries()) == before_undo

        await choose_menu_action(pilot, "#queue-actions", "Clear queue")
        await pilot.click("#clear-all-confirm")
        await pilot.pause()
        assert app.queue.entries() == []
        # Clear's undo is exposed from the same Queue menu.
        await choose_menu_action(pilot, "#queue-actions", "Undo latest removal")
        assert len(app.queue.entries()) == before_undo

        await choose_menu_action(pilot, "#player-menu", "Help")
        await pilot.click("#help-close")
        assert any(state in str(app.query_one("#player").renderable).lower() for state in ("offline", "disconnected"))
        await choose_menu_action(pilot, "#player-menu", "Quit")

    db.close()
