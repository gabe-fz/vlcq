"""Pure playback-progress classification helpers.

The database stores observations; this module owns the derived presentation
policy used by the CLI, queue, controller, and TUI.
"""

from __future__ import annotations


def clamped_percentage(position_ms: int, duration_ms: int) -> int | None:
    """Return furthest progress as a whole percentage.

    ``None`` means that VLC did not provide a known positive duration.  The
    position is clamped to a non-negative value and the resulting percentage
    is bounded to 0..100, so a stale overrun can never produce an invalid UI
    width or export value.
    """
    duration = int(duration_ms)
    if duration <= 0:
        return None
    position = max(0, int(position_ms))
    return max(0, min(100, round(position * 100 / duration)))


# More explicit aliases make the policy convenient for integrations and keep
# callers from having to infer whether the value refers to furthest progress.
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
    """Classify an item without mutating its stored completion evidence.

    Explicit completion is authoritative.  Threshold classification is only
    possible for a known positive duration and uses the unclamped position so
    an overrun remains watched.  The threshold is validated at configuration
    boundaries; the range check here keeps direct library callers safe.
    """
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
    """Return the stable UI category used by filters and badges."""
    if is_watched(
        position_ms,
        duration_ms,
        completion_observed=completion_observed,
        threshold=threshold,
    ):
        return "watched"
    if int(position_ms) > 0:
        return "in_progress"
    return "none"
