from __future__ import annotations

from pathlib import Path

import pytest
from textual import events
from textual.widgets import Button, Input, Label, ListView, Static

from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService
from vlcq.tui import (
    ActionMenu,
    ContextAction,
    FileTarget,
    HistoricalCoverage,
    ItemProgress,
    QueueTarget,
    SubtitleSubitem,
    VLCQApp,
)


def make_files(root: Path, count: int = 8) -> list[Path]:
    root.mkdir(parents=True, exist_ok=True)
    paths = [root / f"episode-{index:02}.mkv" for index in range(count)]
    for path in paths:
        path.write_bytes(b"video")
    return paths


def test_progress_and_history_values_have_semantic_color_spans() -> None:
    low = ItemProgress(20_000, 100_000).renderable
    middle = ItemProgress(50_000, 100_000).renderable
    high = ItemProgress(90_000, 100_000).renderable
    assert any(str(span.style) == "bold red" for span in low.spans)
    assert any(str(span.style) == "bold yellow" for span in middle.spans)
    assert any(str(span.style) == "bold green" for span in high.spans)
    assert any(str(span.style) == "bright_black" for span in high.spans)

    partial_history = HistoricalCoverage(50).renderable
    watched_history = HistoricalCoverage(90, watched=True).renderable
    unknown_history = HistoricalCoverage(None).renderable
    assert any(str(span.style) == "yellow" for span in partial_history.spans)
    assert any(str(span.style) == "bold green" for span in watched_history.spans)
    assert any(str(span.style) == "bright_black" for span in unknown_history.spans)


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [(80, 24), (120, 40), (80, 50)])
async def test_stacked_sections_collapse_and_resize_without_rebuilding(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    root = tmp_path / "library"
    paths = make_files(root)
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=size) as pilot:
        await pilot.pause(0.1)
        files = app.query_one("#files-section")
        queue = app.query_one("#queue-section")
        browser = app.query_one("#browser", ListView)
        queue_view = app.query_one("#queue", ListView)
        app.queue.add(paths)
        app.refresh_queue()
        await pilot.pause()
        browser.index = min(2, len(browser.children) - 1)
        queue_view.index = 2
        original_browser_row = browser.children[2]
        original_queue_row = queue_view.children[2]
        assert files.region.y < queue.region.y
        assert app.query_one("#files-header").region.height == 1
        assert app.query_one("#queue-header").region.height == 1
        assert len(browser.displayed_children) >= 5
        assert len(queue_view.displayed_children) >= 5

        await pilot.click("#files-toggle")
        await pilot.pause()
        assert files.has_class("collapsed")
        assert not app.query_one("#files-body").display
        assert queue.region.height > files.region.height
        await pilot.click("#files-toggle")
        await pilot.pause()
        assert not files.has_class("collapsed")
        assert browser.children[2] is original_browser_row
        assert browser.index == 2

        await pilot.click("#queue-toggle")
        await pilot.pause()
        assert queue.has_class("collapsed")
        assert files.region.height > queue.region.height
        await pilot.click("#queue-toggle")
        await pilot.pause()
        assert queue_view.children[2] is original_queue_row
        assert queue_view.index == 2

        browser.focus()
        await pilot.pause()
        app._toggle_section("files")
        await pilot.pause()
        assert app.focused is app.query_one("#files-toggle")
        app._toggle_section("files")
        await pilot.resize_terminal(80, 24)
        await pilot.pause()
        await pilot.resize_terminal(80, 50)
        await pilot.pause()
        assert browser.children[2] is original_browser_row
        assert browser.index == 2
        assert not app.query("#status-pane")
        assert not app.query("#player")
        app._toggle_section("files")
        app._toggle_section("queue")
        assert app.query_one("#files-header").display
        assert app.query_one("#queue-header").display
        assert app.query_one("#queue-actions").display
    db.close()


