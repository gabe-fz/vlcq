from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from .config import database_path
from .database import Database
from .finder import resolve_handoff
from .ipc import ControllerBusy, ControllerLock
from .paths import PathError, canonical_root, common_root, validate_video
from .queue import QueueService
from .tui import VLCQApp


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(
        prog="vlcq", description="Folder-first deterministic VLC queue"
    )
    result.add_argument("--database", type=Path, default=None, help=argparse.SUPPRESS)
    sub = result.add_subparsers(dest="command", required=True)
    for command in ("play", "add"):
        item = sub.add_parser(command, help=f"{command} a folder or explicit videos")
        item.add_argument("paths", nargs="+", type=Path)
        item.add_argument(
            "--no-vlc", action="store_true", help="do not launch VLC (testing/offline browsing)"
        )
    sub.add_parser("resume", help="resume the most recent queue").add_argument(
        "--no-vlc", action="store_true"
    )
    progress = sub.add_parser("progress", help="export playback progress")
    progress.add_argument("--root", required=True, type=Path)
    progress.add_argument("--json", action="store_true", required=True)
    finder = sub.add_parser(
        "finder-handoff", help="accept Finder/Automator folder or file selections"
    )
    finder.add_argument("paths", nargs="+")
    finder.add_argument("--no-vlc", action="store_true")
    return result


def _resolve_paths(values: list[Path]) -> tuple[Path, list[Path]]:
    if len(values) == 1 and values[0].is_dir():
        return canonical_root(values[0]), []
    if any(value.is_dir() for value in values):
        raise PathError("open a folder separately from explicit video files")
    root = common_root(values)
    return root, [validate_video(value, root) for value in values]


def main(argv: Sequence[str] | None = None) -> int:
    args = parser().parse_args(argv)
    db_path: Path = args.database if args.database is not None else database_path()
    try:
        if args.command == "progress":
            progress_root = canonical_root(args.root)
            database = Database(db_path)
            try:
                document = {
                    "version": 1,
                    "root": str(progress_root),
                    "records": database.export_progress(progress_root),
                }
                print(json.dumps(document, indent=2, sort_keys=True))
            finally:
                database.close()
            return 0

        lock_path = db_path.with_suffix(".lock")
        with ControllerLock(lock_path):
            database = Database(db_path)
            try:
                root: Path
                selected: list[Path]
                if args.command == "resume":
                    saved_root = database.get_root()
                    if saved_root is None or not saved_root.is_dir():
                        raise PathError("there is no resumable library root")
                    root = saved_root
                    selected = []
                elif args.command == "finder-handoff":
                    root, selected = resolve_handoff(args.paths)
                else:
                    root, selected = _resolve_paths(args.paths)
                queue = QueueService(database)
                queue.open(root)
                if selected:
                    queue.add(selected)
                app = VLCQApp(
                    root=root,
                    database=database,
                    no_vlc=args.no_vlc,
                    autoplay=args.command == "play" and bool(selected),
                )
                app.run()
            finally:
                database.close()
        return 0
    except (
        PathError,
        ControllerBusy,
        OSError,
        ValueError,
        RuntimeError,
        sqlite3.DatabaseError,
    ) as exc:
        print(f"vlcq: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
