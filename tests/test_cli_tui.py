from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Label, ListView, Static

import vlcq.cli
import vlcq.tui as tui_module
from vlcq.cli import _resolve_paths, main
from vlcq.database import Database
from vlcq.paths import PathError
from vlcq.queue import QueueService
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


def test_cli_reports_migration_recovery_and_preserves_source(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"video")
    db_path = tmp_path / "db.sqlite3"
    connection = sqlite3.connect(db_path)
    connection.executescript(
        """
        CREATE TABLE settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE queues(
            id INTEGER PRIMARY KEY, root TEXT NOT NULL,
            created_at TEXT NOT NULL, updated_at TEXT NOT NULL
        );
        CREATE TABLE media(
            id INTEGER PRIMARY KEY, path TEXT NOT NULL,
            device INTEGER NOT NULL, inode INTEGER NOT NULL, size INTEGER NOT NULL,
            mtime_ns INTEGER NOT NULL, position_ms INTEGER NOT NULL DEFAULT 0,
            duration_ms INTEGER NOT NULL DEFAULT 0,
            completion_observed INTEGER NOT NULL DEFAULT 0,
            first_observed TEXT NOT NULL, last_observed TEXT NOT NULL,
            UNIQUE(path, device, inode, size, mtime_ns)
        );
        CREATE TABLE queue_entries(
            id INTEGER PRIMARY KEY, queue_id INTEGER NOT NULL REFERENCES queues(id),
            position INTEGER NOT NULL, media_id INTEGER NOT NULL REFERENCES media(id),
            state TEXT NOT NULL DEFAULT 'queued', UNIQUE(queue_id, position)
        );
        """
    )
    connection.execute(
        "INSERT INTO settings(key,value) VALUES('active_queue','1')"
    )
    connection.execute(
        "INSERT INTO queues(id,root,created_at,updated_at) VALUES(1,?,?,?)",
        (str(root), "created", "updated"),
    )
    stat = video.stat()
    connection.execute(
        "INSERT INTO media(id,path,device,inode,size,mtime_ns,first_observed,last_observed) "
        "VALUES(1,?,?,?,?,?,?,?)",
        (str(video), stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, "first", "last"),
    )
    connection.execute(
        "INSERT INTO queue_entries(id,queue_id,position,media_id,state) VALUES(1,1,0,1,'queued')"
    )
    connection.execute("PRAGMA user_version=1")
    connection.commit()
    connection.close()

    def injected_failure(
        database: Database, statements: tuple[str, ...], version: int
    ) -> None:
        del version
        database.connection.execute("BEGIN IMMEDIATE")
        database.connection.execute(statements[0])
        raise sqlite3.OperationalError("injected migration failure")

    monkeypatch.setattr(Database, "_transactional_schema_change", injected_failure)
    assert main(
        ["--database", str(db_path), "progress", "--root", str(root), "--json"]
    ) == 2
    error = capsys.readouterr().err
    assert "database migration failed" in error
    assert "original database was preserved" in error
    assert "private SQLite backup" in error

    preserved = sqlite3.connect(db_path)
    assert preserved.execute("PRAGMA user_version").fetchone() == (1,)
    columns = {row[1] for row in preserved.execute("PRAGMA table_info(media)")}
    assert "resume_position_ms" not in columns
    assert preserved.execute("SELECT root FROM queues WHERE id=1").fetchone() == (str(root),)
    assert preserved.execute("SELECT COUNT(*) FROM queue_entries").fetchone() == (1,)
    preserved.close()


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
        assert app.query_one("#files-actions", Button)
        assert app.query_one("#queue-actions", Button)
        browser.focus()
        await pilot.pause()
        assert app.query_one("#files-section").has_class("focused")
        browser.index = 0
        await pilot.press("right")
        assert app.browser_path == season.resolve()
        browser.index = 0
        await pilot.press("v")
        await pilot.press("a")
        assert [e.path.name for e in app.queue.entries()] == ["e1.mkv"]
        await pilot.press("left")
        assert app.browser_path == root.resolve()
        await pilot.click("#files-sort")
        assert app.browser_reverse is True
        await pilot.click("#queue-actions")
        await pilot.pause()
        clear = next(button for button in app.screen.query(Button) if str(button.label) == "Clear queue")
        await pilot.click(clear)
        await pilot.press("y")
        assert app.queue.entries() == []
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
            assert view.show_vertical_scrollbar
            assert view.max_scroll_y > 0
            assert len(view.displayed_children) >= 5
            assert all(row.region.height == 1 for row in view.displayed_children[:5])

        browser = app.query_one("#browser", ListView)
        rendered_names = [str(row.query_one(Label).renderable) for row in browser.children]
        assert any(long_name in rendered_name for rendered_name in rendered_names)
        assert browser.children[0].query_one(".browser-check", Button).region.width <= 3

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
        assert str(browser.children[1].query_one(".browser-check", Button).label) == "☐"
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

        rendered = str(app.query_one("#notice", Static).renderable)
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
        db.set_current(entry.id)
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
        assert "Retry failed" in str(app.query_one("#notice", Static).renderable)
        app.no_vlc = True
    db.close()


