"""Pure playback-progress and observed-coverage helpers."""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

COVERAGE_QUALIFICATION_SECONDS = 5.0
COVERAGE_MAX_GAP_SECONDS = 5.0
# VLC 3 reports integer-second positions; allow one media second plus a
# bounded request/poll scheduling margin so a 1.0s sample interval does not
# reset on a 20-50ms HTTP timing slip.
COVERAGE_POSITION_TOLERANCE_MS = 1_250
COVERAGE_MAX_REQUEST_SECONDS = 2.0


type MillisecondRange = tuple[int, int]


def merge_intervals(ranges: Iterable[MillisecondRange]) -> tuple[MillisecondRange, ...]:
    """Return sorted disjoint half-open ranges, merging overlap and adjacency."""
    normalized = sorted((max(0, int(start)), max(0, int(end))) for start, end in ranges)
    merged: list[MillisecondRange] = []
    for start, end in normalized:
        if end <= start:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return tuple(merged)


def clipped_coverage_ms(ranges: Iterable[MillisecondRange], duration_ms: int = 0) -> int:
    """Sum unique coverage, clipping to a known duration when one exists."""
    duration = max(0, int(duration_ms))
    clipped = (
        (start, min(end, duration) if duration > 0 else end)
        for start, end in merge_intervals(ranges)
        if duration <= 0 or start < duration
    )
    return sum(max(0, end - start) for start, end in clipped)


