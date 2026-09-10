from __future__ import annotations

import asyncio
import sqlite3
from collections.abc import Callable
from pathlib import Path

from .models import HistoryProjection, QueueEntry, VLCStatus
from .paths import VIDEO_EXTENSIONS, is_beneath
from .progress import (
    COVERAGE_MAX_GAP_SECONDS,
    PlaybackCoverageAccumulator,
    PlaybackObservation,
)
from .queue import QueueService
from .subtitles import (
    SubtitleChoice,
    SubtitleSnapshot,
    SubtitleTarget,
    SubtitleTrack,
    choose_english_track,
    match_remembered_track,
)
from .vlc import VLCClient, VLCError, VLCPlaylistItem, VLCProcess

NATURAL_END_WINDOW_MS = 3_000
NATURAL_TRANSITION_TIMING_TOLERANCE_SECONDS = 1.5


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
        self.legacy_fallback = (
            history is not None and history.fallback_resume_position_ms is not None
        )
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
            self.completed = history.coverage_watched(self.watched_percent)

    @property
    def usable_resume(self) -> bool:
        return (
            self.history is not None
            and self.history.resume_position_ms is not None
            and self.position_ms > 0
        )


class PlaybackController:
    """Own the ordering boundary between VLC observations and queue state."""

    def __init__(
        self,
        queue: QueueService,
        process: VLCProcess | None = None,
        watched_percent: int | None = None,
    ) -> None:
        self.queue = queue
        self.watched_percent = (
            queue.watched_percent if watched_percent is None else int(watched_percent)
        )
        if not 1 <= self.watched_percent <= 100:
            raise ValueError("watched threshold must be an integer from 1 through 100")
        self.process = process or VLCProcess()
        self.client: VLCClient | None = None
        self.status = VLCStatus("unavailable")
        self._poll_task: asyncio.Task[None] | None = None
        self._running = False
        self._near_end_seen = False
        self._generation = 0
        self._coverage = PlaybackCoverageAccumulator()
        self._transition_lock = asyncio.Lock()
        self._last_valid_status: VLCStatus | None = None
        self.active_vlc_id: str | None = None
        self.staged_vlc_id: str | None = None
        self.staged_queue_id: int | None = None
        self.staged_path: Path | None = None
        self._active_queue_id: int | None = None
        self._window_signature: tuple[int | None, int | None] | None = None
        self._playlist_sync_invalidated = True
        self.last_error: str | None = None
        self.subtitle_error: str | None = None
        self._subtitle_generation = -1
        self._subtitle_attempts = 0
        self._subtitle_automatic_done = False
        self._subtitle_explicit_done = False
        self._subtitle_explicit_requested = False
        self._subtitle_choice: SubtitleChoice | None = None
        self.subtitle_retry_limit = 3
        self.observation_timeout = 0.75
        self.readiness_timeout = 3.0

    @property
    def playback_generation(self) -> int:
        return self._generation

    @property
    def current_subtitle_choice(self) -> SubtitleChoice | None:
        return self._subtitle_choice

    def _ensure_subtitle_generation_state(self) -> None:
        if self._subtitle_generation == self._generation:
            return
        self._subtitle_generation = self._generation
        self._subtitle_attempts = 0
        self._subtitle_automatic_done = False
        self._subtitle_explicit_done = False
        self._subtitle_explicit_requested = False
        self._subtitle_choice = None
        self.subtitle_error = None

    def reset_subtitle_policy(self) -> None:
        """Allow a preference toggle to request one fresh automatic attempt."""
        self._ensure_subtitle_generation_state()
        if not self._subtitle_explicit_done:
            self._subtitle_attempts = 0
            self._subtitle_automatic_done = False
            self.subtitle_error = None

    async def _run_database_operation[Result](self, operation: Callable[[], Result]) -> Result:
        return await asyncio.to_thread(self.queue.database.run_serialized, operation)

    async def start(self) -> None:
        """Connect exactly once and own exactly one polling task."""
        async with self._transition_lock:
            if self.client is not None and self._running:
                return
            if self._poll_task is not None and not self._poll_task.done():
                return
            self.client = await self.process.start()
            self._invalidate_playlist_window()
            self.last_error = None
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

    def _reset_playback_evidence(self) -> None:
        self._coverage.reset()
        self._near_end_seen = False

    def _invalidate_playlist_window(self) -> None:
        """Forget ephemeral VLC identities after reconnects and failures."""
        self._reset_playback_evidence()
        self.active_vlc_id = None
        self.staged_vlc_id = None
        self.staged_queue_id = None
        self.staged_path = None
        self._active_queue_id = None
        self._window_signature = None
        self._playlist_sync_invalidated = True

    def _next_eligible_entry(self) -> QueueEntry | None:
        """Return the first safe, existing successor without adopting bad rows."""
        current = self.queue.current()
        if current is None or self.queue.root is None:
            return None
        entries = self.queue.entries()
        try:
            start = next(index for index, entry in enumerate(entries) if entry.id == current.id)
        except StopIteration:
            return None
        root = self.queue.root
        for entry in entries[start + 1 :]:
            try:
                canonical = entry.path.expanduser().resolve(strict=False)
            except (OSError, RuntimeError):
                continue
            if not is_beneath(canonical, root):
                # Keep an unsafe database row visible and untouched.
                continue
            if not canonical.is_file():
                if entry.state != "missing":
                    self.queue.database.set_state(entry.id, "missing")
                continue
            if canonical.suffix.casefold() not in VIDEO_EXTENSIONS:
                continue
            return entry
        return None

    async def _playlist_items(self) -> list[VLCPlaylistItem] | None:
        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        method = getattr(client, "playlist", None)
        if not callable(method):
            # Older injected fakes expose only play/command. They cannot
            # provide native Next, but remain usable for the legacy controller
            # behavior and unit tests that do not model VLC's playlist.
            return None
        items = await method()
        if not isinstance(items, list) or not all(
            isinstance(item, VLCPlaylistItem) for item in items
        ):
            raise VLCError("VLC returned malformed playlist identities")
        return items

    async def _synchronize_playlist_window_locked(self, *, force: bool = False) -> None:
        """Make VLC contain exactly the current item and one safe successor."""
        current = self.queue.current()
        successor = self._next_eligible_entry()
        signature = (
            current.id if current is not None else None,
            successor.id if successor else None,
        )
        if (
            not force
            and not self._playlist_sync_invalidated
            and signature == self._window_signature
        ):
            return
        items = await self._playlist_items()
        if items is None:
            self._window_signature = signature
            self._playlist_sync_invalidated = False
            self.last_error = None
            self.active_vlc_id = self.status.playlist_id
            self._active_queue_id = current.id if current is not None else None
            self.staged_queue_id = successor.id if successor is not None else None
            self.staged_path = successor.path if successor is not None else None
            self.staged_vlc_id = None
            return

        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        if (
            current is not None
            and not items
            and self.active_vlc_id is None
            and self.status.state == "stopped"
            and self.status.path is None
            and self.status.playlist_id is None
        ):
            # A newly connected VLC instance is intentionally idle even when
            # the database retains a current queue identity. Reconnect must not
            # load or autoplay that item; an explicit play action will replace
            # the empty playlist and establish fresh ephemeral identities.
            self.staged_vlc_id = None
            self.staged_queue_id = None
            self.staged_path = None
            self._active_queue_id = None
            self._window_signature = signature
            self._playlist_sync_invalidated = False
            self.last_error = None
            return
        remove = getattr(client, "remove", None)
        enqueue = getattr(client, "enqueue", None)
        if not callable(remove) or not callable(enqueue):
            raise VLCError("VLC playlist controls are unavailable")
        if current is None:
            for item in items:
                await remove(item.playlist_id)
            self._invalidate_playlist_window()
            self._window_signature = signature
            self._playlist_sync_invalidated = False
            self.last_error = None
            return

        current_matches = [item for item in items if self._same_path(item.path, current.path)]
        if self.status.path is not None and not self._same_path(self.status.path, current.path):
            raise VLCError("VLC is playing unexpected media")
        if self.status.playlist_id is not None:
            identified = [item for item in items if item.playlist_id == self.status.playlist_id]
            if identified and not self._same_path(identified[0].path, current.path):
                raise VLCError("VLC is playing unexpected media")
        if len(current_matches) != 1:
            raise VLCError("VLC active playlist identity is unavailable")
        active = current_matches[0]
        staged = next(
            (
                item
                for item in items
                if successor is not None and self._same_path(item.path, successor.path)
            ),
            None,
        )
        if staged is not None and staged.playlist_id == active.playlist_id:
            raise VLCError("VLC playlist identities are ambiguous")
        for item in items:
            if item.playlist_id not in {active.playlist_id, staged.playlist_id if staged else None}:
                await remove(item.playlist_id)
        if successor is not None and staged is None:
            await enqueue(successor.path)
            refreshed = await self._playlist_items()
            if refreshed is None:
                raise VLCError("VLC playlist inspection is unavailable")
            staged_matches = [
                item for item in refreshed if self._same_path(item.path, successor.path)
            ]
            active_matches = [
                item for item in refreshed if self._same_path(item.path, current.path)
            ]
            if len(staged_matches) != 1 or len(active_matches) != 1:
                raise VLCError("VLC playlist window could not be verified")
            active, staged = active_matches[0], staged_matches[0]
        elif staged is not None:
            refreshed = await self._playlist_items()
            if refreshed is None:
                raise VLCError("VLC playlist inspection is unavailable")
            active_matches = [item for item in refreshed if item.playlist_id == active.playlist_id]
            staged_matches = [item for item in refreshed if item.playlist_id == staged.playlist_id]
            if len(active_matches) != 1 or len(staged_matches) != 1:
                raise VLCError("VLC playlist window could not be verified")
            active, staged = active_matches[0], staged_matches[0]
        self.active_vlc_id = active.playlist_id
        self._active_queue_id = current.id
        self.staged_queue_id = successor.id if successor is not None else None
        self.staged_vlc_id = staged.playlist_id if staged is not None else None
        self.staged_path = successor.path if successor is not None else None
        self._window_signature = signature
        self._playlist_sync_invalidated = False
        self.last_error = None

    async def synchronize_playlist(self, *, force: bool = False) -> None:
        async with self._transition_lock:
            await self._synchronize_playlist_window_locked(force=force)

    # Names used by integrations and mutation boundaries.
    sync_playlist_window = synchronize_playlist
    synchronize = synchronize_playlist

    def _current_subtitle_target_locked(self) -> SubtitleTarget | None:
        self._ensure_subtitle_generation_state()
        current = self.queue.current()
        if current is None or self.client is None or self.status.state == "unavailable":
            return None
        if self.status.path is not None and not self._same_path(self.status.path, current.path):
            return None
        if (
            self.status.playlist_id is not None
            and self.active_vlc_id is not None
            and self.status.playlist_id != self.active_vlc_id
        ):
            return None
        playlist_id = self.active_vlc_id or self.status.playlist_id
        if playlist_id is None and self.status.path is None:
            return None
        return SubtitleTarget(self._generation, current.id, current.path.resolve(), playlist_id)

    def current_subtitle_target_for_path(self, path: Path) -> SubtitleTarget | None:
        target = self._current_subtitle_target_locked()
        if target is None or not self._same_path(target.path, path):
            return None
        return target

    def _validate_subtitle_target_locked(self, target: SubtitleTarget) -> QueueEntry:
        current_target = self._current_subtitle_target_locked()
        current = self.queue.current()
        if (
            current is None
            or current_target is None
            or target.generation != self._generation
            or target.queue_entry_id != current.id
            or not self._same_path(target.path, current.path)
            or (
                target.playlist_id is not None
                and current_target.playlist_id is not None
                and target.playlist_id != current_target.playlist_id
            )
        ):
            raise VLCError("subtitle action expired because playback changed")
        return current

    @staticmethod
    def _validate_subtitle_tracks(value: object) -> tuple[SubtitleTrack, ...]:
        if not isinstance(value, (tuple, list)) or not all(
            isinstance(track, SubtitleTrack) for track in value
        ):
            raise VLCError("VLC returned malformed subtitle tracks")
        return tuple(value)

    async def _subtitle_tracks_locked(self, target: SubtitleTarget) -> SubtitleSnapshot:
        self._validate_subtitle_target_locked(target)
        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        method = getattr(client, "subtitle_tracks", None)
        if not callable(method):
            raise VLCError("VLC subtitle controls are unavailable")
        tracks = self._validate_subtitle_tracks(await method())
        status_method = getattr(client, "status", None)
        if callable(status_method):
            fresh_status = await status_method()
            if not isinstance(fresh_status, VLCStatus) or fresh_status.state == "unavailable":
                raise VLCError("VLC media changed while discovering subtitles")
            if fresh_status.path is not None and not self._same_path(fresh_status.path, target.path):
                raise VLCError("VLC media changed while discovering subtitles")
            if (
                fresh_status.playlist_id is not None
                and target.playlist_id is not None
                and fresh_status.playlist_id != target.playlist_id
            ):
                raise VLCError("VLC playlist changed while discovering subtitles")
            if fresh_status.path is None and fresh_status.playlist_id is None:
                raise VLCError("VLC media identity disappeared while discovering subtitles")
        self._validate_subtitle_target_locked(target)
        return SubtitleSnapshot(target, tracks)

    async def discover_subtitles(self, target: SubtitleTarget | None = None) -> SubtitleSnapshot:
        async with self._transition_lock:
            actual = target or self._current_subtitle_target_locked()
            if actual is None:
                raise VLCError("subtitle selection requires the validated current media")
            return await self._subtitle_tracks_locked(actual)

    async def _issue_subtitle_locked(
        self, target: SubtitleTarget, track_id: str | None
    ) -> VLCStatus:
        self._validate_subtitle_target_locked(target)
        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        method = getattr(client, "select_subtitle", None)
        if not callable(method):
            raise VLCError("VLC subtitle controls are unavailable")
        response = await method(track_id)
        if not isinstance(response, VLCStatus):
            raise VLCError("VLC returned malformed subtitle selection status")
        if response.state == "unavailable":
            raise VLCError("VLC rejected the subtitle selection")
        self._validate_subtitle_target_locked(target)
        if response.path is not None and not self._same_path(response.path, target.path):
            raise VLCError("VLC changed media while selecting subtitles")
        if (
            response.playlist_id is not None
            and target.playlist_id is not None
            and response.playlist_id != target.playlist_id
        ):
            raise VLCError("VLC changed playlist identity while selecting subtitles")
        return response

    async def select_subtitle(
        self, target: SubtitleTarget, choice: SubtitleChoice
    ) -> SubtitleChoice:
        """Apply one explicit choice without touching queue or playback state."""
        # Set intent before waiting for the transition lock. If automatic
        # discovery is awaiting VLC, a queued manual action still wins the
        # race and cannot be overwritten by the delayed automatic command.
        self._ensure_subtitle_generation_state()
        self._subtitle_explicit_requested = True
        async with self._transition_lock:
            self._ensure_subtitle_generation_state()
            try:
                snapshot = await self._subtitle_tracks_locked(target)
                selected = choice
                if choice.mode == "track":
                    assert choice.track is not None
                    fresh = next(
                        (track for track in snapshot.tracks if track.track_id == choice.track.track_id),
                        None,
                    )
                    if fresh is None:
                        raise VLCError("subtitle choice is no longer available")
                    selected = SubtitleChoice.track_choice(fresh)
                    track_id = fresh.track_id
                else:
                    track_id = None
                await self._issue_subtitle_locked(target, track_id)
            except (VLCError, OSError, RuntimeError) as exc:
                self._subtitle_explicit_requested = False
                self.subtitle_error = f"Subtitle selection failed; playback continues: {exc}"
                raise
            self._subtitle_explicit_done = True
            self._subtitle_automatic_done = True
            self._subtitle_choice = selected
            self.subtitle_error = None
            if self.queue.database.remember_subtitles_by_show():
                descriptor = selected.descriptor()
                await self._run_database_operation(
                    lambda: self.queue.database.upsert_subtitle_preference(
                        target.path, descriptor, root=self.queue.root
                    )
                )
            return selected

    def _automatic_choice_locked(
        self, path: Path, tracks: tuple[SubtitleTrack, ...]
    ) -> SubtitleChoice | None:
        database = self.queue.database
        if database.remember_subtitles_by_show():
            remembered = database.show_subtitle_preference(path, root=self.queue.root)
            if remembered is not None:
                if remembered.mode == "off":
                    return SubtitleChoice.off()
                match = match_remembered_track(remembered, tracks)
                if match is not None:
                    return SubtitleChoice.track_choice(match)
        if not database.prefer_english_subtitles():
            return None
        english = choose_english_track(tracks)
        return SubtitleChoice.track_choice(english) if english is not None else None

    async def _apply_automatic_subtitles_locked(self) -> SubtitleChoice | None:
        self._ensure_subtitle_generation_state()
        if (
            self._subtitle_automatic_done
            or self._subtitle_explicit_done
            or self._subtitle_explicit_requested
        ):
            return None
        target = self._current_subtitle_target_locked()
        if target is None or self.client is None:
            return None
        if self._subtitle_attempts >= self.subtitle_retry_limit:
            self._subtitle_automatic_done = True
            return None
        self._subtitle_attempts += 1
        try:
            snapshot = await self._subtitle_tracks_locked(target)
            if self._subtitle_explicit_requested:
                return None
            if not snapshot.tracks:
                if self._subtitle_attempts >= self.subtitle_retry_limit:
                    self._subtitle_automatic_done = True
                return None
            choice = self._automatic_choice_locked(target.path, snapshot.tracks)
            if choice is None:
                self._subtitle_automatic_done = True
                self.subtitle_error = None
                return None
            await self._issue_subtitle_locked(
                target, None if choice.mode == "off" else choice.track.track_id  # type: ignore[union-attr]
            )
        except (VLCError, OSError, RuntimeError, AttributeError, TypeError) as exc:
            if self._subtitle_attempts >= self.subtitle_retry_limit:
                self._subtitle_automatic_done = True
                self.subtitle_error = (
                    f"Automatic subtitle selection stopped after {self.subtitle_retry_limit} attempts; "
                    f"playback continues: {exc}"
                )
            return None
        self._subtitle_automatic_done = True
        self._subtitle_choice = choice
        self.subtitle_error = None
        return choice

    async def apply_automatic_subtitles(self) -> SubtitleChoice | None:
        """Try the bounded per-generation subtitle policy under the transition lock."""
        async with self._transition_lock:
            return await self._apply_automatic_subtitles_locked()

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
        await self._observe_locked(
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

    async def _seek_absolute_locked(
        self, position_ms: int, duration_ms: int, expected: Path
    ) -> None:
        if duration_ms <= 0:
            raise VLCError("cannot seek without a known duration")
        target = max(0, min(int(position_ms), int(duration_ms)))
        client = self.client
        if client is None:
            raise VLCError("VLC is not connected")
        self._reset_playback_evidence()
        # VLC's HTTP interface accepts an absolute seek when type=absolute;
        # clamp before sending so malformed or stale history cannot seek out of
        # range.  Whole seconds match VLC's command precision.
        await client.command("seek", val=str(target // 1000), type="absolute")
        self.status = VLCStatus("playing", target, duration_ms, expected, self.status.playlist_id)
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
        try:
            canonical = entry.path.expanduser().resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise VLCError("refusing unsafe queue media") from exc
        if self.queue.root is None or not is_beneath(canonical, self.queue.root):
            raise VLCError("refusing unsafe queue media")
        if not canonical.is_file():
            self.queue.database.set_state(entry.id, "missing")
            raise FileNotFoundError("video is missing")

        current = self.queue.current()
        if (
            current is not None
            and current.id == entry.id
            and current.state in {"playing", "paused"}
        ):
            # Activating the current item is not a reload.  A paused item may
            # be resumed, but its media identity and position stay intact.
            if current.state == "paused":
                self._reset_playback_evidence()
                self._generation += 1
                self.status = await self.client.command("pl_pause")
                await self._sync_queue_state(self.status, entry.path)
            await self._synchronize_playlist_window_locked()
            return

        await self._capture_final_observation_locked()
        self._generation += 1
        generation = self._generation
        self._reset_playback_evidence()
        await self._run_database_operation(lambda: self.queue.play_now(entry_index))
        try:
            replace = getattr(self.client, "replace_playlist", None)
            response = (
                await replace(entry.path)
                if callable(replace)
                else await self.client.play(entry.path)
            )
            ready = await self._wait_for_expected_media(response, entry.path, generation)
            self.status = ready
            self._last_valid_status = ready
            await self._sync_queue_state(ready, entry.path)
            await self._synchronize_playlist_window_locked(force=True)
            if start_over:
                await self._run_database_operation(
                    lambda: self.queue.update_progress(
                        entry.path,
                        0,
                        duration_ms or ready.duration_ms,
                        trustworthy=True,
                        resume_position_ms=0,
                        allow_resume_reset=True,
                    )
                )
            if resume_position_ms is not None:
                seek_duration = duration_ms or ready.duration_ms
                if seek_duration <= 0:
                    raise VLCError("cannot seek without a known duration")
                await self._seek_absolute_locked(resume_position_ms, seek_duration, entry.path)
                await self._run_database_operation(
                    lambda: self.queue.update_progress(
                        entry.path,
                        resume_position_ms,
                        seek_duration,
                        trustworthy=True,
                        resume_position_ms=resume_position_ms,
                        allow_resume_reset=True,
                    )
                )
            await self._apply_automatic_subtitles_locked()
        except (VLCError, OSError, TimeoutError):
            await self._run_database_operation(
                lambda: self.queue.set_current_state(entry.id, "failed")
            )
            self.status = VLCStatus("unavailable", path=entry.path)
            self._invalidate_playlist_window()
            self.last_error = "VLC playback or playlist synchronization failed — press r to retry"
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
        offer = self.resume_offer(index)
        if (
            current is not None
            and current.id == entries[index].id
            and current.state in {"playing", "paused"}
        ):
            # Ordinary activation is not a reload. An explicit Start over is
            # different: invalidate evidence before seeking and persist zero
            # without touching historical coverage.
            if choice == "start_over":
                client = self.client
                if client is None:
                    raise VLCError("VLC is not connected")
                self._reset_playback_evidence()
                self._generation += 1
                response = await client.command("seek", val="0", type="absolute")
                if current.state == "paused":
                    response = await client.command("pl_pause")
                duration = offer.duration_ms or response.duration_ms
                self.status = VLCStatus(
                    "playing",
                    0,
                    duration,
                    current.path,
                    response.playlist_id or self.active_vlc_id,
                    response.rate,
                    response.request_started,
                    response.response_received,
                )
                self._last_valid_status = self.status
                await self._run_database_operation(
                    lambda: self.queue.update_progress(
                        current.path,
                        0,
                        duration,
                        trustworthy=True,
                        resume_position_ms=0,
                        allow_resume_reset=True,
                    )
                )
                await self._run_database_operation(
                    lambda: self.queue.set_current_state(current.id, "playing")
                )
            else:
                await self._play_entry_locked(index)
            return True
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
            return await self._play_with_policy_locked(index, choice=choice, automatic=automatic)

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

    async def _sync_queue_state(self, status: VLCStatus, expected_path: Path | None = None) -> None:
        """Reflect an observed controller state on the active queue row."""
        current = self.queue.current()
        if current is None:
            return
        observed_path = status.path if status.path is not None else expected_path
        if observed_path is not None and not self._same_path(observed_path, current.path):
            return
        if not current.path.is_file():
            await self._run_database_operation(
                lambda: self.queue.set_current_state(current.id, "missing")
            )
            return
        if status.state in {"playing", "paused", "stopped"}:
            await self._run_database_operation(
                lambda: self.queue.set_current_state(current.id, status.state)
            )

    async def toggle_pause(self) -> None:
        async with self._transition_lock:
            if self.status.state == "playing":
                # Preserve the last observed portion of a qualified run before
                # the pause command invalidates continuity. A failed or stale
                # final poll remains conservative and awards nothing.
                await self._capture_final_observation_locked()
            self._reset_playback_evidence()
            self._generation += 1
            if self.client:
                self.status = await self.client.command("pl_pause")
                self._last_valid_status = self.status
                await self._sync_queue_state(self.status)

    async def seek(self, seconds: int) -> None:
        async with self._transition_lock:
            self._reset_playback_evidence()
            self._generation += 1
            if self.client:
                status = await self.client.command("seek", val=f"{seconds:+d}")
                if status.state != "unavailable":
                    self.status = status
                    self._last_valid_status = status
                    await self._sync_queue_state(status)

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
            self._reset_playback_evidence()
            self._generation += 1
            await self._seek_absolute_locked(position_ms, duration, current.path)
            await self._run_database_operation(
                lambda: self.queue.update_progress(
                    current.path,
                    max(0, min(position_ms, duration)),
                    duration,
                    trustworthy=True,
                    resume_position_ms=max(0, min(position_ms, duration)),
                    allow_resume_reset=True,
                )
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
            replace = getattr(self.client, "replace_playlist", None)
            response = await replace(path) if callable(replace) else await self.client.play(path)
            ready = await self._wait_for_expected_media(response, path, self._generation)
            self.status = ready
            self._last_valid_status = ready
            await self._sync_queue_state(ready, path)
            await self._synchronize_playlist_window_locked(force=True)
            if start_over:
                await self._run_database_operation(
                    lambda: self.queue.update_progress(
                        path,
                        0,
                        offer.duration_ms or ready.duration_ms,
                        trustworthy=True,
                        resume_position_ms=0,
                        allow_resume_reset=True,
                    )
                )
            if resume_position is not None:
                duration = offer.duration_ms or ready.duration_ms
                if duration <= 0:
                    raise VLCError("cannot resume without a known duration")
                await self._seek_absolute_locked(resume_position, duration, path)
                await self._run_database_operation(
                    lambda: self.queue.update_progress(
                        path,
                        resume_position,
                        duration,
                        trustworthy=True,
                        resume_position_ms=resume_position,
                        allow_resume_reset=True,
                    )
                )
            await self._apply_automatic_subtitles_locked()
        except (VLCError, OSError, TimeoutError):
            await self._run_database_operation(
                lambda: self.queue.set_current_state(entry_id, "failed")
            )
            self.status = VLCStatus("unavailable", path=path)
            self._invalidate_playlist_window()
            self.last_error = "VLC playback or playlist synchronization failed — press r to retry"
            raise

    async def _next_locked(self, completed: bool = False) -> None:
        await self._capture_final_observation_locked()
        self._reset_playback_evidence()
        self._generation += 1
        entry = await self._run_database_operation(lambda: self.queue.next(completed))
        if entry and self.client:
            await self._play_automatic_entry_locked(entry)
        elif self.client:
            await self._synchronize_playlist_window_locked(force=True)

    async def next(self, completed: bool = False) -> None:
        async with self._transition_lock:
            await self._next_locked(completed)

    async def previous(self) -> None:
        async with self._transition_lock:
            await self._capture_final_observation_locked()
            self._reset_playback_evidence()
            self._generation += 1
            entry = await self._run_database_operation(self.queue.previous)
            if entry and self.client:
                await self._play_automatic_entry_locked(entry)
            elif self.client:
                await self._synchronize_playlist_window_locked(force=True)

    def _status_matches_staged(self, status: VLCStatus) -> bool:
        path_matches = (
            status.path is not None
            and self.staged_path is not None
            and self._same_path(status.path, self.staged_path)
        )
        id_matches = (
            status.playlist_id is not None
            and self.staged_vlc_id is not None
            and status.playlist_id == self.staged_vlc_id
        )
        return (
            (path_matches or id_matches)
            and (status.path is None or path_matches)
            and (status.playlist_id is None or id_matches)
        )

    def _supports_natural_transition(
        self, previous: VLCStatus | None, successor: VLCStatus
    ) -> bool:
        if not self._near_end_seen or previous is None:
            return False
        if (
            previous.duration_ms <= 0
            or previous.position_ms < previous.duration_ms - NATURAL_END_WINDOW_MS
            or previous.rate is None
            or previous.request_started is None
            or previous.response_received is None
            or successor.request_started is None
            or successor.response_received is None
        ):
            return False
        old_time = (previous.request_started + previous.response_received) / 2
        new_time = (successor.request_started + successor.response_received) / 2
        elapsed = new_time - old_time
        if elapsed <= 0 or elapsed > COVERAGE_MAX_GAP_SECONDS:
            return False
        remaining = max(0, previous.duration_ms - previous.position_ms) / (
            previous.rate * 1000
        )
        successor_elapsed = (
            successor.position_ms / (successor.rate * 1000)
            if successor.rate is not None and successor.rate > 0
            else 0.0
        )
        return (
            abs(elapsed - remaining - successor_elapsed)
            <= NATURAL_TRANSITION_TIMING_TOLERANCE_SECONDS
        )

    async def _reconcile_staged_successor_locked(
        self, status: VLCStatus, generation: int | None
    ) -> None:
        """Adopt a VLC-started successor while holding the transition lock."""
        if generation is not None and generation != self._generation:
            return
        if not self._status_matches_staged(status):
            self.last_error = (
                "VLC reported media outside the vlcq playback window; press r to retry"
            )
            self._invalidate_playlist_window()
            return
        current = self.queue.current()
        successor = self._next_eligible_entry()
        staged_id = self.staged_queue_id
        if (
            current is None
            or successor is None
            or staged_id is None
            or successor.id != staged_id
            or self.staged_path is None
            or not self._same_path(successor.path, self.staged_path)
            or not successor.path.is_file()
        ):
            self.last_error = "VLC successor is no longer staged; press r to reconnect"
            self._invalidate_playlist_window()
            return
        old_status = self._last_valid_status
        if (
            old_status is None
            or old_status.path is None
            or not self._same_path(old_status.path, current.path)
        ):
            old_status = self.status if self._same_path(self.status.path, current.path) else None
        near_end = self._supports_natural_transition(old_status, status)
        observed = VLCStatus(
            status.state,
            status.position_ms,
            status.duration_ms,
            successor.path,
            status.playlist_id or self.staged_vlc_id,
            status.rate,
            status.request_started,
            status.response_received,
        )

        successor_state = (
            observed.state if observed.state in {"playing", "paused", "stopped"} else "playing"
        )

        def transition() -> QueueEntry:
            trustworthy = old_status is not None and not (
                old_status.state == "stopped" and old_status.position_ms == 0
            )
            return self.queue.reconcile_successor(
                current.id,
                successor.id,
                successor_state,
                progress_path=current.path if old_status is not None else None,
                position_ms=old_status.position_ms if old_status is not None else 0,
                duration_ms=old_status.duration_ms if old_status is not None else 0,
                completed=near_end,
                trustworthy=trustworthy or near_end,
                resume_position_ms=(old_status.position_ms if old_status is not None else None),
            )

        try:
            adopted = await self._run_database_operation(transition)
        except (OSError, RuntimeError, ValueError, sqlite3.Error):
            self.last_error = "VLC successor transition could not be recorded; press r to retry"
            self._invalidate_playlist_window()
            return
        if adopted is None or adopted.id != successor.id:
            self.last_error = "VLC successor transition did not match the queue; press r to retry"
            self._invalidate_playlist_window()
            return
        self._generation += 1
        self._reset_playback_evidence()
        self.status = observed
        self._last_valid_status = observed
        self.active_vlc_id = observed.playlist_id
        self._active_queue_id = adopted.id
        self.staged_vlc_id = None
        self.staged_queue_id = None
        self.staged_path = None
        self._window_signature = None
        self._playlist_sync_invalidated = True
        await self._synchronize_playlist_window_locked(force=True)
        await self._apply_automatic_subtitles_locked()

    async def _record_coverage_evidence(
        self,
        status: VLCStatus,
        path: Path,
        generation: int,
    ) -> bool:
        if status.request_started is None or status.response_received is None:
            self._coverage.reset()
            if status.state != "stopped":
                self._near_end_seen = False
            return False
        evidence = self._coverage.add(
            PlaybackObservation(
                generation=generation,
                path=path.resolve(),
                playlist_id=status.playlist_id or self.active_vlc_id,
                state=status.state,
                position_ms=status.position_ms,
                duration_ms=status.duration_ms,
                rate=status.rate,
                request_started=status.request_started,
                response_received=status.response_received,
            )
        )
        if not self._coverage.recent_qualified_continuity and status.state != "stopped":
            self._near_end_seen = False
        if evidence.initialized:
            await self._run_database_operation(
                lambda: self.queue.database.merge_coverage(
                    path, evidence.ranges, status.duration_ms
                )
            )
        return evidence.qualified

    async def _observe_locked(
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
        if status.state == "unavailable":
            return
        if self._status_matches_staged(status):
            if allow_advance:
                await self._reconcile_staged_successor_locked(status, generation)
            return
        current = self.queue.current()
        if current is None:
            return
        if (
            status.state == "stopped"
            and status.path is None
            and status.playlist_id is None
            and self.active_vlc_id is None
            and self.last_error is None
        ):
            # Freshly launched VLC reports an identity-free stopped state. It
            # is not unexpected media, and reconnect is required to remain
            # idle until the user explicitly chooses what to play.
            self.status = status
            self._last_valid_status = None
            if current.state in {"playing", "paused"}:
                await self._run_database_operation(
                    lambda: self.queue.set_current_state(current.id, "stopped")
                )
            return
        path_matches = status.path is not None and self._same_path(status.path, current.path)
        identity_matches = (
            status.playlist_id is not None
            and self.active_vlc_id is not None
            and status.playlist_id == self.active_vlc_id
        )
        path_conflicts = status.path is not None and not path_matches
        identity_conflicts = (
            status.playlist_id is not None
            and self.active_vlc_id is not None
            and not identity_matches
        )
        if not (path_matches or identity_matches) or path_conflicts or identity_conflicts:
            self.last_error = (
                "VLC reported unexpected or unidentified media; "
                "playback advancement is paused — press r to retry"
            )
            self._invalidate_playlist_window()
            return
        observed_path = status.path or expected_path or current.path
        if not self._same_path(observed_path, current.path):
            self.last_error = (
                "VLC reported unexpected media; playback advancement is paused — press r to retry"
            )
            self._invalidate_playlist_window()
            return
        if not current.path.is_file():
            await self._run_database_operation(
                lambda: self.queue.set_current_state(current.id, "missing")
            )
            return
        previous_status = self._last_valid_status
        self.status = status
        self._last_valid_status = status
        await self._sync_queue_state(status, observed_path)
        try:
            trustworthy = not (status.state == "stopped" and status.position_ms == 0)
            await self._run_database_operation(
                lambda: self.queue.update_progress(
                    observed_path,
                    status.position_ms,
                    status.duration_ms,
                    trustworthy=trustworthy,
                    resume_position_ms=status.position_ms,
                )
            )
        except OSError:
            await self._run_database_operation(
                lambda: self.queue.set_current_state(current.id, "missing")
            )
            return
        evidence_qualified = await self._record_coverage_evidence(
            status, observed_path, self._generation if generation is None else generation
        )
        if (
            evidence_qualified
            and status.state == "playing"
            and status.duration_ms > 0
            and status.position_ms >= max(0, status.duration_ms - NATURAL_END_WINDOW_MS)
        ):
            self._near_end_seen = True
        if status.state == "stopped" and self._supports_natural_transition(
            previous_status, status
        ):
            self._near_end_seen = False
            await self._run_database_operation(
                lambda: self.queue.update_progress(
                    observed_path,
                    status.position_ms,
                    status.duration_ms,
                    completed=True,
                    trustworthy=status.position_ms != 0,
                    resume_position_ms=(status.position_ms if status.position_ms > 0 else None),
                )
            )
            if allow_advance and (generation is None or generation == self._generation):
                await self._next_locked(completed=True)

    async def _observe(
        self,
        status: VLCStatus,
        *,
        generation: int | None = None,
        expected_path: Path | None = None,
        allow_advance: bool = True,
    ) -> None:
        async with self._transition_lock:
            await self._observe_locked(
                status,
                generation=generation,
                expected_path=expected_path,
                allow_advance=allow_advance,
            )

    async def _poll(self) -> None:
        delay = 1.0
        while self._running and self.client:
            client = self.client
            try:
                # Status collection and interpretation belong to the same
                # serialized transition boundary as play/replay. Otherwise a
                # poll can observe the old VLC item after the queue has already
                # switched to a completed item being replayed and falsely
                # retire a healthy connection as unexpected media.
                async with self._transition_lock:
                    if self.client is not client:
                        continue
                    generation = self._generation
                    expected_path = self._current_expected_path()
                    status = await client.status()
                    await self._observe_locked(
                        status, generation=generation, expected_path=expected_path
                    )
                    if self.client is client and self.last_error is None:
                        await self._synchronize_playlist_window_locked()
                        if self.client is client and self.last_error is None:
                            await self._apply_automatic_subtitles_locked()
                    elif self.client is client and self.last_error is not None:
                        # Do not repair or delete an unexpected VLC item. Retire
                        # this connection and let an explicit reconnect establish
                        # a fresh owned playlist window.
                        self.client = None
                        await client.close()
                        break
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
                self._invalidate_playlist_window()
                self.last_error = (
                    "VLC is unavailable or playlist synchronization failed — press r to retry"
                )
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
            self._reset_playback_evidence()
            self._generation += 1
            current = self.queue.current()
            expected = current.path if current else None
            try:
                response = await client.command("pl_stop")
                if response.state != "stopped":
                    return False
                if (
                    expected is not None
                    and response.path is not None
                    and not self._same_path(response.path, expected)
                ):
                    return False
                if current is not None:
                    await self._run_database_operation(
                        lambda: self.queue.set_current_state(current.id, "stopped")
                    )
                self.status = VLCStatus(
                    "stopped", response.position_ms, response.duration_ms, expected
                )
                self._last_valid_status = self.status
                return True
            except (VLCError, OSError, RuntimeError):
                return False

    async def stop(self, stop_vlc: bool = True) -> None:
        async with self._transition_lock:
            self._running = False
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
            self._reset_playback_evidence()
            self._generation += 1
            if stop_vlc:
                await self.process.stop()
            elif self.client:
                await self.client.close()
            self.client = None
            self._invalidate_playlist_window()
            self.queue.invalidate_undo()
            current = self.queue.current()
            if current is not None and current.state in {"playing", "paused"}:
                await self._run_database_operation(
                    lambda: self.queue.set_current_state(current.id, "stopped")
                )
            self.status = VLCStatus("unavailable")
