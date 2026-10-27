from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import pytest

from vlcq.vlc import VLCError, VLCProcess


@pytest.mark.parametrize("ignore_term", [False, True])
@pytest.mark.parametrize("crash", [False, True])
def test_controller_exit_reaps_child_and_releases_lock(
    tmp_path: Path, ignore_term: bool, crash: bool
) -> None:
    ready = tmp_path / "ready"
    lock = tmp_path / "instance.lock"
    source = Path(__file__).resolve().parents[1] / "src"
    child = """
import signal, sys, time
from pathlib import Path
if sys.argv[2] == 'True':
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
Path(sys.argv[1]).touch()
time.sleep(30)
"""
    parent = """
import os, subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from vlcq.vlc import VLCProcess
owner = VLCProcess(instance_lock_path=sys.argv[2])
fd = owner._acquire_instance_lock()
subprocess.Popen([sys.executable, sys.argv[1] + '/vlcq/supervisor.py',
                  str(os.getpid()), str(fd), sys.executable, '-c',
                  sys.argv[3], sys.argv[4], sys.argv[5]],
                 pass_fds=(fd,), start_new_session=True)
time.sleep(30)
"""
    controller = subprocess.Popen(
        [sys.executable, "-c", parent, str(source), str(lock), child, str(ready), str(ignore_term)]
    )
    unrelated = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    contender = VLCProcess(instance_lock_path=lock)
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert ready.exists()
        with pytest.raises(VLCError):
            contender._acquire_instance_lock()
        if crash:
            controller.kill()
        else:
            controller.terminate()
        controller.wait(timeout=5)
        deadline = time.monotonic() + 5
        while True:
            try:
                contender._acquire_instance_lock()
                break
            except VLCError:
                if time.monotonic() >= deadline:
                    raise
                time.sleep(0.02)
        assert unrelated.poll() is None
    finally:
        contender._release_instance_lock()
        if controller.poll() is None:
            controller.kill()
        controller.wait(timeout=5)
        unrelated.terminate()
        unrelated.wait(timeout=5)