def coverage_percentage(coverage_ms: int, duration_ms: int) -> int | None:
    """Return floor percentage; incomplete evidence can never display as 100%."""
    duration = int(duration_ms)
    if duration <= 0:
        return None
    covered = max(0, min(int(coverage_ms), duration))
    return min(100, covered * 100 // duration)


def coverage_is_watched(
    coverage_ms: int,
    duration_ms: int,
    *,
    threshold: int = 90,
) -> bool:
    """Classify using raw unique coverage rather than a rounded percentage."""
    if not 1 <= int(threshold) <= 100:
        raise ValueError("watched threshold must be an integer from 1 through 100")
    duration = int(duration_ms)
    return duration > 0 and max(0, int(coverage_ms)) * 100 >= duration * int(threshold)


def coverage_category(
    coverage_ms: int | None,
    duration_ms: int,
    *,
    threshold: int = 90,
) -> str:
    if coverage_ms is None:
        return "none"
    if coverage_is_watched(coverage_ms, duration_ms, threshold=threshold):
        return "watched"
    return "in_progress" if coverage_ms > 0 else "none"


def clamped_percentage(position_ms: int, duration_ms: int) -> int | None:
    """Return legacy furthest progress as a bounded rounded percentage."""
    duration = int(duration_ms)
    if duration <= 0:
        return None
    position = max(0, int(position_ms))
    return max(0, min(100, round(position * 100 / duration)))


furthest_percentage = clamped_percentage
watched_percentage = clamped_percentage
clamp_percentage = clamped_percentage
percent_complete = clamped_percentage


def is_watched(
    position_ms: int,
    duration_ms: int,
    *,
    completion_observed: bool = False,
    threshold: int = 90,
) -> bool:
    """Legacy classification retained for version-1 export compatibility."""
    if completion_observed:
        return True
    if not 1 <= int(threshold) <= 100:
        raise ValueError("watched threshold must be an integer from 1 through 100")
    duration = int(duration_ms)
    return duration > 0 and max(0, int(position_ms)) * 100 >= duration * int(threshold)


watched = is_watched
classify_watched = is_watched
watched_at_threshold = is_watched


def progress_category(
    position_ms: int,
    duration_ms: int,
    *,
    completion_observed: bool = False,
    threshold: int = 90,
) -> str:
    """Legacy category retained for consumers of furthest-position history."""
    if is_watched(
        position_ms,
        duration_ms,
        completion_observed=completion_observed,
        threshold=threshold,
    ):
        return "watched"
    return "in_progress" if int(position_ms) > 0 else "none"


@dataclass(frozen=True)
class PlaybackObservation:
    generation: int
    path: Path | None
    playlist_id: str | None
    state: str
    position_ms: int
    duration_ms: int
    rate: float | None
    request_started: float
    response_received: float

    @property
    def observed_at(self) -> float:
        return (self.request_started + self.response_received) / 2


@dataclass(frozen=True)
class CoverageEvidence:
    ranges: tuple[MillisecondRange, ...] = ()
    qualified: bool = False
    initialized: bool = False


class PlaybackCoverageAccumulator:
    """Conservatively qualify coherent VLC observations without sleeping."""

    def __init__(
        self,
        *,
        qualification_seconds: float = COVERAGE_QUALIFICATION_SECONDS,
        max_gap_seconds: float = COVERAGE_MAX_GAP_SECONDS,
        position_tolerance_ms: int = COVERAGE_POSITION_TOLERANCE_MS,
        max_request_seconds: float = COVERAGE_MAX_REQUEST_SECONDS,
    ) -> None:
        self.qualification_seconds = qualification_seconds
        self.max_gap_seconds = max_gap_seconds
        self.position_tolerance_ms = position_tolerance_ms
        self.max_request_seconds = max_request_seconds
        self._anchor: PlaybackObservation | None = None
        self._run_started_at: float | None = None
        self._pending: list[MillisecondRange] = []
        self._qualified = False
        self._recent_qualified = False

    @property
    def qualified(self) -> bool:
        return self._qualified

    @property
    def recent_qualified_continuity(self) -> bool:
        return self._recent_qualified

    def reset(self) -> None:
        self._anchor = None
        self._run_started_at = None
        self._pending.clear()
        self._qualified = False
        self._recent_qualified = False

    @staticmethod
    def _identity(sample: PlaybackObservation) -> tuple[int, Path | None, str | None]:
        return sample.generation, sample.path, sample.playlist_id

    def _valid_baseline(self, sample: PlaybackObservation) -> bool:
        return (
            sample.state == "playing"
            and sample.path is not None
            and sample.rate is not None
            and math.isfinite(sample.rate)
            and sample.rate > 0
            and sample.response_received >= sample.request_started
            and sample.response_received - sample.request_started <= self.max_request_seconds
        )

    def add(self, sample: PlaybackObservation) -> CoverageEvidence:
        """Consume one sample and return newly qualified played intervals."""
        if not self._valid_baseline(sample):
            self.reset()
            return CoverageEvidence()
        anchor = self._anchor
        if anchor is None:
            self._anchor = sample
            self._run_started_at = sample.observed_at
            return CoverageEvidence(initialized=True)
        elapsed = sample.observed_at - anchor.observed_at
        same = self._identity(sample) == self._identity(anchor)
        stable_rate = sample.rate == anchor.rate
        if not same or not stable_rate or elapsed <= 0 or elapsed > self.max_gap_seconds:
            self.reset()
            self._anchor = sample
            self._run_started_at = sample.observed_at
            return CoverageEvidence(initialized=True)
        delta = int(sample.position_ms) - int(anchor.position_ms)
        rate = sample.rate
        assert rate is not None
        expected = elapsed * rate * 1000
        plausible = delta > 0 and abs(delta - expected) <= self.position_tolerance_ms
        if delta == 0 and expected <= self.position_tolerance_ms:
            # VLC 3 reports integer seconds. Retain the advancing anchor so a
            # later tick is compared across the repeated quantized sample.
            return CoverageEvidence(initialized=True)
        if not plausible:
            self.reset()
            self._anchor = sample
            self._run_started_at = sample.observed_at
            return CoverageEvidence(initialized=True)
        self._pending.append((anchor.position_ms, sample.position_ms))
        self._anchor = sample
        started = self._run_started_at if self._run_started_at is not None else sample.observed_at
        if not self._qualified and sample.observed_at - started >= self.qualification_seconds:
            self._qualified = True
        self._recent_qualified = self._qualified
        if not self._qualified:
            return CoverageEvidence(initialized=True)
        accepted = merge_intervals(self._pending)
        self._pending.clear()
        return CoverageEvidence(accepted, qualified=True, initialized=True)
