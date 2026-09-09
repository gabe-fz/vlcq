from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from vlcq.controller import PlaybackController, ResumeChoiceRequired
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService
from vlcq.vlc import VLCError, VLCPlaylistItem


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


class PlaylistFakeClient(FakeClient):
    def __init__(self) -> None:
        super().__init__()
        self.playlist_items: list[VLCPlaylistItem] = []
        self._next_id = 100
        self.removed: list[str] = []
        self.enqueued: list[Path] = []

    async def replace_playlist(self, path: Path) -> VLCStatus:
        self.playlist_items = [VLCPlaylistItem(str(self._next_id), path.resolve())]
        self._next_id += 1
        self.played.append(path.resolve())
        return VLCStatus("playing", path=path.resolve(), playlist_id=self.playlist_items[0].playlist_id)

    async def playlist(self) -> list[VLCPlaylistItem]:
        return list(self.playlist_items)

    async def enqueue(self, path: Path) -> VLCStatus:
        item = VLCPlaylistItem(str(self._next_id), path.resolve())
        self._next_id += 1
        self.playlist_items.append(item)
        self.enqueued.append(path.resolve())
        return VLCStatus("playing", path=self.playlist_items[0].path, playlist_id=self.playlist_items[0].playlist_id)

    async def remove(self, playlist_id: str) -> VLCStatus:
        self.removed.append(playlist_id)
        self.playlist_items = [item for item in self.playlist_items if item.playlist_id != playlist_id]
        return VLCStatus("playing", path=self.playlist_items[0].path if self.playlist_items else None)


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
async def test_completed_item_replay_is_serialized_against_polling(tmp_path: Path) -> None:
    class BlockingReplayClient(PlaylistFakeClient):
        def __init__(self) -> None:
            super().__init__()
            self.replace_started = asyncio.Event()
            self.release_replace = asyncio.Event()
            self.status_calls = 0

        async def status(self) -> VLCStatus:
            self.status_calls += 1
            active = self.playlist_items[0]
            return VLCStatus("playing", path=active.path, playlist_id=active.playlist_id)

        async def replace_playlist(self, path: Path) -> VLCStatus:
            self.replace_started.set()
            await self.release_replace.wait()
            return await super().replace_playlist(path)

    db, queue, videos = setup_queue(tmp_path)
    old_current = queue.play_now(1)
    db.merge_progress(
        videos[0],
        10_000,
        10_000,
        completed=True,
        trustworthy=True,
        resume_position_ms=10_000,
    )
    client = BlockingReplayClient()
    client.playlist_items = [VLCPlaylistItem("old", videos[1].resolve())]
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    controller.status = VLCStatus(
        "playing", path=videos[1].resolve(), playlist_id="old"
    )
    controller.active_vlc_id = "old"
    controller._active_queue_id = old_current.id

    replay = asyncio.create_task(controller.play_with_policy(0, choice="start_over"))
    await asyncio.wait_for(client.replace_started.wait(), 1)
    calls_during_final_capture = client.status_calls
    controller._running = True
    poll = asyncio.create_task(controller._poll())
    await asyncio.sleep(0)
    await asyncio.sleep(0)

    # A poll of the old VLC item here would compare it with the newly selected
    # queue item and disconnect with the reported unexpected-media error.
    assert client.status_calls == calls_during_final_capture

    client.release_replace.set()
    assert await asyncio.wait_for(replay, 1) is True
    await asyncio.sleep(0)
    controller._running = False
    poll.cancel()
    with pytest.raises(asyncio.CancelledError):
        await poll

    assert controller.client is client
    assert controller.last_error is None
    assert queue.current() is not None and queue.current().path == videos[0].resolve()
    assert queue.entries()[0].state == "playing"
    db.close()


@pytest.mark.asyncio
async def test_playlist_window_is_bounded_and_refreshes_successor_without_replay(
    tmp_path: Path,
) -> None:
    root = tmp_path / "show"
    root.mkdir()
    videos = [root / f"e{index}.mkv" for index in range(1, 4)]
    for video in videos:
        video.write_bytes(video.name.encode())
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]

    await controller.play_index(0)
    assert [item.path for item in client.playlist_items] == [videos[0].resolve(), videos[1].resolve()]
    assert len(client.played) == 1
    active_id = controller.active_vlc_id

    queue.move(2, -1)
    await controller.synchronize_playlist()
    assert [item.path for item in client.playlist_items] == [videos[0].resolve(), videos[2].resolve()]
    assert len(client.played) == 1
    assert controller.active_vlc_id == active_id
    assert controller.staged_path == videos[2].resolve()

    queue.remove(1)
    queue.remove(1)
    await controller.synchronize_playlist()
    assert [item.path for item in client.playlist_items] == [videos[0].resolve()]
    assert controller.staged_vlc_id is None
    db.close()


