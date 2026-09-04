from __future__ import annotations

import asyncio

from .models import VLCStatus
from .queue import QueueService
from .vlc import VLCClient, VLCError, VLCProcess


class PlaybackController:
    def __init__(self, queue: QueueService, process: VLCProcess | None = None) -> None:
        self.queue = queue
        self.process = process or VLCProcess()
        self.client: VLCClient | None = None
        self.status = VLCStatus("unavailable")
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._near_end_seen = False

    async def start(self) -> None:
        self.client = await self.process.start()
        self._running = True
        self._poll_task = asyncio.create_task(self._poll())

    async def play_index(self, index: int) -> None:
        entry = self.queue.play_now(index)
        if not entry.path.is_file():
            self.queue.database.set_state(entry.id, "missing")
            await self.next()
            return
        if self.client is None:
            raise VLCError("VLC is not connected")
        await self.client.play(entry.path)
        self._near_end_seen = False

    async def toggle_pause(self) -> None:
        if self.client:
            await self.client.command("pl_pause")

    async def seek(self, seconds: int) -> None:
        if self.client:
            sign = "+" if seconds >= 0 else ""
            await self.client.command("seek", val=f"{sign}{seconds}")

    async def next(self, completed: bool = False) -> None:
        entry = self.queue.next(completed)
        if entry and self.client:
            await self.client.play(entry.path)

    async def previous(self) -> None:
        entry = self.queue.previous()
        if entry and self.client:
            await self.client.play(entry.path)

    async def _observe(self, status: VLCStatus) -> None:
        current = self.queue.current()
        observed_path = status.path or (current.path if current else None)
        if observed_path and current and observed_path == current.path:
            self.queue.update_progress(observed_path, status.position_ms, status.duration_ms)
            if (
                status.state == "playing"
                and status.duration_ms > 0
                and status.position_ms >= max(0, status.duration_ms - 3000)
            ):
                self._near_end_seen = True
            if status.state == "stopped" and self._near_end_seen:
                self.queue.update_progress(
                    observed_path, status.duration_ms, status.duration_ms, True
                )
                await self.next(completed=True)
                self._near_end_seen = False
        self.status = status

    async def _poll(self) -> None:
        delay = 1.0
        while self._running and self.client:
            try:
                status = await self.client.status()
                await self._observe(status)
                delay = (
                    1.0
                    if status.state == "playing"
                    else 2.0
                    if status.state == "paused"
                    else min(5.0, delay * 1.5)
                )
            except VLCError:
                self.status = VLCStatus("unavailable")
                delay = min(5.0, delay * 2)
            await asyncio.sleep(delay)

    async def stop(self, stop_vlc: bool = True) -> None:
        self._running = False
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None
        if stop_vlc:
            await self.process.stop()
        elif self.client:
            await self.client.close()
        self.client = None
