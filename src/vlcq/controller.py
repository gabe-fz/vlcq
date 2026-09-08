from __future__ import annotations

import asyncio
from pathlib import Path

from .models import HistoryProjection, QueueEntry, VLCStatus
from .progress import is_watched
from .queue import QueueService
from .vlc import VLCClient, VLCError, VLCProcess


class ResumeChoiceRequired(VLCError):
    def __init__(self, offer: ResumeOffer) -> None:
        self.offer = offer
        label = "furthest recorded progress" if offer.legacy_fallback else "last position"
        super().__init__(f"choose Resume from {label}, Start over, or Cancel")


class ResumeOffer:
    def __init__(self, history: HistoryProjection | None, watched_percent: int = 90) -> None:
        if not 1 <= int(watched_percent) <= 100:
            raise ValueError("watched threshold must be an integer from 1 through 100")
        self.history = history
        self.watched_percent = int(watched_percent)
        self.legacy_fallback = history is not None and history.fallback_resume_position_ms is not None
        if history is None:
            self.position_ms = 0
            self.duration_ms = 0
            self.completed = False
        else:
            self.position_ms = (
                history.resume_position_ms
                if history.resume_position_ms is not None
                else history.fallback_resume_position_ms or 0
            )
            self.duration_ms = history.duration_ms
            self.completed = is_watched(
                history.position_ms,
                history.duration_ms,
                completion_observed=history.completion_observed,
                threshold=self.watched_percent,
            )

    @property
    def usable_resume(self) -> bool:
        return self.history is not None and self.history.resume_position_ms is not None and self.position_ms > 0


