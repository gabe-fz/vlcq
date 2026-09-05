from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Button, Label, ListView, ProgressBar, Static

import vlcq.cli
from vlcq.cli import _resolve_paths, main
from vlcq.database import Database
from vlcq.paths import PathError
from vlcq.tui import VLCQApp
from vlcq.vlc import VLCError


def test_explicit_file_cli_establishes_root_and_seeds_queue(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "show"
    first = root / "s1" / "e1.mkv"
    second = root / "s2" / "e2.mkv"
    first.parent.mkdir(parents=True)
    second.parent.mkdir(parents=True)
    first.write_bytes(b"1")
    second.write_bytes(b"2")
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, root: Path, database: Database, no_vlc: bool, autoplay: bool) -> None:
            observed["root"] = root
            observed["entries"] = [entry.path for entry in database.queue_entries()]
            observed["no_vlc"] = no_vlc
            observed["autoplay"] = autoplay

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setattr(vlcq.cli, "VLCQApp", FakeApp)
    result = main(
        [
            "--database",
            str(tmp_path / "db.sqlite3"),
            "play",
            str(second),
            str(first),
            "--no-vlc",
        ]
    )
    assert result == 0
    assert observed == {
        "root": root.resolve(),
        "entries": [first.resolve(), second.resolve()],
        "no_vlc": True,
        "autoplay": True,
        "ran": True,
    }


def test_commandless_cli_opens_last_library(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "show"
    root.mkdir()
    db_path = tmp_path / "db.sqlite3"
    database = Database(db_path)
    database.set_root(root)
    database.close()
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, root: Path, database: Database, no_vlc: bool, autoplay: bool) -> None:
            observed.update(root=root, no_vlc=no_vlc, autoplay=autoplay)

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setattr(vlcq.cli, "VLCQApp", FakeApp)
    assert main(["--database", str(db_path)]) == 0
    assert observed == {
        "root": root.resolve(),
        "no_vlc": False,
        "autoplay": False,
        "ran": True,
    }


