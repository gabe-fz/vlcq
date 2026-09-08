from __future__ import annotations

import os
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from urllib.parse import unquote, urlsplit

from .models import BrowserEntry, TreeEntry

VIDEO_EXTENSIONS = frozenset(
    {
        ".3gp",
        ".asf",
        ".avi",
        ".divx",
        ".dv",
        ".f4v",
        ".flv",
        ".m2ts",
        ".m2v",
        ".m4v",
        ".mkv",
        ".mov",
        ".mp4",
        ".mpeg",
        ".mpg",
        ".mts",
        ".ogm",
        ".ogv",
        ".ts",
        ".vob",
        ".webm",
        ".wmv",
    }
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


def _folder_entries(
    folder: Path, base: Path, *, raise_on_error: bool = False
) -> list[BrowserEntry]:
    """List one canonical folder, skipping unsafe/unreadable children."""
    entries: list[BrowserEntry] = []
    try:
        children = list(folder.iterdir())
    except OSError as exc:
        if raise_on_error:
            raise PathError("folder cannot be read") from exc
        return entries
    for child in children:
        if child.name.startswith("."):
            continue
        try:
            canonical = child.resolve(strict=True)
            confined = is_beneath(canonical, base)
            is_dir = canonical.is_dir() and confined
            is_file = canonical.is_file() and confined
        except (OSError, RuntimeError):
            continue
        supported = is_file and canonical.suffix.casefold() in VIDEO_EXTENSIONS
        if is_dir or supported:
            entries.append(BrowserEntry(canonical, child.name, is_dir, supported))
    return sorted(entries, key=lambda item: (not item.is_dir, natural_key(item.name)))


def list_folder(folder: str | Path, root: str | Path | None = None) -> list[BrowserEntry]:
    base = canonical_root(root if root is not None else folder)
    current = Path(folder).expanduser().resolve(strict=True)
    if not current.is_dir() or not is_beneath(current, base):
        raise PathError("folder is outside the library root")
    # Preserve the existing direct-browser diagnostic for an unreadable root;
    # recursive discovery treats unreadable descendants as empty and continues.
    return _folder_entries(current, base, raise_on_error=True)


def discover_tree(
    root: str | Path,
    *,
    cancel: Callable[[], bool] | None = None,
    should_cancel: Callable[[], bool] | None = None,
) -> list[TreeEntry]:
    """Recursively discover supported entries beneath ``root``.

    Discovery is deliberately synchronous so callers can run it in a worker.
    Every path is canonicalized and checked before it is yielded. Canonical
    directory/file sets prevent symlink aliases and cycles from duplicating
    content, while inaccessible descendants are simply skipped.
    """
    base = canonical_root(root)
    visited_dirs: set[Path] = {base}
    visited_files: set[Path] = set()
    result: list[TreeEntry] = []
    cancelled = cancel or should_cancel

    def visit(folder: Path, depth: int) -> None:
        if cancelled is not None and cancelled():
            return
        for entry in _folder_entries(folder, base):
            if cancelled is not None and cancelled():
                return
            canonical = entry.path
            if entry.is_dir:
                if canonical in visited_dirs:
                    continue
                visited_dirs.add(canonical)
                result.append(
                    TreeEntry(canonical, entry.name, True, False, depth=depth, parent=folder)
                )
                visit(canonical, depth + 1)
            elif canonical not in visited_files:
                visited_files.add(canonical)
                result.append(
                    TreeEntry(canonical, entry.name, False, True, depth=depth, parent=folder)
                )

    visit(base, 0)
    return result


# Descriptive aliases used by callers and tests that treat this as a scanner.
recursive_discovery = discover_tree
recursive_discover = discover_tree
list_tree = discover_tree


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
