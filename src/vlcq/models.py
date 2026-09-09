from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .progress import clamped_percentage, is_watched


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    name: str
    is_dir: bool
    supported: bool
    depth: int = 0
    parent: Path | None = None
    expanded: bool = False
    loading: bool = False


@dataclass(frozen=True)
class TreeEntry:
    """A canonical root-confined entry in the lazily displayed library tree."""

    path: Path
    name: str
    is_dir: bool
    supported: bool
    depth: int = 0
    parent: Path | None = None
    expanded: bool = False
    loading: bool = False

    @property
    def key(self) -> Path:
        return self.path


@dataclass(frozen=True)
class QueueEntry:
    id: int
    position: int
    media_id: int
    path: Path
    state: str


@dataclass(frozen=True)
class HistoryProjection:
    """Read-only playback history for the current file identity.

    ``position_ms`` is the public, monotonic furthest position.  The nullable
    ``resume_position_ms`` is the last trustworthy position and is deliberately
    separate from it.  A legacy row has no trustworthy resume position; the UI
    may use ``fallback_resume_position_ms`` with an explicit label instead.
    """

    path: Path
    media_id: int
    position_ms: int
    resume_position_ms: int | None
    duration_ms: int
    completion_observed: bool
    first_observed: str | None
    last_observed: str | None
    last_played_at: str | None

    @property
    def fallback_resume_position_ms(self) -> int | None:
        if self.resume_position_ms is None and not self.completion_observed and self.position_ms > 0:
            return self.position_ms
        return None

    @property
    def has_recorded_progress(self) -> bool:
        return self.position_ms > 0 or self.completion_observed

    @property
    def category(self) -> str:
        """Legacy storage category based only on explicit evidence."""
        if self.completion_observed:
            return "completed"
        if self.position_ms > 0:
            return "in_progress"
        return "none"

    def watched(self, threshold: int = 90) -> bool:
        return is_watched(
            self.position_ms,
            self.duration_ms,
            completion_observed=self.completion_observed,
            threshold=threshold,
        )

    def percentage(self) -> int | None:
        return clamped_percentage(self.position_ms, self.duration_ms)


@dataclass(frozen=True)
class VLCStatus:
    state: str
    position_ms: int = 0
    duration_ms: int = 0
    path: Path | None = None
    playlist_id: str | None = None

    @property
    def vlc_id(self) -> str | None:
        """Stable identity of the observed VLC playlist item, when available."""
        return self.playlist_id