def test_progress_cli_root_filtered(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    root = tmp_path / "root"
    root.mkdir()
    db_path = tmp_path / "db.sqlite3"
    db = Database(db_path)
    db.set_root(root)
    db.close()
    assert main(["--database", str(db_path), "progress", "--root", str(root), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["version"] == 1


def test_explicit_cli_files_cannot_replace_saved_root(tmp_path: Path) -> None:
    saved_root = tmp_path / "saved"
    outside = tmp_path / "outside" / "movie.mkv"
    saved_root.mkdir()
    outside.parent.mkdir()
    outside.write_bytes(b"video")

    with pytest.raises(PathError, match="outside"):
        _resolve_paths([outside], saved_root)

    inside = saved_root / "episode.mkv"
    inside.write_bytes(b"video")
    root, selected = _resolve_paths([inside], saved_root)
    assert root == saved_root.resolve()
    assert selected == [inside.resolve()]


def test_cli_and_finder_reject_files_outside_saved_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    saved_root = tmp_path / "saved"
    outside = tmp_path / "outside" / "movie.mkv"
    saved_root.mkdir()
    outside.parent.mkdir()
    outside.write_bytes(b"video")
    db_path = tmp_path / "db.sqlite3"
    db = Database(db_path)
    db.set_root(saved_root)
    db.close()

    class UnexpectedApp:
        def __init__(self, **kwargs: object) -> None:
            raise AssertionError(f"rejected input opened the app: {kwargs}")

        def run(self) -> None:
            raise AssertionError("rejected input opened the app")

    monkeypatch.setattr(vlcq.cli, "VLCQApp", UnexpectedApp)
    assert main(["--database", str(db_path), "add", str(outside), "--no-vlc"]) == 2
    assert main(["--database", str(db_path), "finder-handoff", str(outside), "--no-vlc"]) == 2
    assert capsys.readouterr().err.count("outside") == 2

    reopened = Database(db_path)
    assert reopened.get_root() == saved_root.resolve()
    assert reopened.queue_entries() == []
    reopened.close()


@pytest.mark.parametrize("command", ["add", "finder-handoff"])
def test_explicit_files_recover_from_deleted_saved_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    deleted_root = tmp_path / "deleted"
    deleted_root.mkdir()
    db_path = tmp_path / f"{command}.sqlite3"
    database = Database(db_path)
    database.set_root(deleted_root)
    database.close()
    deleted_root.rmdir()
    video = tmp_path / "new" / "movie.mkv"
    video.parent.mkdir()
    video.write_bytes(b"video")
    observed: dict[str, object] = {}

    class FakeApp:
        def __init__(self, *, root: Path, database: Database, **_kwargs: object) -> None:
            observed["root"] = root
            observed["queue"] = [entry.path for entry in database.queue_entries()]

        def run(self) -> None:
            observed["ran"] = True

    monkeypatch.setattr(vlcq.cli, "VLCQApp", FakeApp)

    assert main(["--database", str(db_path), command, str(video), "--no-vlc"]) == 0
    assert observed == {
        "root": video.parent.resolve(),
        "queue": [video.resolve()],
        "ran": True,
    }


@pytest.mark.asyncio
async def test_tui_browse_select_add_and_parent_without_auto_enqueue(tmp_path: Path) -> None:
    root = tmp_path / "show"
    season = root / "season"
    season.mkdir(parents=True)
    (season / "e1.mkv").write_bytes(b"x")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        assert db.queue_entries() == []
        browser = app.query_one("#browser", ListView)
        assert app.query_one("#browser-up", Button)
        assert app.query_one("#browser-sort", Button)
        assert app.query_one("#queue-clear", Button)
        assert app.query_one("#queue-sort", Button)
        browser.focus()
        await pilot.pause()
        assert app.query_one("#browser-pane").has_class("focused")
        browser.index = 0
        await pilot.press("right")
        assert app.browser_path == season.resolve()
        browser.index = 0
        await pilot.press("v")
        await pilot.press("a")
        assert [e.path.name for e in app.queue.entries()] == ["e1.mkv"]
        await pilot.press("left")
        assert app.browser_path == root.resolve()
        await pilot.click("#browser-sort")
        assert app.browser_reverse is True
        await pilot.click("#queue-sort")
        assert app.query_one("#queue-pane").has_class("focused")
        await pilot.click("#queue-play")
        assert app.queue.current() is not None
        await pilot.click("#queue-clear")
        await pilot.press("y")
        assert app.queue.entries() == []
        await app.query_one("#progress", ProgressBar).remove()
        app.refresh_playback()  # timers may race safely with screen teardown
    db.close()


@pytest.mark.asyncio
async def test_tui_lists_scroll_to_show_long_names_and_all_rows(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    long_name = f"{'long-episode-name-' * 6}.mkv"
    paths = [root / long_name, *(root / f"episode-{index:02}.mkv" for index in range(40))]
    for path in paths:
        path.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    app.queue.add(paths)

    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        for selector in ("#browser", "#queue"):
            view = app.query_one(selector, ListView)
            assert view.styles.overflow_x == "auto"
            assert view.styles.overflow_y == "auto"
            assert view.show_horizontal_scrollbar
            assert view.show_vertical_scrollbar
            assert view.max_scroll_x > 0
            assert view.max_scroll_y > 0
            view.scroll_to(x=view.max_scroll_x, animate=False)
            await pilot.pause()
            assert view.scroll_x == view.max_scroll_x

        browser = app.query_one("#browser", ListView)
        rendered_names = [str(row.query_one(Label).renderable) for row in browser.children]
        assert f"[ ] {long_name}" in rendered_names

        left = next(binding for binding in app.BINDINGS if binding.key == "left")
        right = next(binding for binding in app.BINDINGS if binding.key == "right")
        assert left.priority and right.priority
    db.close()


@pytest.mark.asyncio
async def test_tui_root_prompt_and_reorder_keep_keyboard_workflow(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    (first_root / "one.mkv").write_bytes(b"1")
    (second_root / "two.mkv").write_bytes(b"2")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=first_root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        browser = app.query_one("#browser", ListView)
        assert app.focused is browser
        assert browser.index == 0

        await pilot.press("o")
        root_input = app.query_one("#root-input")
        root_input.value = str(second_root)
        await pilot.press("enter")
        await pilot.pause()
        assert app.root == second_root.resolve()
        assert app.browser_entries[0].path == (second_root / "two.mkv").resolve()
        assert browser.index == 0

        paths = []
        for name in ("episode1.mkv", "episode2.mkv", "episode3.mkv"):
            path = second_root / name
            path.write_bytes(name.encode())
            paths.append(path)
        app.queue.add(paths)
        app.refresh_queue()
        await pilot.pause()
        queue = app.query_one("#queue", ListView)
        queue.focus()
        queue.index = 1
        await pilot.press("J")
        await pilot.pause()
        assert [entry.path.name for entry in app.queue.entries()] == [
            "episode1.mkv",
            "episode3.mkv",
            "episode2.mkv",
        ]
        assert queue.index == 2
    db.close()


@pytest.mark.asyncio
async def test_tui_add_actions_use_highlight_and_selection_precedence(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    episode2 = root / "episode2.mkv"
    episode10 = root / "episode10.mkv"
    episode2.write_bytes(b"2")
    episode10.write_bytes(b"10")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        browser = app.query_one("#browser", ListView)
        browser.focus()
        await pilot.pause()

        # Add works directly from the highlighted playable browser item.
        browser.index = 0
        await pilot.press("a")
        assert [entry.path.name for entry in app.queue.entries()] == ["episode2.mkv"]
        assert app.queue.entries()[0].state == "queued"

        # Add-and-play targets the highlighted item rather than queue index zero.
        browser.index = 1
        await pilot.press("A")
        assert app.queue.current() is not None
        assert app.queue.current().path == episode10.resolve()

        # An explicit multi-selection takes precedence and is naturally ordered.
        browser.index = 1
        await pilot.press("v")
        browser.index = 0
        await pilot.press("v")
        browser.index = 1
        await pilot.press("A")
        assert app.queue.current() is not None
        assert app.queue.current().path == episode2.resolve()
        assert app.selected_paths == set()
    db.close()


@pytest.mark.asyncio
async def test_tui_shortcuts_only_mutate_the_focused_pane(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    paths = [root / "episode1.mkv", root / "episode2.mkv"]
    for path in paths:
        path.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.queue.add(paths)
        app.refresh_queue()
        await pilot.pause()
        browser = app.query_one("#browser", ListView)
        queue = app.query_one("#queue", ListView)
        queue.index = 0
        before = [entry.path for entry in app.queue.entries()]

        # Browser shortcuts must not remove, reorder, or retry a queue row.
        browser.focus()
        await pilot.pause()
        await pilot.press("d", "J", "K", "r")
        await pilot.pause()
        assert app.selected_paths == set()
        assert [entry.path for entry in app.queue.entries()] == before

        # Queue shortcuts must not toggle browser selection or navigate folders.
        subfolder = root / "subfolder"
        subfolder.mkdir()
        app.browser_path = subfolder.resolve()
        queue.focus()
        await pilot.pause()
        browser.index = 0
        await pilot.press("v", "backspace")
        assert app.selected_paths == set()
        assert app.browser_path == subfolder.resolve()
    db.close()


@pytest.mark.asyncio
async def test_tui_direct_enter_consumes_only_activated_selection(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    first = root / "episode1.mkv"
    second = root / "episode2.mkv"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        browser = app.query_one("#browser", ListView)
        browser.focus()
        await pilot.pause()
        browser.index = 1
        await pilot.press("v")
        assert app.selected_paths == {second.resolve()}
        await pilot.press("enter")
        assert app.queue.current() is not None
        assert app.queue.current().path == second.resolve()
        assert second.resolve() not in app.selected_paths
        await pilot.pause()
        selected_label = str(browser.children[1].query_one(Label).renderable)
        assert selected_label.startswith("[ ]")
    db.close()


@pytest.mark.asyncio
async def test_play_when_client_missing_shows_retry_status(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        app.queue.add([video])
        app.refresh_queue()
        await pilot.pause()
        queue = app.query_one("#queue", ListView)
        queue.focus()
        queue.index = 0
        app.no_vlc = False

        await app.action_activate()

        rendered = str(app.query_one("#status", Static).renderable)
        assert "VLC unavailable — press r to retry" in rendered
        assert "VLC is not connected" not in rendered
        app.no_vlc = True
    db.close()


@pytest.mark.asyncio
async def test_periodic_queue_refresh_reuses_and_updates_existing_rows(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        app.queue.add([video])
        app.refresh_queue()
        await pilot.pause()
        view = app.query_one("#queue", ListView)
        original_row = view.children[0]

        app.refresh_queue()
        await pilot.pause()
        assert view.children[0] is original_row

        entry = app.queue.entries()[0]
        db.set_state(entry.id, "playing")
        app.refresh_queue()
        await pilot.pause()
        assert view.children[0] is original_row
        assert "PLAYING" in str(original_row.query_one(Label).renderable)
    db.close()


@pytest.mark.asyncio
async def test_tui_retry_reconnects_when_vlc_is_disconnected(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        app.queue.add([video])
        app.refresh_queue()
        await pilot.pause()
        queue = app.query_one("#queue", ListView)
        queue.focus()
        await pilot.pause()
        queue.index = 0
        app.no_vlc = False
        started: list[bool] = []
        played: list[int] = []

        async def reconnect() -> None:
            started.append(True)
            app.controller.client = object()  # type: ignore[assignment]

        async def play(index: int) -> None:
            played.append(index)

        monkeypatch.setattr(app.controller, "start", reconnect)
        monkeypatch.setattr(app.controller, "play_index", play)
        await app.action_retry()
        assert started == [True]
        assert played == [0]

        await pilot.pause()
        queue.index = 0
        app.controller.client = None

        async def failed_reconnect() -> None:
            raise VLCError("VLC startup failed")

        monkeypatch.setattr(app.controller, "start", failed_reconnect)
        await app.action_retry()
        assert "Retry failed" in str(app.query_one("#status", Static).renderable)
        app.no_vlc = True
    db.close()


@pytest.mark.asyncio
async def test_tui_empty_feedback_and_queue_state_indicators(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#browser-empty", Static).display
        assert app.query_one("#queue-empty", Static).display
        assert app.query_one("#queue-add", Button).disabled
        await pilot.press("a")
        assert "Nothing to add" in str(app.query_one("#status", Static).renderable)

        paths = []
        for index in range(8):
            path = root / f"episode{index}.mkv"
            path.write_bytes(str(index).encode())
            paths.append(path)
        app.queue.add(paths)
        entries = app.queue.entries()
        states = [
            "queued",
            "playing",
            "paused",
            "stopped",
            "skipped",
            "completed",
            "missing",
            "failed",
        ]
        for entry, state in zip(entries, states, strict=True):
            db.set_state(entry.id, state)
        db.set_current(entries[1].id)
        app.refresh_queue()
        await pilot.pause()
        labels = [
            str(item.query_one(Label).renderable)
            for item in app.query_one("#queue", ListView).children
        ]
        for state in (
            "QUEUED",
            "PLAYING",
            "PAUSED",
            "STOPPED",
            "SKIPPED",
            "COMPLETED",
            "MISSING",
            "FAILED",
        ):
            assert state in "\n".join(labels)
        assert not app.query_one("#queue-empty", Static).display
    db.close()
