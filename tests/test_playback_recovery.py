from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService
from vlcq.vlc import VLCError, VLCPlaylistItem


class RecoveryClient:
    def __init__(self) -> None:
        self.alive = True
        self.failures = 0
        self.attempts = 0
        self.items: list[VLCPlaylistItem] = []
        self.current = VLCStatus("stopped")
        self.commands: list[tuple[str, dict[str, str | int]]] = []

    async def status(self) -> VLCStatus:
        if not self.alive:
            raise VLCError("user quit VLC")
        return self.current

    async def replace_playlist(self, path: Path) -> VLCStatus:
        self.attempts += 1
        await self.status()
        if self.failures:
            self.failures -= 1
            raise VLCError("transient play failure")
        self.items = [VLCPlaylistItem(str(self.attempts), path)]
        self.current = VLCStatus("playing", 0, 60_000, path, self.items[0].id)
        return self.current

    async def playlist(self) -> list[VLCPlaylistItem]:
        await self.status()
        return list(self.items)

    async def enqueue(self, path: Path) -> VLCStatus:
        self.items.append(VLCPlaylistItem("next", path))
        return self.current

    async def remove(self, playlist_id: str) -> VLCStatus:
        self.items = [item for item in self.items if item.id != playlist_id]
        return self.current

    async def command(self, command: str, **parameters: str | int) -> VLCStatus:
        await self.status()
        self.commands.append((command, parameters))
        return self.current

    async def close(self) -> None:
        pass


class RecoveryProcess:
    def __init__(self) -> None:
        self.client: RecoveryClient | None = None
        self.clients: list[RecoveryClient] = []
        self.starts = 0
        self.stops = 0
        self.play_failures = 0
        self.start_failures = 0

    async def start(self) -> RecoveryClient:
        self.starts += 1
        if self.start_failures:
            self.start_failures -= 1
            raise VLCError("startup failure")
        if self.client is None or not self.client.alive:
            self.client = RecoveryClient()
            self.client.failures = self.play_failures
            self.clients.append(self.client)
        return self.client

    async def stop(self) -> None:
        self.stops += 1
        if self.client is not None:
            self.client.alive = False
            self.client = None


