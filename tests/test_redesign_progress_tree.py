from __future__ import annotations

import asyncio
import threading
import time
from contextlib import suppress
from pathlib import Path

import pytest
from textual.widgets import Button, ListView

from vlcq.cli import main
from vlcq.config import resolve_watched_percent
from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.paths import discover_tree
from vlcq.progress import clamped_percentage, is_watched
from vlcq.queue import QueueService
from vlcq.tui import HistoricalCoverage, ItemProgress, VLCQApp, render_filename


def video(path: Path, content: bytes = b"video") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return path


def test_watched_configuration_helpers_and_export_are_threshold_consistent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("VLCQ_WATCHED_PERCENT", raising=False)
    assert resolve_watched_percent() == 90
    monkeypatch.setenv("VLCQ_WATCHED_PERCENT", "75")
    assert resolve_watched_percent() == 75
    for invalid in ("", "0", "101", "90.0", " 09 ", "-1"):
        with pytest.raises(ValueError, match="1 through 100"):
            resolve_watched_percent(invalid)

    assert clamped_percentage(45_000, 60_000) == 75
    assert clamped_percentage(70_000, 60_000) == 100
    assert clamped_percentage(1_000, 0) is None
    assert is_watched(54_000, 60_000, threshold=90)
    assert not is_watched(53_999, 60_000, threshold=90)
    assert is_watched(0, 0, completion_observed=True)
    assert not is_watched(50_000, 0, threshold=50)
    rendered_name = render_filename("[WEB-DL] Episode 01.mkv")
    assert rendered_name.plain == "[WEB-DL] Episode 01.mkv"
    assert len({str(span.style) for span in rendered_name.spans}) >= 4

    root = tmp_path / "library"
    item = video(root / "episode.mkv")
    database = Database(tmp_path / "state.sqlite3")
    database.merge_progress(item, 45_000, 60_000)
    default_record = database.export_progress(root)["episode.mkv"]
    custom_record = database.export_progress(root, 75)["episode.mkv"]
    assert default_record.keys() == custom_record.keys()
    assert default_record["completionObserved"] is False
    assert custom_record["completionObserved"] is True
    assert custom_record["watchedPercent"] == 75
    database.close()

    invalid_database = tmp_path / "invalid.sqlite3"
    monkeypatch.setenv("VLCQ_WATCHED_PERCENT", "not-a-percentage")
    assert main(["--database", str(invalid_database), "progress", "--root", str(root), "--json"]) == 2
    assert "1 through 100" in capsys.readouterr().err
    assert not invalid_database.exists()


