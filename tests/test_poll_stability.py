"""The status poller tolerates transient hiccups but not a dead player."""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest

from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService
from vlcq.vlc import VLCError


class FlakyClient:
    def __init__(self, controller_ref: list[PlaybackController], script: list[object]) -> None:
        self._controller = controller_ref
        self.script = script
        self.calls = 0
        self.closed = False

    async def status(self) -> VLCStatus:
        self.calls += 1
        if not self.script:
            self._controller[0]._running = False
            return VLCStatus("stopped")
        step = self.script.pop(0)
        if isinstance(step, BaseException):
            raise step
        if step == "slow":
            await asyncio.sleep(10)
        assert isinstance(step, VLCStatus)
        return step

    async def command(self, command: str, **_parameters: str | int) -> VLCStatus:
        return VLCStatus("paused" if command == "pl_pause" else "playing")

    async def close(self) -> None:
        self.closed = True


@pytest.fixture
def setup(tmp_path: Path):
    video = tmp_path / "one.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "queue.sqlite3")
    queue = QueueService(db)
    queue.open(tmp_path)
    queue.add([video])
    process = SimpleNamespace(process=SimpleNamespace(returncode=None))
    controller = PlaybackController(queue, process=process)  # type: ignore[arg-type]
    controller.poll_retry_delay = 0
    controller._running = True
    yield controller, process, queue, video
    db.close()


def _attach(controller: PlaybackController, script: list[object]) -> FlakyClient:
    client = FlakyClient([controller], script)
    controller.client = client  # type: ignore[assignment]
    return client


async def _run_poll(controller: PlaybackController, monkeypatch: pytest.MonkeyPatch) -> None:
    real_sleep = asyncio.sleep
    monkeypatch.setattr("vlcq.controller.asyncio.sleep", lambda _delay: real_sleep(0))
    await asyncio.wait_for(PlaybackController._poll(controller), 5)


async def test_transient_failures_keep_live_connection(setup, monkeypatch) -> None:
    controller, _process, _queue, _video = setup
    client = _attach(controller, [VLCError("blip"), VLCError("blip"), VLCStatus("stopped")])
    await _run_poll(controller, monkeypatch)
    assert controller.client is client
    assert controller.last_error is None
    assert client.calls >= 3


async def test_repeated_failures_retire_connection(setup, monkeypatch) -> None:
    controller, _process, _queue, _video = setup
    client = _attach(controller, [VLCError("down")] * 3)
    await _run_poll(controller, monkeypatch)
    assert controller.client is None
    assert client.closed
    assert controller.last_error is not None


async def test_exited_player_is_retired_immediately(setup, monkeypatch) -> None:
    controller, process, _queue, _video = setup
    process.process.returncode = 0
    client = _attach(controller, [VLCError("quit"), VLCStatus("stopped")])
    await _run_poll(controller, monkeypatch)
    assert controller.client is None
    assert client.calls == 1


async def test_unexpected_observation_error_does_not_kill_poll(setup, monkeypatch) -> None:
    controller, _process, _queue, _video = setup
    client = _attach(controller, [VLCStatus("stopped"), VLCStatus("stopped")])
    calls = {"n": 0}
    original = controller._observe_locked

    async def flaky_observe(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise sqlite3.OperationalError("database is locked")
        return await original(*args, **kwargs)

    monkeypatch.setattr(controller, "_observe_locked", flaky_observe)
    await _run_poll(controller, monkeypatch)
    assert controller.client is client
    assert calls["n"] >= 2
    assert controller.observation_error is None  # cleared by the next good poll


async def test_slow_status_does_not_reload_paused_video(setup) -> None:
    controller, _process, queue, video = setup
    current = queue.entries()[0]
    queue.database.set_current(current.id)
    queue.set_current_state(current.id, "paused")
    controller.observation_timeout = 0.01
    controller.readiness_timeout = 1.0
    paused = VLCStatus("paused", 5_000, 60_000, video)

    class SlowOnce(FlakyClient):
        async def status(self) -> VLCStatus:
            self.calls += 1
            if self.calls == 1:
                await asyncio.sleep(0.2)
            return paused

    client = SlowOnce([controller], [])
    controller.client = client  # type: ignore[assignment]
    reloads: list[object] = []

    async def no_reload(entry):
        reloads.append(entry)

    controller._play_automatic_entry_locked = no_reload  # type: ignore[method-assign]
    await controller.toggle_pause()
    assert reloads == []


async def test_transport_key_errors_are_reported_not_fatal(tmp_path: Path) -> None:
    from vlcq.tui import VLCQApp

    root = tmp_path / "media"
    root.mkdir()
    video = root / "e01.mkv"
    video.write_bytes(b"video")
    db = Database(tmp_path / "db.sqlite3")
    try:
        app = VLCQApp(root=root, database=db, no_vlc=False)
        app.queue.add([video])
        app.database.set_current(app.queue.entries()[0].id)

        async def locked() -> None:
            raise sqlite3.OperationalError("database is locked")

        async def no_vlc_start() -> None:  # never launch a real player here
            return None

        app.controller.start = no_vlc_start  # type: ignore[method-assign]
        app.controller.toggle_pause = locked  # type: ignore[method-assign]
        app.controller.next = lambda completed=False: locked()  # type: ignore[method-assign]
        async with app.run_test(size=(100, 35)) as pilot:
            await app.action_pause()
            assert "Pause/resume failed" in app._notice
            await app.action_next()
            assert "Next failed" in app._notice
            await pilot.pause()
            assert app.is_running
    finally:
        db.close()