@pytest.fixture
def recovery(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    videos = [tmp_path / "one.mkv", tmp_path / "two.mkv"]
    for video in videos:
        video.touch()
    db = Database(tmp_path / "queue.sqlite3")
    queue = QueueService(db)
    queue.open(tmp_path)
    queue.add(videos)
    process = RecoveryProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    # Most tests drive observations explicitly, without a background poll.
    monkeypatch.setattr(controller, "_poll", AsyncMock())
    yield controller, process, queue, videos
    db.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("poll_detected", [False, True])
@pytest.mark.parametrize("target", [0, 1])
async def test_play_after_manual_quit(recovery, poll_detected: bool, target: int) -> None:
    controller, process, queue, videos = recovery
    await controller.play_with_policy(0)
    old = process.client
    old.alive = False
    if poll_detected:
        await PlaybackController._poll(controller)
        assert controller.client is None
    assert len(process.clients) == 1  # polling must not respawn a manually quit player
    await controller.play_with_policy(target)
    assert len(process.clients) == 2
    assert process.client is not old
    assert controller.status.path == videos[target]
    assert controller.status.state == "playing"
    assert queue.current().path == videos[target]
    assert controller.last_error is None
    await controller.stop()


@pytest.mark.asyncio
async def test_transient_play_failure_reuses_healthy_process(recovery) -> None:
    controller, process, queue, videos = recovery
    process.play_failures = 1
    await controller.play_index(0)
    assert process.client.attempts == 2
    assert len(process.clients) == 1
    assert process.stops == 0
    assert [item.path for item in process.client.items] == videos
    assert queue.current().state == "playing"
    assert controller.last_error is None
    await controller.stop()


@pytest.mark.asyncio
async def test_persistent_failure_has_bounded_retry_and_single_replacement(recovery) -> None:
    controller, process, queue, _ = recovery
    process.play_failures = 99
    with pytest.raises(VLCError, match="transient play failure"):
        await controller.play_index(0)
    assert sum(client.attempts for client in process.clients) == 3
    assert len(process.clients) == 2
    assert process.stops == 1
    assert not process.clients[0].alive
    assert queue.current().state == "failed"
    assert "3 attempts" in controller.last_error
    assert controller.active_vlc_id is None
    assert controller.staged_vlc_id is None
    await controller.stop()


@pytest.mark.asyncio
async def test_startup_failure_is_retried(recovery) -> None:
    controller, process, _, _ = recovery
    process.start_failures = 1
    await controller.play_index(0)
    assert process.starts == 2
    assert controller.status.state == "playing"
    await controller.stop()


@pytest.mark.asyncio
async def test_exhausted_startup_reports_original_error_without_current_entry(recovery) -> None:
    controller, process, queue, _ = recovery
    process.start_failures = 99
    with pytest.raises(VLCError, match="startup failure"):
        await controller.play_index(0)
    assert process.starts == 3
    assert queue.current() is None
    assert queue.entries()[0].state == "failed"
    assert "3 attempts" in controller.last_error


@pytest.mark.asyncio
async def test_retry_preserves_resume_and_successor(recovery) -> None:
    controller, process, queue, videos = recovery
    queue.update_progress(videos[0], 12_000, 60_000)
    process.play_failures = 1
    await controller.play_with_policy(0, choice="resume")
    assert ("seek", {"val": "12", "type": "absolute"}) in process.client.commands
    assert controller.status.position_ms == 12_000
    assert [item.path for item in process.client.items] == videos
    assert queue.database.history_for(videos[0], root=queue.root).coverage_ms is None
    await controller.stop()


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["next", "previous", "toggle_pause"])
async def test_transport_play_actions_recover(recovery, action: str) -> None:
    controller, process, _, _ = recovery
    await controller.play_index(0)
    process.client.alive = False
    await getattr(controller, action)()
    assert len(process.clients) == 2
    assert controller.status.state == "playing"
    await controller.stop()


@pytest.mark.asyncio
async def test_cancel_and_missing_file_do_not_launch(recovery) -> None:
    controller, process, _, videos = recovery
    assert not await controller.play_with_policy(0, choice="cancel")
    videos[0].unlink()
    with pytest.raises(FileNotFoundError):
        await controller.play_index(0)
    assert process.starts == 0


@pytest.mark.asyncio
async def test_cancelled_play_does_not_retry(recovery, monkeypatch: pytest.MonkeyPatch) -> None:
    controller, process, _, _ = recovery
    monkeypatch.setattr(process, "start", AsyncMock(side_effect=asyncio.CancelledError))
    with pytest.raises(asyncio.CancelledError):
        await controller.play_index(0)
    assert process.start.await_count == 1


@pytest.mark.asyncio
async def test_reconnect_is_idle_and_concurrent_play_reuses_instance(recovery) -> None:
    controller, process, _, _ = recovery
    await controller.play_index(0)
    process.client.alive = False
    await controller.reconnect()
    assert process.client.current.state == "stopped"
    assert process.client.attempts == 0
    await asyncio.gather(controller.play_index(0), controller.play_index(0))
    assert process.client.attempts == 1
    assert len(process.clients) == 2
    await controller.stop()


@pytest.mark.asyncio
async def test_matching_but_stopped_media_does_not_count_as_playback(recovery) -> None:
    controller, process, _, videos = recovery
    await controller.start()
    process.client.current = VLCStatus("stopped", path=videos[0])
    controller.readiness_timeout = 0.01
    with pytest.raises(VLCError, match="did not become ready"):
        await controller._wait_for_expected_media(
            process.client.current, videos[0], controller.playback_generation
        )
    await controller.stop()