class RecordingClient:
    def __init__(self, path: Path, duration_ms: int = 10_000) -> None:
        self.path = path
        self.duration_ms = duration_ms
        self.commands: list[tuple[str, dict[str, str | int]]] = []
        self.played: list[Path] = []

    async def play(self, path: Path) -> VLCStatus:
        self.played.append(path)
        return VLCStatus("playing", 0, self.duration_ms, path)

    async def command(self, command: str, **parameters: str | int) -> VLCStatus:
        self.commands.append((command, parameters))
        return VLCStatus("playing", 0, self.duration_ms, self.path)

    async def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_threshold_classification_does_not_advance_and_watched_replay_starts_over(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    first = video(root / "first.mkv", b"first")
    second = video(root / "second.mkv", b"second")
    database = Database(tmp_path / "state.sqlite3")
    queue = QueueService(database, watched_percent=50)
    queue.open(root)
    queue.add([first, second])
    queue.play_now(0)
    controller = PlaybackController(queue, watched_percent=50)
    client = RecordingClient(first)
    controller.client = client  # type: ignore[assignment]

    await controller._observe(VLCStatus("playing", 5_000, 10_000, first.resolve()))
    assert queue.current() is not None and queue.current().path == first.resolve()
    assert queue.entries()[0].state == "playing"
    assert database.progress_for(first)["completion_observed"] == 0

    database.merge_progress(second, 5_000, 10_000)
    database.merge_coverage(second, [(0, 5_000)], 10_000)
    assert await controller.play_with_policy(1)
    assert client.played == [second.resolve()]
    history = database.history_for(second, root=root)
    assert history is not None
    assert history.position_ms == 5_000
    assert history.resume_position_ms == 0
    assert history.coverage_watched(50)
    database.close()


def test_recursive_discovery_is_confined_natural_and_deduplicated(tmp_path: Path) -> None:
    root = tmp_path / "library"
    episode_two = video(root / "season" / "episode2.mkv", b"two")
    episode_ten = video(root / "season" / "episode10.mkv", b"ten")
    video(root / ".hidden.mkv")
    video(root / "notes.txt")
    outside = video(tmp_path / "outside" / "escape.mkv")
    (root / "alias-season").symlink_to(root / "season", target_is_directory=True)
    (root / "season" / "cycle").symlink_to(root, target_is_directory=True)
    (root / "escape").symlink_to(outside.parent, target_is_directory=True)

    entries = discover_tree(root)
    files = [entry.path for entry in entries if not entry.is_dir]
    assert files == [episode_two.resolve(), episode_ten.resolve()]
    assert len({entry.path for entry in entries}) == len(entries)
    assert all(entry.path.is_relative_to(root.resolve()) for entry in entries)


@pytest.mark.asyncio
async def test_tree_coverage_filters_keyboard_transport_and_read_only_rows(tmp_path: Path) -> None:
    root = tmp_path / "library"
    left = video(root / "left" / "left-episode.mkv", b"left")
    right = video(root / "right" / "right-episode.mkv", b"right")
    unknown = video(root / "unknown.mkv", b"unknown")
    unplayed = video(root / "unplayed.mkv", b"unplayed")
    database = Database(tmp_path / "state.sqlite3")
    database.merge_progress(left, 45_000, 60_000)
    database.merge_coverage(left, [(0, 45_000)], 60_000)
    database.merge_progress(right, 85_000, 100_000)
    database.merge_coverage(right, [(0, 85_000)], 100_000)
    database.merge_progress(unknown, 1_000, 0)
    database.initialize_coverage(unknown, 0)
    app = VLCQApp(root=root, database=database, no_vlc=True, watched_percent=80)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.1)
        browser = app.query_one("#browser", ListView)
        folders = {entry.name: entry for entry in app.browser_entries if entry.is_dir}
        await app._open_browser_folder(folders["left"].path)
        await pilot.pause(0.05)
        await app._open_browser_folder(folders["right"].path)
        await pilot.pause(0.05)
        assert {left.resolve(), right.resolve()}.issubset(
            {entry.path for entry in app.browser_entries}
        )
        assert {folders["left"].path, folders["right"].path}.issubset(app.expanded_paths)

        rows = {row.path: row for row in browser.children}
        partial_history = rows[left.resolve()].query_one(HistoricalCoverage)
        watched_history = rows[right.resolve()].query_one(HistoricalCoverage)
        unknown_history = rows[unknown.resolve()].query_one(HistoricalCoverage)
        unplayed_history = rows[unplayed.resolve()].query_one(HistoricalCoverage)
        assert "75%" in str(partial_history.renderable)
        assert "85%" in str(watched_history.renderable)
        assert "hist    —" in str(unknown_history.renderable)
        assert "hist    —" in str(unplayed_history.renderable)
        assert not rows[left.resolve()].query(ItemProgress)

        await pilot.click("#files-search")
        assert app.screen.__class__.__name__ == "SearchFilterPrompt"
        await pilot.click("#search-cancel")
        await pilot.click("#files-open")
        assert app.screen.__class__.__name__ == "RootPrompt"
        await pilot.click("#root-cancel")

        app.queue.add([left, right])
        app.queue.play_now(0)
        app.refresh_queue()
        await pilot.pause()
        current_before = app.queue.current()
        await pilot.click(partial_history)
        assert app.queue.current() == current_before

        await pilot.press("space")
        assert app.queue.current() is not None and app.queue.current().state == "paused"
        await pilot.press("space")
        assert app.queue.current() is not None and app.queue.current().state == "playing"
        await pilot.press("n")
        assert app.queue.current() is not None and app.queue.current().path == right.resolve()

        await pilot.click("#files-actions")
        file_overflow = {str(button.label) for button in app.screen.query(Button)}
        assert not any(label.startswith("Add to end") for label in file_overflow)
        await pilot.press("escape")
        await pilot.click("#queue-actions")
        queue_overflow = {str(button.label) for button in app.screen.query(Button)}
        assert "Clear watched/completed" not in queue_overflow
        assert {"Pause / resume", "Previous", "Next", "Seek back 10s"}.isdisjoint(
            queue_overflow
        )
        await pilot.press("escape")

        app.search_query = ""
        app.history_filter = "not-completed"
        await app.refresh_browser()
        assert right.resolve() not in {entry.path for entry in app.browser_entries}
        assert left.resolve() in {entry.path for entry in app.browser_entries}

        client = RecordingClient(right.resolve(), 100_000)
        app.no_vlc = False
        app.controller.client = client  # type: ignore[assignment]
        app.controller.status = VLCStatus("playing", 50_000, 100_000, right.resolve())
        app.refresh_playback()
        await pilot.pause(0.05)
        queue_row = app.query_one("#queue", ListView).children[1]
        current_progress = queue_row.query_one(ItemProgress)
        assert "50%" in str(current_progress.renderable)
        await pilot.click(current_progress)
        assert client.commands == []
        app.controller.status = VLCStatus("playing", 1_000, 0, right.resolve())
        app.refresh_playback()
        await pilot.pause()
        assert "--%" in str(current_progress.renderable)
        assert client.commands == []
        app.no_vlc = True
        app.controller.client = None

        await pilot.click("#queue-clear")
        assert app.screen.__class__.__name__ == "ConfirmClear"
        await pilot.click("#clear-confirm")
        await pilot.pause()
        assert right.exists()
        assert right.resolve() not in {entry.path for entry in app.queue.entries()}
    database.close()


