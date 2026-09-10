from __future__ import annotations

import os
import shutil
from pathlib import Path


def app_dir() -> Path:
    override = os.environ.get("VLCQ_HOME")
    path = (
        Path(override).expanduser()
        if override
        else Path.home() / "Library" / "Application Support" / "vlcq"
    )
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    return path


def database_path() -> Path:
    return app_dir() / "vlcq.sqlite3"


def ffprobe_path() -> Path | None:
    """Resolve the local FFmpeg inspector once from trusted configuration."""
    override = os.environ.get("VLCQ_FFPROBE")
    value = override if override else shutil.which("ffprobe")
    return Path(value).expanduser() if value else None


def ffprobe_diagnostic() -> tuple[bool, str]:
    executable = ffprobe_path()
    if executable is None:
        return False, "ffprobe is unavailable; install FFmpeg with: brew install ffmpeg"
    if not executable.is_file():
        return False, "configured ffprobe is not a regular executable; set VLCQ_FFPROBE or install FFmpeg"
    return True, f"ffprobe ready: {executable.name}"


def resolve_watched_percent(value: str | int | None = None) -> int:
    """Resolve and validate the derived watched-history threshold.

    The environment is intentionally the only user-facing configuration
    surface until vlcq has a settings UI.  Invalid values fail before command
    startup can open or mutate the database.
    """
    raw: str | int = os.environ.get("VLCQ_WATCHED_PERCENT", "90") if value is None else value
    text = str(raw).strip()
    if not text or not text.isdecimal() or (len(text) > 1 and text.startswith("0")):
        raise ValueError("VLCQ_WATCHED_PERCENT must be a whole percentage from 1 through 100")
    result = int(text)
    if not 1 <= result <= 100:
        raise ValueError("VLCQ_WATCHED_PERCENT must be a whole percentage from 1 through 100")
    return result


# Short aliases are useful to callers that treat this as a normal setting
# resolver rather than an environment-specific implementation detail.
watched_percent = resolve_watched_percent
watched_threshold = resolve_watched_percent
get_watched_percent = resolve_watched_percent
resolve_watched_threshold = resolve_watched_percent
