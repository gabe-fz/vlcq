from __future__ import annotations

import os
import re
from collections.abc import Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import BrowserEntry

VIDEO_EXTENSIONS = frozenset(
    {".3gp", ".avi", ".flv", ".m4v", ".mkv", ".mov", ".mp4", ".mpeg", ".mpg", ".webm", ".wmv"}
)
_NUMBERS = re.compile(r"(\d+)")


class PathError(ValueError):
    pass


def natural_key(value: str) -> tuple[object, ...]:
    return tuple(int(part) if part.isdigit() else part.casefold() for part in _NUMBERS.split(value))


def canonical_root(path: str | Path) -> Path:
    raw = Path(path).expanduser()
    try:
        result = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathError("library root does not exist") from exc
    if not result.is_dir():
        raise PathError("library root must be a directory")
    return result


def is_beneath(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def validate_video(path: str | Path, root: str | Path) -> Path:
    base = canonical_root(root)
    raw = Path(path).expanduser()
    try:
        result = raw.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathError("video does not exist") from exc
    if not result.is_file():
        raise PathError("video must be a regular file")
    if not is_beneath(result, base):
        raise PathError("video is outside the library root")
    if result.suffix.casefold() not in VIDEO_EXTENSIONS:
        raise PathError("unsupported video extension")
    return result


def common_root(paths: Sequence[str | Path]) -> Path:
    if not paths:
        raise PathError("at least one video is required")
    resolved: list[Path] = []
    for value in paths:
        try:
            item = Path(value).expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise PathError("video does not exist") from exc
        if not item.is_file():
            raise PathError("video must be a regular file")
        resolved.append(item)
    return Path(os.path.commonpath([str(item.parent) for item in resolved])).resolve()


def deduplicate_natural(paths: Sequence[Path]) -> list[Path]:
    unique = {path.resolve(): path.resolve() for path in paths}
    return sorted(unique.values(), key=lambda path: natural_key(str(path)))


def list_folder(folder: str | Path, root: str | Path | None = None) -> list[BrowserEntry]:
    base = canonical_root(root if root is not None else folder)
    current = Path(folder).expanduser().resolve(strict=True)
    if not current.is_dir() or not is_beneath(current, base):
        raise PathError("folder is outside the library root")
    entries: list[BrowserEntry] = []
    try:
        children = list(current.iterdir())
    except OSError as exc:
        raise PathError("folder cannot be read") from exc
    for child in children:
        try:
            canonical = child.resolve(strict=True)
            confined = is_beneath(canonical, base)
            is_dir = canonical.is_dir() and confined
            is_file = canonical.is_file() and confined
        except (OSError, RuntimeError):
            continue
        supported = is_file and canonical.suffix.casefold() in VIDEO_EXTENSIONS
        if is_dir or is_file:
            entries.append(BrowserEntry(canonical, child.name, is_dir, supported))
    return sorted(entries, key=lambda item: (not item.is_dir, natural_key(item.name)))


def file_uri_to_path(uri: str) -> Path:
    parsed = urlsplit(uri)
    if parsed.scheme != "file" or parsed.netloc not in ("", "localhost"):
        raise PathError("only local file URIs are accepted")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise PathError("unsafe file URI")
    if re.search(r"%(?![0-9A-Fa-f]{2})", parsed.path):
        raise PathError("malformed file URI")
    try:
        decoded = unquote(parsed.path, errors="strict")
    except (UnicodeDecodeError, ValueError) as exc:
        raise PathError("malformed file URI") from exc
    if "\x00" in decoded:
        raise PathError("NUL in file URI")
    try:
        return Path(decoded).resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise PathError("file URI target does not exist") from exc