@pytest.mark.asyncio
async def test_recursive_refresh_cancels_worker_and_slow_database_write_stays_responsive(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    item = video(root / "episode.mkv")
    database = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=database, no_vlc=True)

    started = threading.Event()
    cancelled = threading.Event()
    calls = 0

    def delayed_discovery(
        _root: Path, *, cancel=None, should_cancel=None  # type: ignore[no-untyped-def]
    ):
        nonlocal calls
        calls += 1
        if calls > 1:
            return []
        callback = cancel or should_cancel
        started.set()
        while callback is not None and not callback():
            time.sleep(0.005)
        cancelled.set()
        return []

    monkeypatch.setattr("vlcq.paths.discover_tree", delayed_discovery)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        first = asyncio.create_task(app._refresh_recursive_discovery())
        assert await asyncio.to_thread(started.wait, 1)
        await app._refresh_recursive_discovery()
        with suppress(asyncio.CancelledError):
            await first
        assert await asyncio.to_thread(cancelled.wait, 1)

        browser = app.query_one("#browser", ListView)
        browser.index = next(
            index for index, entry in enumerate(app.browser_entries) if entry.path == item.resolve()
        )
        original_add = app.queue.add

        def delayed_add(paths):  # type: ignore[no-untyped-def]
            time.sleep(0.15)
            return original_add(paths)

        monkeypatch.setattr(app.queue, "add", delayed_add)
        operation = asyncio.create_task(app.action_add_selected())
        await asyncio.sleep(0.02)
        assert not operation.done()
        browser.focus()
        assert app.focused is browser
        await operation
        assert [entry.path for entry in app.queue.entries()] == [item.resolve()]
    database.close()