class PlaybackController:
    """Own the ordering boundary between VLC observations and queue state."""

    def __init__(
        self,
        queue: QueueService,
        process: VLCProcess | None = None,
        watched_percent: int | None = None,
    ) -> None:
        self.queue = queue
        self.watched_percent = queue.watched_percent if watched_percent is None else int(watched_percent)
        if not 1 <= self.watched_percent <= 100:
            raise ValueError("watched threshold must be an integer from 1 through 100")
        self.process = process or VLCProcess()
        self.client: VLCClient | None = None
        self.status = VLCStatus("unavailable")
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._near_end_seen = False
        self._generation = 0
        self._transition_lock = asyncio.Lock()
        self._last_valid_status: VLCStatus | None = None
        self.observation_timeout = 0.75
        self.readiness_timeout = 3.0

    @property
    def playback_generation(self) -> int:
        return self._generation

    async def start(self) -> None:
        """Connect exactly once and own exactly one polling task."""
        async with self._transition_lock:
            if self.client is not None and self._running:
                return
            if self._poll_task is not None and not self._poll_task.done():
                return
            self.client = await self.process.start()
            self._running = True
            self._generation += 1
            self._poll_task = asyncio.create_task(self._poll())

    async def reconnect(self) -> None:
        # ``start`` is the idempotent lifecycle boundary and deliberately does
        # not select or autoplay any queue item.
        await self.start()

    def _same_path(self, left: Path | None, right: Path | None) -> bool:
        if left is None or right is None:
            return left is right
        try:
            return left.resolve() == right.resolve()
        except (OSError, RuntimeError):
            return False

    def _current_expected_path(self) -> Path | None:
        current = self.queue.current()
        return current.path if current is not None else None

    async def _capture_final_observation_locked(self) -> bool:
        """Capture a bounded matching status, retaining the last valid value."""
        client = self.client
        current = self.queue.current()
        if client is None or current is None:
            return True
        generation = self._generation
        expected = current.path
        try:
            status = await asyncio.wait_for(client.status(), self.observation_timeout)
        except (TimeoutError, VLCError, OSError, RuntimeError, AttributeError):
            return False
        if generation != self._generation:
            return False
        if status.state == "unavailable":
            return False
        if status.path is not None and not self._same_path(status.path, expected):
            return False
        await self._observe(
            status, generation=generation, expected_path=expected, allow_advance=False
        )
        return True

    async def _wait_for_expected_media(
        self, initial: VLCStatus, expected: Path, generation: int
    ) -> VLCStatus:
        """Do not seek or sync queue state until VLC names the expected file."""
        if initial.path is not None and self._same_path(initial.path, expected):
            return initial
        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        deadline = asyncio.get_running_loop().time() + self.readiness_timeout
        latest = initial
        while asyncio.get_running_loop().time() < deadline:
            if generation != self._generation:
                raise VLCError("playback operation was superseded")
            remaining = max(0.01, deadline - asyncio.get_running_loop().time())
            try:
                latest = await asyncio.wait_for(client.status(), min(0.25, remaining))
            except TimeoutError:
                continue
            if latest.path is not None and self._same_path(latest.path, expected):
                return latest
            await asyncio.sleep(0)
        raise VLCError("VLC did not become ready for the expected media")

    async def _seek_absolute_locked(self, position_ms: int, duration_ms: int, expected: Path) -> None:
        if duration_ms <= 0:
            raise VLCError("cannot seek without a known duration")
        target = max(0, min(int(position_ms), int(duration_ms)))
        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        self._near_end_seen = False
        # VLC's HTTP interface accepts an absolute seek when type=absolute;
        # clamp before sending so malformed or stale history cannot seek out of
        # range.  Whole seconds match VLC's command precision.
        await client.command("seek", val=str(target // 1000), type="absolute")
        self.status = VLCStatus("playing", target, duration_ms, expected)
        self._last_valid_status = self.status

    async def _play_entry_locked(
        self,
        entry_index: int,
        *,
        resume_position_ms: int | None = None,
        duration_ms: int = 0,
        start_over: bool = False,
    ) -> None:
        if self.client is None:
            raise VLCError("VLC is not connected")
        entries = self.queue.entries()
        if not 0 <= entry_index < len(entries):
            raise IndexError("queue index out of range")
        entry = entries[entry_index]
        if not entry.path.is_file():
            self.queue.database.set_state(entry.id, "missing")
            raise FileNotFoundError("video is missing")

        current = self.queue.current()
        if current is not None and current.id == entry.id and current.state in {"playing", "paused"}:
            # Activating the current item is not a reload.  A paused item may
            # be resumed, but its media identity and position stay intact.
            if current.state == "paused":
                self.status = await self.client.command("pl_pause")
                self._sync_queue_state(self.status, entry.path)
            return

        await self._capture_final_observation_locked()
        self._generation += 1
        generation = self._generation
        self._near_end_seen = False
        self.queue.play_now(entry_index)
        try:
            response = await self.client.play(entry.path)
            ready = await self._wait_for_expected_media(response, entry.path, generation)
            self.status = ready
            self._last_valid_status = ready
            self._sync_queue_state(ready, entry.path)
            if start_over:
                self.queue.update_progress(
                    entry.path,
                    0,
                    duration_ms or ready.duration_ms,
                    trustworthy=True,
                    resume_position_ms=0,
                    allow_resume_reset=True,
                )
            if resume_position_ms is not None:
                seek_duration = duration_ms or ready.duration_ms
                if seek_duration <= 0:
                    raise VLCError("cannot seek without a known duration")
                await self._seek_absolute_locked(resume_position_ms, seek_duration, entry.path)
                self.queue.update_progress(
                    entry.path,
                    resume_position_ms,
                    seek_duration,
                    trustworthy=True,
                    resume_position_ms=resume_position_ms,
                    allow_resume_reset=True,
                )
        except (VLCError, OSError, TimeoutError):
            self.queue.set_current_state(entry.id, "failed")
            self.status = VLCStatus("unavailable", path=entry.path)
            raise

    def resume_offer(self, index: int) -> ResumeOffer:
        entries = self.queue.entries()
        if not 0 <= index < len(entries):
            raise IndexError("queue index out of range")
        return ResumeOffer(
            self.queue.database.history_for(entries[index].path, root=self.queue.root),
            self.watched_percent,
        )

    async def _play_with_policy_locked(
        self,
        index: int,
        *,
        choice: str | None = None,
        automatic: bool = False,
    ) -> bool:
        """Apply the one playback policy while holding the transition lock."""
        entries = self.queue.entries()
        if not 0 <= index < len(entries):
            raise IndexError("queue index out of range")
        current = self.queue.current()
        if (
            current is not None
            and current.id == entries[index].id
            and current.state in {"playing", "paused"}
        ):
            # Activating the active item is intentionally not a reload.  A
            # paused item is resumed by _play_entry_locked; no resume dialog is
            # presented and no queue insertion occurs.  A stopped current item
            # still goes through the normal resume choice on CLI/TUI startup.
            await self._play_entry_locked(index)
            return True
        offer = self.resume_offer(index)
        if offer.completed:
            await self._play_entry_locked(
                index,
                resume_position_ms=0 if offer.duration_ms > 0 else None,
                duration_ms=offer.duration_ms,
                start_over=True,
            )
            return True
        if offer.usable_resume or offer.legacy_fallback:
            if offer.legacy_fallback and automatic and not offer.usable_resume:
                # A legacy maximum is not a trustworthy last position.  It is
                # safe for automatic advancement to start from zero instead.
                await self._play_entry_locked(index)
                return True
            if choice is None and not automatic:
                raise ResumeChoiceRequired(offer)
            if choice == "cancel":
                return False
            if choice not in {None, "resume", "start_over"}:
                raise ValueError("unknown resume choice")
            if choice == "start_over":
                await self._play_entry_locked(
                    index,
                    resume_position_ms=0 if offer.duration_ms > 0 else None,
                    duration_ms=offer.duration_ms,
                    start_over=True,
                )
            else:
                await self._play_entry_locked(
                    index,
                    resume_position_ms=offer.position_ms,
                    duration_ms=offer.duration_ms,
                )
            return True
        # No usable resume point: explicit and automatic playback both start at
        # zero, without presenting a misleading choice.
        await self._play_entry_locked(index)
        return True

    async def play_with_policy(
        self,
        index: int,
        *,
        choice: str | None = None,
        automatic: bool = False,
    ) -> bool:
        """Apply the single playback policy used by UI, CLI, and autoplay."""
        async with self._transition_lock:
            return await self._play_with_policy_locked(
                index, choice=choice, automatic=automatic
            )

    async def play_index(
        self,
        index: int,
        *,
        resume_position_ms: int | None = None,
        duration_ms: int = 0,
        start_over: bool = False,
    ) -> None:
        async with self._transition_lock:
            await self._play_entry_locked(
                index,
                resume_position_ms=resume_position_ms,
                duration_ms=duration_ms,
                start_over=start_over,
            )

    def _sync_queue_state(self, status: VLCStatus, expected_path: Path | None = None) -> None:
        """Reflect an observed controller state on the active queue row."""
        current = self.queue.current()
        if current is None:
            return
        observed_path = status.path if status.path is not None else expected_path
        if observed_path is not None and not self._same_path(observed_path, current.path):
            return
        if not current.path.is_file():
            self.queue.set_current_state(current.id, "missing")
            return
        if status.state in {"playing", "paused", "stopped"}:
            self.queue.set_current_state(current.id, status.state)

    async def toggle_pause(self) -> None:
        async with self._transition_lock:
            if self.client:
                self._generation += 1
                self.status = await self.client.command("pl_pause")
                self._last_valid_status = self.status
                self._sync_queue_state(self.status)

    async def seek(self, seconds: int) -> None:
        async with self._transition_lock:
            self._near_end_seen = False
            self._generation += 1
            if self.client:
                status = await self.client.command("seek", val=f"{seconds:+d}")
                if status.state != "unavailable":
                    self.status = status
                    self._last_valid_status = status
                    self._sync_queue_state(status)

    async def seek_absolute(self, position_ms: int, duration_ms: int | None = None) -> None:
        async with self._transition_lock:
            current = self.queue.current()
            if self.client is None or current is None:
                raise VLCError("absolute seek is unavailable while disconnected")
            status = self.status
            duration = duration_ms or status.duration_ms
            if (
                duration <= 0
                or status.path is None
                or not self._same_path(status.path, current.path)
                or status.state == "unavailable"
            ):
                raise VLCError("absolute seek requires a matching item with known duration")
            self._generation += 1
            await self._seek_absolute_locked(position_ms, duration, current.path)
            self.queue.update_progress(
                current.path,
                max(0, min(position_ms, duration)),
                duration,
                trustworthy=True,
                resume_position_ms=max(0, min(position_ms, duration)),
                allow_resume_reset=True,
            )

    async def _play_automatic_entry_locked(self, entry: QueueEntry) -> None:
        if self.client is None:
            return
        path = entry.path
        entry_id = entry.id
        offer = ResumeOffer(
            self.queue.database.history_for(path, root=self.queue.root), self.watched_percent
        )
        resume_position = offer.position_ms if offer.usable_resume else None
        start_over = offer.completed
        try:
            response = await self.client.play(path)
            ready = await self._wait_for_expected_media(response, path, self._generation)
            self.status = ready
            self._last_valid_status = ready
            self._sync_queue_state(ready, path)
            if start_over:
                self.queue.update_progress(
                    path,
                    0,
                    offer.duration_ms or ready.duration_ms,
                    trustworthy=True,
                    resume_position_ms=0,
                    allow_resume_reset=True,
                )
            if resume_position is not None:
                duration = offer.duration_ms or ready.duration_ms
                if duration <= 0:
                    raise VLCError("cannot resume without a known duration")
                await self._seek_absolute_locked(resume_position, duration, path)
                self.queue.update_progress(
                    path,
                    resume_position,
                    duration,
                    trustworthy=True,
                    resume_position_ms=resume_position,
                    allow_resume_reset=True,
                )
        except (VLCError, OSError, TimeoutError):
            self.queue.set_current_state(entry_id, "failed")
            self.status = VLCStatus("unavailable", path=path)
            raise

    async def next(self, completed: bool = False) -> None:
        async with self._transition_lock:
            await self._capture_final_observation_locked()
            self._near_end_seen = False
            self._generation += 1
            entry = self.queue.next(completed)
            if entry and self.client:
                await self._play_automatic_entry_locked(entry)

    async def previous(self) -> None:
        async with self._transition_lock:
            await self._capture_final_observation_locked()
            self._near_end_seen = False
            self._generation += 1
            entry = self.queue.previous()
            if entry and self.client:
                await self._play_automatic_entry_locked(entry)

    async def _observe(
        self,
        status: VLCStatus,
        *,
        generation: int | None = None,
        expected_path: Path | None = None,
        allow_advance: bool = True,
    ) -> None:
        # A response obtained before a transition belongs to the old media and
        # must not update the new media or trigger natural advancement.
        if generation is not None and generation != self._generation:
            return
        if (
            expected_path is not None
            and status.path is not None
            and not self._same_path(status.path, expected_path)
        ):
            return
        if status.state == "unavailable":
            return
        current = self.queue.current()
        observed_path = status.path or expected_path or (current.path if current else None)
        if current is None or observed_path is None or not self._same_path(observed_path, current.path):
            return
        if not current.path.is_file():
            self.queue.set_current_state(current.id, "missing")
            return
        self.status = status
        self._last_valid_status = status
        self._sync_queue_state(status, observed_path)
        try:
            trustworthy = not (status.state == "stopped" and status.position_ms == 0)
            self.queue.update_progress(
                observed_path,
                status.position_ms,
                status.duration_ms,
                trustworthy=trustworthy,
                resume_position_ms=status.position_ms,
            )
        except OSError:
            self.queue.set_current_state(current.id, "missing")
            return
        if (
            status.state == "playing"
            and status.duration_ms > 0
            and status.position_ms >= max(0, status.duration_ms - 3000)
        ):
            self._near_end_seen = True
        if status.state == "stopped" and self._near_end_seen:
            self.queue.update_progress(
                observed_path,
                status.duration_ms,
                status.duration_ms,
                completed=True,
                trustworthy=True,
                resume_position_ms=status.duration_ms,
            )
            if allow_advance and (generation is None or generation == self._generation):
                await self.next(completed=True)
            self._near_end_seen = False

    async def _poll(self) -> None:
        delay = 1.0
        while self._running and self.client:
            client = self.client
            generation = self._generation
            expected_path = self._current_expected_path()
            try:
                status = await client.status()
                await self._observe(
                    status, generation=generation, expected_path=expected_path
                )
                delay = (
                    1.0
                    if status.state == "playing"
                    else 2.0
                    if status.state == "paused"
                    else min(5.0, delay * 1.5)
                )
            except (VLCError, OSError, RuntimeError, AttributeError):
                if self.client is client:
                    self.client = None
                self.status = VLCStatus("unavailable")
                try:
                    await client.close()
                except (VLCError, OSError, RuntimeError):
                    pass
                break
            await asyncio.sleep(delay)

    async def stop_playback(self) -> bool:
        """Stop owned playback and require a matching stopped observation."""
        async with self._transition_lock:
            client = self.client
            if client is None:
                return False
            await self._capture_final_observation_locked()
            self._generation += 1
            current = self.queue.current()
            expected = current.path if current else None
            try:
                response = await client.command("pl_stop")
                if response.state != "stopped":
                    return False
                if expected is not None and response.path is not None and not self._same_path(
                    response.path, expected
                ):
                    return False
                if current is not None:
                    self.queue.set_current_state(current.id, "stopped")
                self.status = VLCStatus("stopped", response.position_ms, response.duration_ms, expected)
                self._last_valid_status = self.status
                return True
            except (VLCError, OSError, RuntimeError):
                return False

    async def stop(self, stop_vlc: bool = True) -> None:
        async with self._transition_lock:
            self._running = False
            self._generation += 1
            if self._poll_task:
                current_task = asyncio.current_task()
                if self._poll_task is not current_task:
                    self._poll_task.cancel()
                    try:
                        await self._poll_task
                    except asyncio.CancelledError:
                        pass
                self._poll_task = None
            await self._capture_final_observation_locked()
            if stop_vlc:
                await self.process.stop()
            elif self.client:
                await self.client.close()
            self.client = None
            self.queue.invalidate_undo()
            current = self.queue.current()
            if current is not None and current.state in {"playing", "paused"}:
                self.queue.set_current_state(current.id, "stopped")
            self.status = VLCStatus("unavailable")