@pytest.mark.asyncio
async def test_headers_lead_with_color_coded_pane_specific_controls(tmp_path: Path) -> None:
    root = tmp_path / "library"
    nested = root / "season"
    make_files(nested, 2)
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.1)
        folder = next(entry for entry in app.browser_entries if entry.is_dir)
        await app._open_browser_folder(folder.path)
        await pilot.pause()
        assert app.query_one("#files-title", Static).region.x < app.query_one("#files-open", Button).region.x
        assert app.query_one("#queue-title", Static).region.x < app.query_one("#queue-remove", Button).region.x
        assert app.query_one("#files-open", Button).region.x < 40
        assert app.query_one("#queue-remove", Button).region.x < 40
        assert "focus:" not in str(app.query_one("#files-title", Static).renderable)
        assert app.query_one("#files-open", Button).variant == "primary"
        assert app.query_one("#files-search", Button).variant == "success"
        assert app.query_one("#queue-remove", Button).variant == "error"
        assert app.query_one("#files-sort", Button).display
        assert not app.query_one("#queue-undo", Button).display
        assert not app.query("#player-seek-back")
        assert not app.query_one("#files-clear-selection", Button).display
        assert not app.query_one("#files-actions", Button).display
        assert app.query_one("#queue-actions", Button).display
        await pilot.click("#queue-actions")
        assert {str(button.label) for button in app.screen.query(Button)} == {
            "☐ Remember subtitles by show", "☑ Prefer English subtitles",
            "Reconnect", "Help", "Quit", "Last notice details"
        }
        await pilot.press("escape")

        await pilot.resize_terminal(160, 30)
        await pilot.pause()
        assert not app.query_one("#files-clear-selection", Button).display
        assert not app.query_one("#queue-clear-all", Button).display
        assert not app.query("#player-help")
        assert not app.query("#player-quit")
        assert not app.query_one("#files-actions", Button).display
        assert app.query_one("#queue-actions", Button).display
        assert not app.query("#player-menu")

        selected_path = (nested / "episode-00.mkv").resolve()
        app.selected_paths.add(selected_path)
        app.queue.add([selected_path])
        app.refresh_queue()
        await pilot.pause()
        assert app.query_one("#files-clear-selection", Button).display
        assert app.query_one("#queue-clear-all", Button).display

        await pilot.resize_terminal(80, 30)
        await pilot.pause()
        assert not app.query_one("#files-sort", Button).display
        assert not app.query_one("#queue-undo", Button).display
        assert not app.query("#player-seek-back")
        assert app.query_one("#files-actions", Button).display
        assert app.query_one("#queue-actions", Button).display
        assert not app.query("#player-menu")

        await pilot.click("#files-actions")
        files_actions = {str(button.label) for button in app.screen.query(Button)}
        assert files_actions == {"Up", "Reverse filename order", "Clear selection"}
        await pilot.press("escape")
        await pilot.click("#queue-actions")
        queue_actions = {str(button.label) for button in app.screen.query(Button)}
        assert queue_actions == {
            "Details", "Clear queue", "☐ Remember subtitles by show",
            "☑ Prefer English subtitles", "Reconnect", "Help", "Quit", "Last notice details"
        }
        await pilot.press("escape")
        queue_entry = app.queue.entries()[0]
        row_actions = app._context_actions_for_queue(
            QueueTarget(queue_entry.id, app._root_generation)
        )
        assert "Details" in {action.label for action in row_actions}
        assert {"Previous", "Next", "Seek back 10s"}.isdisjoint(
            {action.label for action in row_actions}
        )

    db.close()


@pytest.mark.asyncio
async def test_video_rows_add_compact_subtitle_subitems_without_changing_metadata(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    absent = root / "[WEB-DL][1080p] Episode [01] - A filename with literal brackets.mkv"
    progress = root / "episode-02.mkv"
    completed = root / "episode-03.mkv"
    for path in (absent, progress, completed):
        path.write_bytes(b"video")
    db = Database(tmp_path / "state.sqlite3")
    db.merge_progress(progress, 8_000, 42_000)
    db.merge_coverage(progress, [(0, 8_000)], 42_000)
    db.merge_progress(completed, 42_000, 42_000, completed=True)
    db.merge_coverage(completed, [(0, 42_000)], 42_000)
    queue = QueueService(db)
    queue.open(root)
    queue.add([completed])
    entry = queue.entries()[0]
    db.set_state(entry.id, "failed")
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.1)
        browser = app.query_one("#browser", ListView)
        rows = {row.path: row for row in browser.children}
        absent_label = rows[absent.resolve()].query_one(Label)
        progress_label = rows[progress.resolve()].query_one(Label)
        completed_label = rows[completed.resolve()].query_one(Label)
        assert absent.name in str(absent_label.renderable)
        assert "No recorded progress" not in str(absent_label.renderable)
        assert "In progress" not in str(progress_label.renderable)
        assert "Completed" not in str(completed_label.renderable)
        progress_history = rows[progress.resolve()].query_one(HistoricalCoverage)
        assert "hist  19%" in str(progress_history.renderable)
        assert "hist 100%" in str(rows[completed.resolve()].query_one(HistoricalCoverage).renderable)
        assert progress_history.region.x - progress_label.region.right <= 1
        absent_history = rows[absent.resolve()].query_one(HistoricalCoverage)
        assert absent_history.region.right <= browser.content_region.right
        checkbox = rows[absent.resolve()].query_one(".browser-check", Button)
        assert checkbox.region.width <= 3
        assert checkbox.region.height == 1
        assert all(row.region.height == 2 for row in browser.children)
        assert all(
            "↳ Subtitles:" in str(row.query_one(SubtitleSubitem).renderable)
            for row in browser.children
        )
        queue_row = app.query_one("#queue", ListView).children[0]
        assert queue_row.region.height == 2
        assert "×" in str(queue_row.query_one(Label).renderable)
        queue_label = queue_row.query_one(Label)
        queue_progress = queue_row.query_one(ItemProgress)
        queue_history = queue_row.query_one(HistoricalCoverage)
        assert "Completed" not in str(queue_label.renderable)
        assert "hist 100%" in str(queue_history.renderable)
        assert "100%" in str(queue_progress.renderable)
        assert queue_progress.region.x - queue_label.region.right <= 1
        assert queue_history.region.x - queue_progress.region.right <= 1
    db.close()