@pytest.mark.asyncio
async def test_tui_search_filters_without_queueing_or_shortcut_leakage(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    (root / "alpha1.mkv").write_bytes(b"one")
    (root / "episode2.mkv").write_bytes(b"two")
    (root / "completed.mkv").write_bytes(b"done")
    db = Database(tmp_path / "db.sqlite3")
    db.merge_progress(root / "alpha1.mkv", 1_000, 10_000)
    db.merge_progress(root / "completed.mkv", 10_000, 10_000, completed=True)
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        app.action_search_filter()
        await pilot.pause()
        search = app.query_one("#search-input", Input)
        search.focus()
        await pilot.press("a", "1")
        await pilot.click("#search-apply")
        await pilot.pause()
        assert app.search_query == "a1"
        assert [entry.name for entry in app.browser_entries] == ["alpha1.mkv"]
        assert app.queue.entries() == []

        app.action_search_filter()
        await pilot.pause()
        await pilot.click("#search-clear")
        await pilot.pause()
        app.action_search_filter()
        await pilot.pause()
        await pilot.click("#search-filter-progress")
        await pilot.click("#search-apply")
        await pilot.pause()
        assert [entry.name for entry in app.browser_entries] == ["alpha1.mkv"]
        app.action_search_filter()
        await pilot.pause()
        await pilot.click("#search-filter-not-completed")
        await pilot.click("#search-apply")
        await pilot.pause()
        assert {entry.name for entry in app.browser_entries} == {"alpha1.mkv", "episode2.mkv"}
    db.close()


@pytest.mark.asyncio
async def test_tui_video_and_queue_row_clicks_only_highlight(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    first = root / "episode1.mkv"
    second = root / "episode2.mkv"
    first.write_bytes(b"one")
    second.write_bytes(b"two")
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    app.queue.open(root)
    app.queue.add([first, second])
    app.queue.play_now(0)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        browser = app.query_one("#browser", ListView)
        queue = app.query_one("#queue", ListView)
        await pilot.click(browser.children[1])
        await pilot.pause()
        assert browser.index == 1
        assert app.queue.current() is not None
        assert app.queue.current().path == first.resolve()
        await pilot.click(queue.children[1])
        await pilot.pause()
        assert queue.index == 1
        assert app.queue.current() is not None
        assert app.queue.current().path == first.resolve()
    db.close()


@pytest.mark.asyncio
async def test_tui_queue_highlight_persists_by_entry_identity_and_clears_on_removal(
    tmp_path: Path,
) -> None:
    root = tmp_path / "show"
    root.mkdir()
    paths = [root / "first.mkv", root / "second.mkv"]
    for path in paths:
        path.write_bytes(path.name.encode())
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    app.queue.add(paths)

    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = app.query_one("#queue", ListView)
        entries = app.queue.entries()
        await pilot.click(view.children[1])
        await pilot.pause()
        assert db.get_selected_id() == entries[1].id

        app.queue.move(1, -1)
        app.refresh_queue()
        await pilot.pause()
        assert db.get_selected_id() == entries[1].id
        assert view.index == 0

    # Reopen the database as a new process would. The persisted selection must
    # remain visibly identifiable even though startup puts focus in Files.
    db.close()
    reopened = Database(tmp_path / "db.sqlite3")
    restarted = VLCQApp(root=root, database=reopened, no_vlc=True)
    async with restarted.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = restarted.query_one("#queue", ListView)
        selected_row = view.children[0]
        assert view.index == 0
        assert reopened.get_selected_id() == restarted.queue.entries()[0].id
        assert selected_row.has_class("queue-selected")
        assert str(selected_row.query_one(Label).renderable).startswith("›")
        restarted.queue.remove(0)
        restarted.refresh_queue()
        await pilot.pause()
        assert reopened.get_selected_id() is None
        assert view.index is None
    reopened.close()


@pytest.mark.asyncio
async def test_tui_restart_does_not_retarget_stale_cross_queue_selection(tmp_path: Path) -> None:
    first_root = tmp_path / "first"
    second_root = tmp_path / "second"
    first_root.mkdir()
    second_root.mkdir()
    first = first_root / "first.mkv"
    second = second_root / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")

    db_path = tmp_path / "db.sqlite3"
    db = Database(db_path)
    queue = QueueService(db)
    queue.open(first_root)
    queue.add([first])
    stale_entry_id = queue.entries()[0].id
    queue.open(second_root)
    queue.add([second])
    db.connection.execute(
        "INSERT INTO settings(key,value) VALUES('selected_entry',?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(stale_entry_id),),
    )
    db.close()

    reopened = Database(db_path)
    app = VLCQApp(root=second_root, database=reopened, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = app.query_one("#queue", ListView)
        assert view.index is None
        assert reopened.get_selected_id() is None
        assert app.queue.current() is None
    reopened.close()


@pytest.mark.asyncio
async def test_tui_resume_startup_uses_persisted_current_identity_offline(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    first = root / "first.mkv"
    second = root / "second.mkv"
    first.write_bytes(b"first")
    second.write_bytes(b"second")
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add([first, second])
    current = queue.play_now(1)
    db.set_state(current.id, "stopped")
    db.merge_progress(second, 1_000, 10_000)
    app = VLCQApp(root=root, database=db, no_vlc=True)
    app.resume_command = True
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await pilot.click("#resume-choice")
        await pilot.pause()
        assert app.queue.current() is not None
        assert app.queue.current().path == second.resolve()
    db.close()


@pytest.mark.asyncio
async def test_tui_history_refresh_is_async_and_reuses_rows(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "show"
    root.mkdir()
    paths = [root / "episode1.mkv", root / "episode2.mkv"]
    for path in paths:
        path.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    db.merge_progress(paths[0], 1_000, 10_000)
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        view = app.query_one("#browser", ListView)
        original_rows = list(view.children)
        original_index = view.index
        original_scroll = view.scroll_y
        original_identity = tui_module._file_identity

        def delayed_identity(path: Path, library_root: Path):
            time.sleep(0.05)
            return original_identity(path, library_root)

        monkeypatch.setattr(tui_module, "_file_identity", delayed_identity)
        app._refresh_browser_history()
        view.index = 1
        await pilot.pause()
        assert view.index == 1
        await pilot.pause(0.1)
        assert list(view.children) == original_rows
        assert view.index == 1
        assert view.scroll_y >= original_scroll
        assert original_index == 0
    db.close()


@pytest.mark.asyncio
async def test_tui_direct_resume_and_start_over_target_highlighted_item(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    video = root / "episode.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    db.merge_progress(video, 2_000, 10_000)
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        await pilot.pause()
        await app._open_details()
        await pilot.pause()
        assert not app.query_one("#details-resume", Button).disabled
        await pilot.click("#details-resume")
        await pilot.pause()
        assert app.queue.current() is not None
        assert app.queue.current().path == video.resolve()
        app.queue.database.set_state(app.queue.current().id, "paused")
        await app._open_details()
        await pilot.pause()
        await pilot.click("#details-start-over")
        await pilot.pause()
        assert app.queue.current() is not None
        assert app.queue.current().state == "playing"
    db.close()


@pytest.mark.asyncio
async def test_tui_empty_feedback_and_queue_state_indicators(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    db = Database(tmp_path / "db.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(120, 40)) as pilot:
        assert app.query_one("#files-empty", Static).display
        assert app.query_one("#queue-empty", Static).display
        await pilot.press("a")
        assert "Nothing to add" in str(app.query_one("#notice", Static).renderable)

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
        rendered = "\n".join(labels)
        assert "QUEUED" not in rendered
        assert "PLAYING" in rendered
        assert "PAUSED" not in rendered
        assert "STOPPED" not in rendered
        for state in ("SKIPPED", "COMPLETED", "MISSING", "FAILED"):
            assert state in rendered
        assert not app.query_one("#queue-empty", Static).display
    db.close()
