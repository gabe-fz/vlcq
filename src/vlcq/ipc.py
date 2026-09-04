from __future__ import annotations

import fcntl
import os
from pathlib import Path
from types import TracebackType
from typing import Self, TextIO


class ControllerBusy(RuntimeError):
    pass


class ControllerLock:
    """A private advisory lock ensuring one database controller per user."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.file: TextIO | None = None

    def acquire(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if self.path.is_symlink() or self.path.parent.is_symlink():
            raise ControllerBusy("refusing a symlinked controller lock")
        descriptor = open(self.path, "a+", encoding="utf-8")  # noqa: SIM115
        os.chmod(self.path, 0o600)
        try:
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            descriptor.close()
            raise ControllerBusy("another vlcq controller is already running") from exc
        descriptor.seek(0)
        descriptor.truncate()
        descriptor.write(str(os.getpid()))
        descriptor.flush()
        self.file = descriptor

    def release(self) -> None:
        if self.file is not None:
            descriptor = self.file
            fcntl.flock(descriptor.fileno(), fcntl.LOCK_UN)
            descriptor.close()
            self.file = None

    def __enter__(self) -> Self:
        self.acquire()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self.release()
