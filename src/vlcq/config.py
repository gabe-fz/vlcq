from __future__ import annotations

import os
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