@pytest.mark.asyncio
async def test_successor_selection_skips_missing_and_unsafe_rows_without_staging(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    outside = tmp_path / "private" / "outside.mkv"
    outside.parent.mkdir()
    outside.write_bytes(b"outside")
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    client.enqueued.clear()

    videos[1].unlink()
    media_id = db.ensure_media(outside)
    db.connection.execute(
        "INSERT INTO queue_entries(queue_id,position,media_id,state) VALUES(?,?,?,'queued')",
        (db.active_queue_id(), 2, media_id),
    )
    unsafe_id = queue.entries()[-1].id
    await controller.synchronize_playlist(force=True)

    assert queue.entries()[1].state == "missing"
    assert queue.entries()[-1].id == unsafe_id
    assert queue.entries()[-1].state == "queued"
    assert controller.staged_vlc_id is None
    assert [item.path for item in client.playlist_items] == [videos[0].resolve()]
    assert client.enqueued == []
    db.close()


@pytest.mark.asyncio
async def test_playlist_window_reconnect_invalidates_ephemeral_ids(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.active_vlc_id = "active"
    controller.staged_vlc_id = "staged"
    controller.staged_queue_id = queue.entries()[1].id
    controller.staged_path = videos[1]
    controller._window_signature = (queue.entries()[0].id, queue.entries()[1].id)
    controller._playlist_sync_invalidated = False

    controller._invalidate_playlist_window()
    assert controller.active_vlc_id is None
    assert controller.staged_vlc_id is None
    assert controller.staged_queue_id is None
    assert controller.staged_path is None
    assert controller._window_signature is None
    assert controller._playlist_sync_invalidated
    db.close()


@pytest.mark.asyncio
async def test_native_next_adopts_staged_successor_without_replaying_it(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    assert staged_id is not None

    await controller._observe(
        VLCStatus("playing", 1_000, 10_000, videos[1].resolve(), staged_id),
        generation=generation,
    )

    assert queue.entries()[0].state == "skipped"
    assert queue.current() is not None and queue.current().path == videos[1].resolve()
    assert queue.entries()[1].state == "playing"
    assert client.played == [videos[0].resolve()]
    assert [item.path for item in client.playlist_items] == [videos[1].resolve()]
    db.close()


@pytest.mark.asyncio
async def test_identity_free_observation_fails_closed_without_history_update(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    before = dict(db.progress_for(videos[0]))

    await controller._observe(
        VLCStatus("playing", 4_000, 10_000),
        generation=controller.playback_generation,
        expected_path=videos[0],
    )

    assert dict(db.progress_for(videos[0])) == before
    assert queue.current() is not None and queue.current().path == videos[0].resolve()
    assert controller.last_error is not None
    assert controller.active_vlc_id is None
    db.close()


@pytest.mark.asyncio
async def test_unexpected_media_is_not_adopted_or_written_to_history(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    unexpected = videos[0].with_name("unexpected.mkv")
    unexpected.write_bytes(b"unexpected")
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    before = [(entry.id, entry.state) for entry in queue.entries()]
    media_count = db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0]

    await controller._observe(VLCStatus("playing", path=unexpected.resolve()))

    assert [(entry.id, entry.state) for entry in queue.entries()] == before
    assert db.connection.execute("SELECT COUNT(*) FROM media").fetchone()[0] == media_count
    assert client.played == [videos[0].resolve()]
    assert controller.last_error is not None
    db.close()


@pytest.mark.asyncio
async def test_missing_or_no_longer_staged_successor_fails_closed(tmp_path: Path) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    assert staged_id is not None
    videos[1].unlink()

    await controller._observe(
        VLCStatus("playing", path=videos[1], playlist_id=staged_id), generation=generation
    )

    assert queue.current() is not None and queue.current().path == videos[0].resolve()
    assert queue.entries()[1].state == "missing"
    assert client.played == [videos[0].resolve()]
    assert controller.staged_vlc_id is None
    db.close()


@pytest.mark.asyncio
async def test_no_longer_staged_successor_is_not_adopted_after_reorder(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    videos = [root / f"e{index}.mkv" for index in range(1, 4)]
    for video in videos:
        video.write_bytes(video.name.encode())
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    assert staged_id is not None
    queue.move(1, 1)

    await controller._observe(
        VLCStatus("playing", path=videos[1].resolve(), playlist_id=staged_id), generation=generation
    )

    assert queue.current() is not None and queue.current().path == videos[0].resolve()
    assert [entry.path for entry in queue.entries()] == [
        videos[0].resolve(), videos[2].resolve(), videos[1].resolve()
    ]
    assert client.played == [videos[0].resolve()]
    assert controller.staged_vlc_id is None
    db.close()


@pytest.mark.asyncio
async def test_native_transition_classifies_completion_from_near_end_evidence(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    active_id = controller.active_vlc_id
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    assert active_id is not None and staged_id is not None

    await controller._observe(
        VLCStatus("playing", 9_000, 10_000, videos[0].resolve(), active_id),
        generation=generation,
    )
    await controller._observe(
        VLCStatus("playing", 500, 10_000, videos[1].resolve(), staged_id),
        generation=generation,
    )

    first = queue.entries()[0]
    progress = db.progress_for(videos[0])
    assert first.state == "completed"
    assert progress["completion_observed"] == 1
    assert progress["position_ms"] == 10_000
    assert progress["resume_position_ms"] == 10_000
    assert queue.current() is not None and queue.current().path == videos[1].resolve()
    db.close()


@pytest.mark.asyncio
async def test_native_transition_rolls_back_history_when_queue_update_fails(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    active_id = controller.active_vlc_id
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    first = queue.entries()[0]
    assert active_id is not None and staged_id is not None

    await controller._observe(
        VLCStatus("playing", 9_000, 10_000, videos[0].resolve(), active_id),
        generation=generation,
    )
    before = dict(db.progress_for(videos[0]))
    db.connection.execute(
        "CREATE TRIGGER reject_completed_transition "
        "BEFORE UPDATE OF state ON queue_entries "
        f"WHEN OLD.id={first.id} AND NEW.state='completed' "
        "BEGIN SELECT RAISE(ABORT, 'injected transition failure'); END"
    )

    await controller._observe(
        VLCStatus("playing", 500, 10_000, videos[1].resolve(), staged_id),
        generation=generation,
    )

    assert dict(db.progress_for(videos[0])) == before
    assert queue.current() is not None and queue.current().id == first.id
    assert [entry.state for entry in queue.entries()] == ["playing", "queued"]
    assert controller.last_error is not None
    db.close()


@pytest.mark.asyncio
async def test_earlier_native_transition_records_skip_and_last_trustworthy_progress(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    active_id = controller.active_vlc_id
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    assert active_id is not None and staged_id is not None

    await controller._observe(
        VLCStatus("playing", 2_000, 10_000, videos[0].resolve(), active_id),
        generation=generation,
    )
    await controller._observe(
        VLCStatus("playing", 500, 10_000, videos[1].resolve(), staged_id),
        generation=generation,
    )

    first = queue.entries()[0]
    progress = db.progress_for(videos[0])
    assert first.state == "skipped"
    assert progress["completion_observed"] == 0
    assert progress["position_ms"] == 2_000
    assert progress["resume_position_ms"] == 2_000
    db.close()


@pytest.mark.asyncio
async def test_repeated_native_advancement_keeps_playlist_window_bounded(tmp_path: Path) -> None:
    root = tmp_path / "show"
    root.mkdir()
    videos = [root / f"e{index}.mkv" for index in range(1, 4)]
    for video in videos:
        video.write_bytes(video.name.encode())
    db = Database(tmp_path / "db.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add(videos)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)

    first_staged = controller.staged_vlc_id
    assert first_staged is not None
    await controller._observe(
        VLCStatus("playing", path=videos[1].resolve(), playlist_id=first_staged),
        generation=controller.playback_generation,
    )
    assert [item.path for item in client.playlist_items] == [
        videos[1].resolve(),
        videos[2].resolve(),
    ]
    second_staged = controller.staged_vlc_id
    assert second_staged is not None

    await controller._observe(
        VLCStatus("playing", path=videos[2].resolve(), playlist_id=second_staged),
        generation=controller.playback_generation,
    )
    assert [item.path for item in client.playlist_items] == [videos[2].resolve()]
    assert len(client.playlist_items) <= 2
    assert len(client.played) == 1
    db.close()


@pytest.mark.asyncio
async def test_stale_and_duplicate_successor_observations_do_not_transition_twice(
    tmp_path: Path,
) -> None:
    db, queue, videos = setup_queue(tmp_path)
    client = PlaylistFakeClient()
    controller = PlaybackController(queue, process=FakeProcess())  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    await controller.play_index(0)
    staged_id = controller.staged_vlc_id
    generation = controller.playback_generation
    assert staged_id is not None

    await controller._observe(
        VLCStatus("playing", path=videos[1].resolve(), playlist_id=staged_id),
        generation=generation - 1,
    )
    assert queue.current() is not None and queue.current().path == videos[0].resolve()

    await controller._observe(
        VLCStatus("playing", path=videos[1].resolve(), playlist_id=staged_id),
        generation=generation,
    )
    assert queue.current() is not None and queue.current().path == videos[1].resolve()
    states = [entry.state for entry in queue.entries()]
    assert states.count("playing") == 1
    after_first = [(entry.id, entry.state) for entry in queue.entries()]

    await controller._observe(
        VLCStatus("playing", 2_000, 10_000, videos[1].resolve(), staged_id),
        generation=generation + 1,
    )
    assert [(entry.id, entry.state) for entry in queue.entries()] == after_first
    db.close()


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
