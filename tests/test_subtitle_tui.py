from __future__ import annotations

from pathlib import Path

import pytest
from textual import events
from textual.widgets import Button, Label, Static

from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.subtitles import (
    SubtitleCandidate,
    SubtitleChoice,
    SubtitleSnapshot,
    SubtitleTarget,
    SubtitleTrack,
)
from vlcq.tui import (
    ActionMenu,
    QueueTarget,
    SubtitlePicker,
    SubtitleSubitem,
    VLCQApp,
)


def make_video(root: Path, name: str = "episode.mkv") -> Path:
    root.mkdir(parents=True, exist_ok=True)
    path = root / name
    path.write_bytes(b"video")
    return path


@pytest.mark.asyncio
async def test_subtitle_picker_selects_and_dismisses_at_compact_terminal_size(tmp_path: Path) -> None:
    root = tmp_path / "library"
    video = make_video(root)
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    target = SubtitleTarget(1, 1, video, "vlc-1")
    snapshot = SubtitleSnapshot(
        target,
        (
            SubtitleTrack("1", "en", "English Full Dialogue", True, False, False, False, None, 0),
            SubtitleTrack("2", "en", "English Signs Songs", False, True, False, False, None, 1),
        ),
    )
    result: list[object] = []
    async with app.run_test(size=(80, 24)) as pilot:
        app.push_screen(SubtitlePicker(snapshot), result.append)
        await pilot.pause()
        assert isinstance(app.screen, SubtitlePicker)
        assert app.query_one("#subtitle-picker-list").region.height <= 12
        assert any("Off" in str(button.label) for button in app.screen.query(Button))
        assert "VLC current/default" in str(
            app.screen.query_one("#subtitle-active-unknown", Static).render()
        )
        assert not any(str(button.label).startswith("●") for button in app.screen.query(Button))
        await pilot.click("#subtitle-choice-1")
        await pilot.pause()
        assert result and isinstance(result[0], SubtitleChoice)
        assert result[0].track is not None and result[0].track.track_id == "1"
        assert app.screen.__class__.__name__ == "Screen"

        app.push_screen(SubtitlePicker(snapshot, result[0]), result.append)
        await pilot.pause()
        assert not app.screen.query("#subtitle-active-unknown")
        assert str(app.screen.query_one("#subtitle-choice-1", Button).label).startswith("●")
        await pilot.press("escape")

        app.push_screen(SubtitlePicker(snapshot, SubtitleChoice.off()), result.append)
        await pilot.pause()
        assert not app.screen.query("#subtitle-active-unknown")
        assert str(app.screen.query_one("#subtitle-choice-0", Button).label).startswith("● Off")
        await pilot.press("escape")
        assert result[-2:] == [None, None]
    db.close()


@pytest.mark.asyncio
async def test_subtitle_subitem_is_visible_and_video_menu_no_longer_owns_subtitles(
    tmp_path: Path,
) -> None:
    root = tmp_path / "library"
    current = make_video(root, "current.mkv")
    inactive = make_video(root, "inactive.mkv")
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    app.queue.add([current, inactive])
    entry = app.queue.play_now(0)
    app.controller.status = VLCStatus("playing", path=current.resolve(), playlist_id="vlc-1")
    app.controller.client = object()  # type: ignore[assignment]
    app.controller._subtitle_choice = SubtitleChoice.off()
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        rows = list(app.query_one("#queue").children)
        assert len(rows) == 2
        current_subtitle = rows[0].query_one(SubtitleSubitem)
        inactive_subtitle = rows[1].query_one(SubtitleSubitem)
        assert "● Off" in str(current_subtitle.renderable)
        assert str(inactive_subtitle.renderable)
        current_actions = app._context_actions_for_queue(QueueTarget(entry.id, app._root_generation))
        inactive_entry = app.queue.entries()[1]
        inactive_actions = app._context_actions_for_queue(
            QueueTarget(inactive_entry.id, app._root_generation)
        )
        assert not any(action.key == "subtitle-queue" for action in current_actions)
        assert not any(action.key == "subtitle-queue" for action in inactive_actions)
    db.close()


@pytest.mark.asyncio
async def test_inactive_subtitle_subitem_opens_persistent_picker_without_vlc(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    video = root / "Example Show" / "Season 1" / "S01E01.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    app.queue.add([video])

    async def discover(path: Path, *, root_generation: int = 0, target=None) -> SubtitleSnapshot:
        del target
        subtitle_target = SubtitleTarget(0, 1, path.resolve(), None, root_generation)
        return SubtitleSnapshot(
            subtitle_target,
            (),
            planned_choice=SubtitleChoice.off(),
        )

    monkeypatch.setattr(app.controller, "discover_subtitles_for_path", discover)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        subtitle = app.query_one("#queue").children[0].query_one(SubtitleSubitem)
        assert str(subtitle.renderable)
        assert app.controller.client is None
        app.on_click(
            events.Click(
                subtitle,
                1,
                0,
                0,
                0,
                3,
                False,
                False,
                False,
                subtitle.region.x + 1,
                subtitle.region.y,
            )
        )
        await pilot.pause()
        assert isinstance(app.screen, SubtitlePicker)
        assert "★ Off" in str(subtitle.renderable)
        await pilot.press("escape")
        subtitle.focus()
        await pilot.press("shift+f10")
        await pilot.pause()
        assert isinstance(app.screen, SubtitlePicker)
    db.close()


@pytest.mark.asyncio
async def test_visible_subtitle_subitem_resolves_mkv_english_choice_in_background(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    video = make_video(root, "episode.mkv")
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)

    async def discover(path: Path, root_path: Path):
        assert path == video.resolve()
        assert root_path == root.resolve()
        return (
            SubtitleCandidate(
                "embedded",
                "en",
                "English Full Dialogue",
                full_dialogue=True,
                source_order=0,
            ),
        )

    monkeypatch.setattr(app.subtitle_discovery, "discover", discover)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause(0.1)
        subtitle = app.query_one("#browser").children[0].query_one(SubtitleSubitem)
        assert "★ English Full Dialogue" in str(subtitle.renderable)
        title = app.query_one("#browser").children[0].query_one(".row-label", Label)
        assert subtitle.region.y == title.region.y
        assert subtitle.region.x >= title.region.right
    db.close()


@pytest.mark.asyncio
async def test_preference_actions_are_reachable_with_empty_queue_and_persist(tmp_path: Path) -> None:
    root = tmp_path / "library"
    root.mkdir()
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=True)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.click("#queue-actions")
        assert isinstance(app.screen, ActionMenu)
        labels = {str(button.label) for button in app.screen.query(Button)}
        assert "☐ Remember subtitles by show" in labels
        assert "☑ Prefer English subtitles" in labels
        remember = next(button for button in app.screen.query(Button) if "Remember subtitles" in str(button.label))
        await pilot.click(remember)
        await pilot.pause()
        assert db.remember_subtitles_by_show() is True
    db.close()
    reopened = Database(tmp_path / "state.sqlite3")
    restarted = VLCQApp(root=root, database=reopened, no_vlc=True)
    assert restarted.database.remember_subtitles_by_show() is True
    assert restarted.database.prefer_english_subtitles() is True
    reopened.close()
