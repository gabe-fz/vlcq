from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class BrowserEntry:
    path: Path
    name: str
    is_dir: bool
    supported: bool


@dataclass(frozen=True)
class QueueEntry:
    id: int
    position: int
    media_id: int
    path: Path
    state: str


@dataclass(frozen=True)
class VLCStatus:
    state: str
    position_ms: int = 0
    duration_ms: int = 0
    path: Path | None = None