@pytest.mark.asyncio
async def test_details_is_read_only_complete_and_fingerprint_matched(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    absent = root / "absent.mkv"
    known = root / "known.mkv"
    absent.write_bytes(b"a")
    known.write_bytes(b"known")
    db = Database(tmp_path / "state.sqlite3")
    db.merge_progress(known, 8_000, 42_000, resume_position_ms=4_000, played_at="2026-01-02T03:04:05+00:00")
    db.set_root(root)
    media_before = db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]
    observed_before = db.connection.execute("SELECT last_observed FROM media").fetchone()[0]
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        browser = app.query_one("#browser", ListView)
        browser.index = next(i for i, entry in enumerate(app.browser_entries) if entry.path == absent.resolve())
        await app._open_details()
        await pilot.pause()
        content = str(app.query_one("#details-content", Static).renderable)
        assert f"Full path: {absent.resolve()}" in content
        assert "History: absent" in content
        assert "Current/resume position: unknown" in content
        await pilot.click("#details-close")
        browser.index = next(i for i, entry in enumerate(app.browser_entries) if entry.path == known.resolve())
        await app._open_details()
        await pilot.pause()
        content = str(app.query_one("#details-content", Static).renderable)
        assert "Current/resume position: 0:04" in content
        assert "Historical coverage: —" in content
        assert "Legacy maximum: 0:08" in content
        assert "Duration: 0:42" in content
        assert "Last trustworthy playback: 2026-01-02T03:04:05+00:00" in content
        await pilot.click("#details-close")
        known.write_bytes(b"replacement-with-a-new-fingerprint")
        await app._open_details()
        await pilot.pause()
        content = str(app.query_one("#details-content", Static).renderable)
        assert "History: absent" in content
        assert "Current/resume position: unknown" in content
        assert db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == media_before
        assert db.connection.execute("SELECT last_observed FROM media").fetchone()[0] == observed_before
    db.close()


@pytest.mark.asyncio
async def test_context_menu_entry_points_dismissal_focus_and_edge_clamping(tmp_path: Path) -> None:
    root = tmp_path / "library"
    paths = make_files(root, 2)
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.1)
        browser = app.query_one("#browser", ListView)
        await pilot.click("#files-actions")
        await pilot.pause()
        assert isinstance(app.screen, ActionMenu)
        menu = app.query_one("#context-menu")
        assert menu.region.right <= app.size.width
        assert menu.region.bottom <= app.size.height
        await pilot.press("escape")
        assert app.screen.__class__.__name__ == "Screen"
        assert app.focused is app.query_one("#files-actions")

        # A right-click highlights a video and opens actions without playback.
        row = next(row for row in browser.children if row.path == paths[0].resolve())
        browser.index = list(browser.children).index(row)
        app.on_click(events.Click(row, 1, 0, 0, 0, 3, False, False, False, row.region.x + 1, row.region.y))
        await pilot.pause()
        assert isinstance(app.screen, ActionMenu)
        assert app.browser_path == root.resolve()
        assert any(str(button.label).startswith("Play now") for button in app.screen.query(Button))
        await pilot.click(None, offset=(1, app.size.height - 1))
        await pilot.pause()
        assert app.screen.__class__.__name__ == "Screen"
    db.close()


