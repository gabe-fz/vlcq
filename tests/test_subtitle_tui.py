from __future__ import annotations

from pathlib import Path

import pytest
from textual.widgets import Button

from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.subtitles import SubtitleChoice, SubtitleSnapshot, SubtitleTarget, SubtitleTrack
from vlcq.tui import (
    ActionMenu,
    QueueTarget,
    SubtitlePicker,
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
        await pilot.click("#subtitle-choice-1")
        await pilot.pause()
        assert result and isinstance(result[0], SubtitleChoice)
        assert result[0].track is not None and result[0].track.track_id == "1"
        assert app.screen.__class__.__name__ == "Screen"
        app.push_screen(SubtitlePicker(snapshot), result.append)
        await pilot.pause()
        await pilot.press("escape")
        assert result[-1] is None
    db.close()


@pytest.mark.asyncio
async def test_subtitles_only_appears_for_matching_current_row_and_menu_is_read_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "library"
    current = make_video(root, "current.mkv")
    inactive = make_video(root, "inactive.mkv")
    db = Database(tmp_path / "state.sqlite3")
    app = VLCQApp(root=root, database=db, no_vlc=False)
    app.queue.add([current, inactive])
    entry = app.queue.play_now(0)
    app.controller.status = VLCStatus("playing", path=current.resolve(), playlist_id="vlc-1")
    app.controller.client = object()  # type: ignore[assignment]
    target = SubtitleTarget(app.controller.playback_generation, entry.id, current.resolve(), "vlc-1")
    calls: list[str] = []

    def target_for_path(path: Path) -> SubtitleTarget | None:
        calls.append(str(path))
        return target if path.resolve() == current.resolve() else None

    monkeypatch.setattr(app.controller, "current_subtitle_target_for_path", target_for_path)
    async with app.run_test(size=(80, 24)) as pilot:
        await pilot.pause()
        current_actions = app._context_actions_for_queue(QueueTarget(entry.id, app._root_generation))
        inactive_entry = app.queue.entries()[1]
        inactive_actions = app._context_actions_for_queue(
            QueueTarget(inactive_entry.id, app._root_generation)
        )
        assert any(action.key == "subtitle-queue" for action in current_actions)
        assert not any(action.key == "subtitle-queue" for action in inactive_actions)
        assert calls == [str(current.resolve()), str(inactive.resolve())]
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
