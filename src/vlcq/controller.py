from __future__ import annotations

import asyncio
from pathlib import Path

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
        if self.client is None:
            raise VLCError("VLC is not connected")
        entry = self.queue.play_now(index)
        if not entry.path.is_file():
            self.queue.database.set_state(entry.id, "missing")
            await self.next()
            return
        try:
            self.status = await self.client.play(entry.path)
        except (VLCError, OSError):
            self.queue.database.set_state(entry.id, "failed")
            self.status = VLCStatus("unavailable", path=entry.path)
            raise
        self._sync_queue_state(self.status, entry.path)
        self._near_end_seen = False

    def _sync_queue_state(self, status: VLCStatus, expected_path: Path | None = None) -> None:
        """Reflect an observed controller state on the active queue row."""
        current = self.queue.current()
        if current is None:
            return
        observed_path = status.path if status.path is not None else expected_path
        if observed_path is not None:
            try:
                observed_path = observed_path.resolve()
                current_path = current.path.resolve()
            except (OSError, RuntimeError):
                return
            if observed_path != current_path:
                return
        if not current.path.is_file():
            # ``QueueService.entries`` marks vanished media as missing, but a
            # stale VLC poll can otherwise overwrite that diagnosis with a
            # playing/paused state.
            self.queue.database.set_state(current.id, "missing")
            return
        if status.state in {"playing", "paused", "stopped"}:
            self.queue.database.set_state(current.id, status.state)

    async def toggle_pause(self) -> None:
        if self.client:
            self.status = await self.client.command("pl_pause")
            self._sync_queue_state(self.status)

    async def seek(self, seconds: int) -> None:
        # A seek is a manual transition.  A near-end observation from before
        # it must not be reused as evidence that a later stop was natural.
        self._near_end_seen = False
        if self.client:
            await self.client.command("seek", val=f"{seconds:+d}")

    async def next(self, completed: bool = False) -> None:
        # Advancing, including an advance to no item, starts a new completion
        # observation window.
        self._near_end_seen = False
        entry = self.queue.next(completed)
        if entry and self.client:
            try:
                self.status = await self.client.play(entry.path)
            except (VLCError, OSError):
                self.queue.database.set_state(entry.id, "failed")
                self.status = VLCStatus("unavailable", path=entry.path)
                raise
            self._sync_queue_state(self.status, entry.path)

    async def previous(self) -> None:
        # Returning to an earlier item also invalidates any near-end evidence
        # collected for the item that was active before this transition.
        self._near_end_seen = False
        entry = self.queue.previous()
        if entry and self.client:
            try:
                self.status = await self.client.play(entry.path)
            except (VLCError, OSError):
                self.queue.database.set_state(entry.id, "failed")
                self.status = VLCStatus("unavailable", path=entry.path)
                raise
            self._sync_queue_state(self.status, entry.path)

    async def _observe(self, status: VLCStatus) -> None:
        # Set the observed value before advancing so an autoplay transition may
        # replace it with the next item's playback response.
        self.status = status
        current = self.queue.current()
        observed_path = status.path or (current.path if current else None)
        if observed_path and current:
            try:
                observed_matches_current = observed_path.resolve() == current.path.resolve()
            except (OSError, RuntimeError):
                observed_matches_current = False
        else:
            observed_matches_current = False
        if observed_path and current and observed_matches_current:
            self._sync_queue_state(status, observed_path)
            if not current.path.is_file():
                self.queue.database.set_state(current.id, "missing")
                return
            try:
                self.queue.update_progress(observed_path, status.position_ms, status.duration_ms)
            except OSError:
                # A file may disappear between the state check and the stat in
                # the progress store; keep the queue row visibly missing.
                self.queue.database.set_state(current.id, "missing")
                return
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

    async def _poll(self) -> None:
        delay = 1.0
        while self._running and self.client:
            client = self.client
            try:
                status = await client.status()
                await self._observe(status)
                delay = (
                    1.0
                    if status.state == "playing"
                    else 2.0
                    if status.state == "paused"
                    else min(5.0, delay * 1.5)
                )
            except (VLCError, OSError):
                # A failed poll means this client can no longer be trusted.
                # Retire it directly instead of calling ``stop`` here: this
                # coroutine is itself the polling task and must not await
                # itself. Clearing the reference also makes the UI retry
                # path reconnect on the next attempt.
                if self.client is client:
                    self.client = None
                self.status = VLCStatus("unavailable")
                try:
                    await client.close()
                except (VLCError, OSError, RuntimeError):
                    pass
                # Do not leave an old polling task alive while retry creates a
                # new one; the retry path will start a fresh polling task.
                break
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
