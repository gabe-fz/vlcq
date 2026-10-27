"""Private VLC lifetime supervisor; only signals its own unreaped child."""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time


def main() -> int:
    parent_pid, lock_fd = map(int, sys.argv[1:3])
    stopping = False

    def stop(_signal: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    for signum in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(signum, stop)
    # Do not launch anything if the controller died before we initialized.
    if os.getppid() != parent_pid or stopping:
        return 0
    child = subprocess.Popen(sys.argv[3:], pass_fds=(lock_fd,))
    try:
        while child.poll() is None:
            if stopping or os.getppid() != parent_pid:
                break
            time.sleep(0.05)
    finally:
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=1)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
    return child.returncode or 0


if __name__ == "__main__":
    sys.exit(main())