@pytest.mark.asyncio
async def test_context_targets_are_identity_checked_before_dispatch(tmp_path: Path) -> None:
    root = tmp_path / "library"
    first, second = make_files(root, 2)
    db = Database(tmp_path / "state.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add([first, second])
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause(0.1)
        entries = app.queue.entries()
        stale = ContextAction("remove-queue", "Remove from queue", QueueTarget(entries[0].id, 0))
        db.remove_entries([entries[0].id])
        await app._dispatch_context_action(stale)
        assert [entry.path for entry in app.queue.entries()] == [second.resolve()]
        outside = tmp_path / "outside.mkv"
        outside.write_bytes(b"outside")
        unsafe = ContextAction("add-end-file", "Add to end", FileTarget(outside, 0))
        await app._dispatch_context_action(unsafe)
        assert [entry.path for entry in app.queue.entries()] == [second.resolve()]
        assert "outside" in app._notice.lower()
    db.close()


@pytest.mark.asyncio
async def test_on_demand_search_and_hidden_selection_summary(tmp_path: Path) -> None:
    root = tmp_path / "library"
    folder = root / "season"
    root.mkdir()
    root_file = root / "root-video.mkv"
    root_file.write_bytes(b"root")
    child = folder / "episode-01.mkv"
    folder.mkdir()
    child.write_bytes(b"child")
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.1)
        browser = app.query_one("#browser", ListView)
        root_index = next(i for i, entry in enumerate(app.browser_entries) if entry.path == root_file.resolve())
        browser.index = root_index
        await pilot.press("v")
        folder_index = next(i for i, entry in enumerate(app.browser_entries) if entry.is_dir)
        browser.index = folder_index
        await pilot.press("right")
        await pilot.pause()
        child_index = next(i for i, entry in enumerate(app.browser_entries) if entry.path == child.resolve())
        browser.index = child_index
        await pilot.press("v")
        assert "2 selected" in str(app.query_one("#files-title").renderable)
        await pilot.press("backspace")
        await pilot.pause()
        assert "1 hidden" in str(app.query_one("#files-title").renderable)
        app.action_search_filter()
        await pilot.pause()
        search = app.query_one("#search-input", Input)
        search.value = "episode"
        await pilot.click("#search-filter-not-completed")
        await pilot.click("#search-apply")
        await pilot.pause()
        assert app.search_query == "episode"
        assert app.history_filter == "not-completed"
        assert app.queue.entries() == []
        await app.action_clear_selection()
        assert app.selected_paths == set()
    db.close()


@pytest.mark.asyncio
async def test_two_pane_live_row_transient_notice_and_queue_application_menu(tmp_path: Path) -> None:
    root = tmp_path / "library"
    video = make_files(root, 1)[0]
    db = Database(tmp_path / "state.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add([video])
    entry = queue.play_now(0)
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.1)
        app.controller.status = VLCStatus("playing", 5_000, 20_000, video.resolve())
        app.refresh_playback()
        queue_row = app.query_one("#queue", ListView).children[0]
        assert "25%" in str(queue_row.query_one(ItemProgress).renderable)
        assert video.name in str(queue_row.query_one(Label).renderable)
        assert not app.query("#status-pane")
        assert not app.query("#player")
        assert not app.query_one("#notice").display

        app.update_status("A very long playback failure notice with complete details")
        await pilot.pause()
        notice = app.query_one("#notice", Static)
        assert notice.display and notice.region.height == 1
        assert "complete details" in str(notice.renderable)
        await pilot.click("#notice")
        assert not notice.display

        await pilot.click("#files-toggle")
        await pilot.click("#queue-toggle")
        await pilot.pause()
        assert app.query_one("#files-header").display
        assert app.query_one("#queue-header").display
        await pilot.click("#queue-actions")
        await pilot.pause()
        overflow_labels = {str(button.label) for button in app.screen.query(Button)}
        assert {"Details", "Reconnect", "Help", "Quit", "Last notice details"}.issubset(
            overflow_labels
        )
        assert {"Pause / resume", "Previous", "Next", "Seek back 10s"}.isdisjoint(
            overflow_labels
        )
        await pilot.press("escape")

        await pilot.press("space")
        assert app.queue.current() is not None and app.queue.current().state == "paused"
        await pilot.press("n")
        assert app.queue.current() is None
        await pilot.press("p")
        assert app.queue.current() is not None

        await pilot.click("#queue-actions")
        await pilot.click(next(button for button in app.screen.query(Button) if str(button.label) == "Help"))
        await pilot.click("#help-close")
        await pilot.click("#queue-actions")
        await pilot.click(next(button for button in app.screen.query(Button) if str(button.label) == "Quit"))
        await pilot.pause()
        assert app.screen.__class__.__name__ == "QuitPrompt"
        await pilot.click("#quit-cancel")
        assert app.screen.__class__.__name__ == "Screen"
        assert entry.path == video.resolve()
    db.close()
