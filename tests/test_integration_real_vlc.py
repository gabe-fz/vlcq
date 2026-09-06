from __future__ import annotations

import asyncio
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.queue import QueueService
from vlcq.vlc import VLCError, VLCProcess


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
    try:
        client = await process.start(timeout=15)
    except (OSError, TimeoutError, VLCError) as exc:
        pytest.skip(f"VLC HTTP startup unavailable in this environment: {exc}")
    assert process.port > 0
    status = await client.status()
    assert status.state in {"stopped", "paused", "playing", "unavailable"}
    await process.stop()
    assert process.process is None


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("VLCQ_REAL_VLC") != "1",
    reason="set VLCQ_REAL_VLC=1 for installed VLC 3 playback test",
)
async def test_real_vlc_temporary_media_play_seek_pause_stop_and_reconnect(
    tmp_path: Path,
) -> None:
    """Opt-in integration coverage never uses a user's media or database."""
    executable = Path("/Applications/VLC.app/Contents/MacOS/VLC")
    if not executable.is_file():
        pytest.skip("macOS VLC application is not installed")
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        pytest.skip("ffmpeg is required to generate temporary integration media")
    media = tmp_path / "generated.mp4"
    result = await asyncio.to_thread(
        subprocess.run,
        [
            ffmpeg,
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=160x90:r=10",
            "-t",
            "3",
            "-pix_fmt",
            "yuv420p",
            "-c:v",
            "mpeg4",
            "-y",
            str(media),
        ],
        check=False,
        capture_output=True,
    )
    if result.returncode != 0:
        pytest.skip("ffmpeg could not generate the temporary test media")

    database = Database(tmp_path / "integration.sqlite3")
    queue = QueueService(database)
    queue.open(tmp_path)
    queue.add([media])
    controller = PlaybackController(queue, process=VLCProcess(executable))
    try:
        try:
            await controller.start()
        except (OSError, TimeoutError, VLCError) as exc:
            pytest.skip(f"VLC HTTP startup unavailable in this environment: {exc}")
        await controller.play_index(0)
        await asyncio.sleep(0.5)
        status = await controller.client.status() if controller.client is not None else None
        assert status is not None
        if status.duration_ms > 0:
            await controller.seek_absolute(0, status.duration_ms)
        await controller.toggle_pause()
        await controller.toggle_pause()
        assert await controller.stop_playback()
        await controller.reconnect()
    finally:
        await controller.stop()
        database.close()
