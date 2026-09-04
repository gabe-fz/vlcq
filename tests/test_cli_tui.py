from __future__ import annotations

import json
from pathlib import Path

import pytest
from textual.widgets import Button, ListView, ProgressBar

import vlcq.cli
from vlcq.cli import main
from vlcq.database import Database
from vlcq.tui import VLCQApp


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
        await pilot.click("#queue-clear")
        await pilot.press("y")
        assert app.queue.entries() == []
        await app.query_one("#progress", ProgressBar).remove()
        app.refresh_playback()  # timers may race safely with screen teardown
    db.close()
