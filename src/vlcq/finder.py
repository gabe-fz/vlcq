from __future__ import annotations

from pathlib import Path

from .paths import PathError, canonical_root, common_root, validate_video


def resolve_handoff(values: list[str]) -> tuple[Path, list[Path]]:
    if not values:
        raise PathError("select a folder or one or more videos")
    paths = [Path(value).expanduser() for value in values]
    directories = [path for path in paths if path.is_dir()]
    if directories:
        if len(paths) != 1:
            raise PathError("a folder must be opened separately from video selections")
        return canonical_root(directories[0]), []
    root = common_root(paths)
    return root, [validate_video(path, root) for path in paths]
