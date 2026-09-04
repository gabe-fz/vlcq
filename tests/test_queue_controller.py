from __future__ import annotations

from pathlib import Path

import pytest

from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService


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
