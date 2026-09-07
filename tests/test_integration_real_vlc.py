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
    media_paths = [tmp_path / "generated-1.mp4", tmp_path / "generated-2.mp4"]
    for media in media_paths:
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
    queue.add(media_paths)
    controller = PlaybackController(queue, process=VLCProcess(executable))
    try:
        try:
            await controller.start()
        except (OSError, TimeoutError, VLCError) as exc:
            pytest.skip(f"VLC HTTP startup unavailable in this environment: {exc}")

        # Seed only temporary history, then exercise the same policy used by
        # the CLI and TUI for a persisted resume point.
        database.set_resume_position(media_paths[0], 1_000, 3_000)
        await controller.play_with_policy(0, choice="resume")
        await asyncio.sleep(0.5)
        status = await controller.client.status() if controller.client is not None else None
        assert status is not None
        duration = status.duration_ms or 3_000
        await controller.seek_absolute(0, duration)
        progress = database.progress_for(media_paths[0])
        assert progress["position_ms"] >= 1_000
        assert progress["resume_position_ms"] == 0
        await controller.toggle_pause()
        await controller.toggle_pause()
        assert await controller.stop_playback()

        # Replaying the first temporary file should naturally advance to the
        # second without an interactive resume prompt.
        await controller.play_index(0)
        for _ in range(20):
            if queue.entries()[0].state == "completed":
                break
            await asyncio.sleep(0.5)
        assert queue.entries()[0].state == "completed"
        assert database.progress_for(media_paths[0])["completion_observed"] == 1
        active = queue.current()
        assert active is not None and active.path == media_paths[1].resolve()

        assert await controller.stop_playback()
        active_index = next(
            index for index, entry in enumerate(queue.entries()) if entry.id == active.id
        )
        queue.remove(active_index, stop_confirmed=True)
        assert media_paths[1].is_file()

        # Overlapping recovery requests share one owned lifecycle and never
        # select or autoplay the remaining queue.
        clients = await asyncio.gather(controller.reconnect(), controller.reconnect())
        assert clients == [None, None]
        assert controller.client is not None
        assert queue.current() is None
    finally:
        await controller.stop()
        database.close()
