from pathlib import Path
from unittest.mock import patch

from rich.text import Text
from textual.widgets import Button, Label, Static

from vlcq.database import Database
from vlcq.tui import HistoricalCoverage, ItemProgress, VLCQApp, _update_if_changed


def test_base_style_changes_are_not_hidden_by_rich_text_equality() -> None:
    widget = Static(Text("same text", style="red"))
    with patch.object(widget, "refresh") as refresh:
        _update_if_changed(widget, Text("same text", style="green"))
        refresh.assert_called_once_with(layout=True)
    with patch.object(widget, "refresh") as refresh:
        _update_if_changed(widget, Text("same text", style="green"))
        refresh.assert_not_called()


def test_unchanged_coverage_does_not_request_layout() -> None:
    widget = HistoricalCoverage(40)
    with patch.object(widget, "refresh") as refresh:
        widget.set_percentage(40)
        refresh.assert_not_called()
        widget.set_percentage(40, watched=True)
        refresh.assert_called_once_with(layout=True)
    assert isinstance(widget.renderable, Text)
    assert widget.renderable.spans[-1].style == "bold green"


def test_progress_updates_only_when_visible_value_changes() -> None:
    widget = ItemProgress(10_000, 100_000)
    with patch.object(widget, "refresh") as refresh:
        widget.set_position(10_001, 100_000)
        refresh.assert_not_called()
        widget.set_position(11_000, 100_000)
        refresh.assert_called_once_with(layout=True)
    with patch.object(widget, "refresh") as refresh:
        widget.set_position(None, None)
        refresh.assert_called_once_with(layout=True)
    with patch.object(widget, "refresh") as refresh:
        widget.set_position(None, None)
        refresh.assert_not_called()


async def test_unchanged_browser_rows_do_not_repaint(tmp_path: Path) -> None:
    root = tmp_path / "media"
    root.mkdir()
    (root / "episode.mkv").write_bytes(b"video")
    database = Database(tmp_path / "db.sqlite3")
    try:
        app = VLCQApp(root=root, database=database, no_vlc=True)
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            # Width-only refresh must not transiently reveal state-dependent
            # actions, which the next header pass would immediately hide again.
            assert not app.query_one("#queue-sort", Button).display
            app._refresh_responsive_controls()
            assert not app.query_one("#queue-sort", Button).display
            label = app.query_one("#browser .row-label", Label)
            with patch.object(label, "update", wraps=label.update) as update:
                app._apply_browser_history_rows()
                app._apply_browser_history_rows()
                update.assert_not_called()
    finally:
        database.close()


async def test_unchanged_queue_refresh_keeps_highlight_without_restyling(tmp_path: Path) -> None:
    from textual.widgets import ListView

    from vlcq.tui import QueueListItem

    root = tmp_path / "media"
    root.mkdir()
    for name in ("e01.mkv", "e02.mkv"):
        (root / name).write_bytes(b"video")
    database = Database(tmp_path / "db.sqlite3")
    try:
        app = VLCQApp(root=root, database=database, no_vlc=True)
        app.queue.add([root / "e01.mkv", root / "e02.mkv"])
        async with app.run_test(size=(100, 35)) as pilot:
            app.refresh_queue()
            await pilot.pause()
            view = app.query_one("#queue", ListView)
            view.focus()
            await pilot.pause()
            rows = [row for row in view.children if isinstance(row, QueueListItem)]
            assert rows[0].has_class("-highlight", "queue-selected")
            with patch.object(app, "update_styles", wraps=app.update_styles) as update_styles:
                app.refresh_queue()
                app.refresh_queue()
                update_styles.assert_not_called()
            # ListView's own highlight class must survive periodic refreshes.
            assert rows[0].has_class("-highlight", "queue-selected")
            assert not rows[1].has_class("queue-selected")
    finally:
        database.close()


async def test_keyboard_shortcuts_for_search_and_clearing_selection(tmp_path: Path) -> None:
    from vlcq.tui import SearchFilterPrompt

    root = tmp_path / "media"
    root.mkdir()
    (root / "e01.mkv").write_bytes(b"video")
    database = Database(tmp_path / "db.sqlite3")
    try:
        app = VLCQApp(root=root, database=database, no_vlc=True)
        async with app.run_test(size=(100, 35)) as pilot:
            await pilot.pause()
            await pilot.press("v")
            assert app.selected_paths
            await pilot.press("escape")
            assert not app.selected_paths
            await pilot.press("slash")
            await pilot.pause()
            assert isinstance(app.screen, SearchFilterPrompt)
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, SearchFilterPrompt)
    finally:
        database.close()
