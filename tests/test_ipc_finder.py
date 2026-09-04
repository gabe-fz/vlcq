from __future__ import annotations

from pathlib import Path

import pytest

from vlcq.finder import resolve_handoff
from vlcq.ipc import ControllerBusy, ControllerLock
from vlcq.paths import PathError


def test_controller_lock_excludes_second_owner_and_is_private(tmp_path: Path) -> None:
    path = tmp_path / "controller.lock"
    first = ControllerLock(path)
    second = ControllerLock(path)
    first.acquire()
    try:
        with pytest.raises(ControllerBusy):
            second.acquire()
        assert path.stat().st_mode & 0o777 == 0o600
    finally:
        first.release()
    second.acquire()
    second.release()


def test_finder_handoff_folder_and_nearest_common_parent(tmp_path: Path) -> None:
    root = tmp_path / "show"
    one = root / "s1" / "e1.mkv"
    two = root / "s2" / "e2.mp4"
    one.parent.mkdir(parents=True)
    two.parent.mkdir(parents=True)
    one.write_bytes(b"1")
    two.write_bytes(b"2")
    assert resolve_handoff([str(root)]) == (root.resolve(), [])
    selected_root, selected = resolve_handoff([str(one), str(two)])
    assert selected_root == root.resolve()
    assert selected == [one.resolve(), two.resolve()]
    with pytest.raises(PathError):
        resolve_handoff([str(root), str(one)])
