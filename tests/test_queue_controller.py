from __future__ import annotations

from pathlib import Path

import pytest

from vlcq.controller import PlaybackController, ResumeChoiceRequired
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService
from vlcq.vlc import VLCError


class FakeClient:
    def __init__(self) -> None:
        self.commands: list[tuple[str, dict[str, str | int]]] = []
        self.played: list[Path] = []

    async def play(self, path: Path) -> VLCStatus:
        self.played.append(path)
        return VLCStatus("playing", path=path)

    async def command(self, command: str, **parameters: str | int) -> VLCStatus:
        self.commands.append((command, parameters))
        return VLCStatus("paused")

    async def close(self) -> None:
        pass


class FakeProcess:
    def __init__(self) -> None:
        self.client = FakeClient()
        self.stopped = False

    async def start(self) -> FakeClient:
        return self.client

    async def stop(self) -> None:
        self.stopped = True


def setup_queue(tmp_path: Path) -> tuple[Database, QueueService, list[Path]]:
    root = tmp_path / "show"
    root.mkdir()
    videos = [root / "e1.mkv", root / "e2.mkv"]
    for video in videos:
        video.write_bytes(video.name.encode())
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    return db, queue, videos


@pytest.mark.asyncio
async def test_controller_state_observation_updates_visible_queue_state(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.client = process.client
    await controller.play_index(0)
    assert queue.entries()[0].state == "playing"
    await controller.toggle_pause()
    assert queue.entries()[0].state == "paused"
    await controller._observe(VLCStatus("playing", path=videos[0].resolve()))
    assert queue.entries()[0].state == "playing"
    await controller._observe(VLCStatus("stopped", path=videos[0].resolve()))
    assert queue.entries()[0].state == "stopped"
    db.close()


@pytest.mark.asyncio
async def test_manual_transitions_reset_near_end_completion_evidence(tmp_path: Path) -> None:
    db, queue, _videos = setup_queue(tmp_path)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.client = process.client

    controller._near_end_seen = True
    await controller.seek(-10)
    assert not controller._near_end_seen

    controller._near_end_seen = True
    await controller.next()
    assert not controller._near_end_seen

    controller._near_end_seen = True
    await controller.previous()
    assert not controller._near_end_seen
    db.close()


@pytest.mark.asyncio
async def test_poll_failure_retires_stale_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db, queue, _videos = setup_queue(tmp_path)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]

    class FailedClient(FakeClient):
        async def status(self) -> VLCStatus:
            raise VLCError("connection dropped")

    client = FailedClient()
    controller.client = client  # type: ignore[assignment]
    controller._running = True

    async def stop_after_failure(_delay: float) -> None:
        controller._running = False

    monkeypatch.setattr("vlcq.controller.asyncio.sleep", stop_after_failure)
    await controller._poll()

    assert controller.client is None
    assert controller.status.state == "unavailable"
    db.close()


@pytest.mark.asyncio
async def test_controller_keeps_missing_media_visible_during_stale_poll(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.client = process.client
    queue.play_now(0)
    videos[0].unlink()

    await controller._observe(VLCStatus("playing", path=videos[0]))

    assert queue.entries()[0].state == "missing"
    db.close()


@pytest.mark.asyncio
async def test_controller_controls_and_conservative_completion(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.client = process.client  # avoid polling for deterministic unit test
    await controller.play_index(0)
    await controller.toggle_pause()
    await controller.seek(10)
    assert process.client.played == [videos[0].resolve()]
    assert process.client.commands == [("pl_pause", {}), ("seek", {"val": "+10"})]
    await controller._observe(VLCStatus("playing", 9_000, 10_000, videos[0].resolve()))
    await controller._observe(VLCStatus("stopped", 9_000, 10_000, videos[0].resolve()))
    assert queue.entries()[0].state == "completed"
    assert queue.current() is not None and queue.current().path == videos[1].resolve()
    assert db.progress_for(videos[0])["completion_observed"] == 1
    db.close()


@pytest.mark.asyncio
async def test_stopped_current_item_still_uses_resume_policy(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    current = queue.play_now(1)
    db.set_state(current.id, "stopped")
    queue.update_progress(videos[1], 4_000, 10_000)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.client = process.client

    with pytest.raises(ResumeChoiceRequired):
        await controller.play_with_policy(1)
    assert process.client.played == []
    db.close()


@pytest.mark.asyncio
async def test_explicit_policy_cancel_preserves_queue_and_current_item(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    queue.update_progress(videos[1], 4_000, 10_000)
    process = FakeProcess()
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.client = process.client
    before = [(entry.id, entry.state) for entry in queue.entries()]

    with pytest.raises(ResumeChoiceRequired):
        await controller.play_with_policy(1)
    assert process.client.played == []
    assert [(entry.id, entry.state) for entry in queue.entries()] == before
    assert await controller.play_with_policy(1, choice="cancel") is False
    assert process.client.played == []
    assert [(entry.id, entry.state) for entry in queue.entries()] == before

    assert await controller.play_with_policy(1, choice="resume") is True
    assert process.client.played == [videos[1].resolve()]
    assert any(command == "seek" for command, _parameters in process.client.commands)
    db.close()


def test_switching_from_stopped_current_normalizes_only_transient_state(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    third_path = videos[0].with_name("e3.mkv")
    third_path.write_bytes(b"third")
    queue.add([third_path])
    first, second, third = queue.entries()
    queue.play_now(0)
    db.set_state(first.id, "stopped")
    db.set_state(second.id, "completed")

    queue.play_now(2)

    states = {entry.id: entry.state for entry in queue.entries()}
    assert states[first.id] == "queued"
    assert states[second.id] == "completed"
    assert states[third.id] == "playing"
    assert queue.current() is not None and queue.current().id == third.id
    db.close()


def test_resume_target_prefers_persisted_current_identity(tmp_path: Path) -> None:
    db, queue, _videos = setup_queue(tmp_path)
    queue.play_now(1)
    db.set_state(queue.entries()[1].id, "stopped")
    assert queue.resume_target_index() == 1
    db.set_state(queue.entries()[1].id, "completed")
    assert queue.resume_target_index() == 0
    db.close()


def test_undo_expiration_is_reportable_without_expiring_new_removals(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    queue.remove(0)
    queue.add([videos[0]])
    assert not queue.undo_available
    assert queue.consume_undo_expired() is True
    assert queue.consume_undo_expired() is False
    db.close()


def test_missing_entry_keeps_its_terminal_state_when_advancing(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    queue.play_now(0)
    videos[0].unlink()
    assert queue.next() is not None
    assert queue.entries()[0].state == "missing"
    db.close()


def test_clear_completed_current_entry_clears_current_pointer(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    current = queue.play_now(0)
    db.set_state(current.id, "completed")

    queue.clear_completed()

    assert db.get_current_id() is None
    assert queue.entries()[0].path == videos[1].resolve()
    db.close()


def test_queue_reorder_remove_missing_and_clear(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    queue.move(0, 1)
    assert [entry.path for entry in queue.entries()] == [videos[1].resolve(), videos[0].resolve()]
    queue.sort_natural()
    assert [entry.path for entry in queue.entries()] == [videos[0].resolve(), videos[1].resolve()]
    queue.play_now(0)
    videos[1].unlink()
    assert queue.next() is None
    assert queue.entries()[1].state == "missing"
    db.set_state(queue.entries()[0].id, "completed")
    queue.clear_completed()
    assert len(queue.entries()) == 1
    queue.remove(0)
    assert queue.entries() == []
    queue.add([videos[0]])
    queue.clear_all()
    assert queue.entries() == []
    db.close()
