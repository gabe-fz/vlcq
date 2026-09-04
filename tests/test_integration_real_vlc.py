from __future__ import annotations

import os
from pathlib import Path

import pytest

from vlcq.vlc import VLCProcess


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("VLCQ_REAL_VLC") != "1",
    reason="set VLCQ_REAL_VLC=1 for installed VLC 3 smoke test",
)
async def test_real_vlc_authenticated_loopback_start_and_clean_stop() -> None:
    executable = Path("/Applications/VLC.app/Contents/MacOS/VLC")
    if not executable.is_file():
        pytest.skip("macOS VLC application is not installed")
    process = VLCProcess(executable)
    client = await process.start(timeout=15)
    assert process.port > 0
    status = await client.status()
    assert status.state in {"stopped", "paused", "playing", "unavailable"}
    await process.stop()
    assert process.process is None
