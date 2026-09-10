from __future__ import annotations

import asyncio
import os
import subprocess
import wave
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
    media_paths = [tmp_path / f"generated-{index}.mp4" for index in range(1, 5)]
    for index, media in enumerate(media_paths):
        source = tmp_path / f"source-{index}.wav"
        with wave.open(str(source), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(8_000)
            # Give the first item enough headroom for the controller's initial
            # idle poll plus the five-second qualification window. The later
            # items stay short so natural-successor checks remain bounded.
            seconds = 20 if index == 0 else 12
            output.writeframes(b"\0\0" * 8_000 * seconds)
        encoded = media.with_suffix(".m4a")
        result = await asyncio.to_thread(
            subprocess.run,
            [
                "/usr/bin/avconvert",
                "--source",
                str(source),
                "--preset",
                "PresetAppleM4A",
                "--output",
                str(encoded),
                "--replace",
            ],
            check=False,
            capture_output=True,
        )
        if result.returncode != 0:
            pytest.fail("macOS avconvert could not generate temporary integration media")
        encoded.replace(media)

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

        # Exercise persisted resume, real status rate/timing, and normal
        # five-second qualification using only generated temporary media.
        database.set_resume_position(media_paths[0], 1_000, 20_000)
        await controller.play_with_policy(0, choice="resume")
        await asyncio.sleep(0.5)
        client = controller.client
        assert client is not None
        status = await client.status()
        assert status.rate is not None and status.rate > 0
        assert status.request_started is not None
        assert status.response_received is not None
        duration = status.duration_ms or 20_000
        # Startup can leave the polling loop in its idle backoff briefly. Wait
        # for the observable qualification result rather than racing exactly
        # five seconds from the first controller-owned sample.
        for _ in range(20):
            normal = database.progress_for(media_paths[0])
            if (normal["coverage_ms"] or 0) >= 3_000:
                break
            await asyncio.sleep(0.5)
        await controller.toggle_pause()
        normal = database.progress_for(media_paths[0])
        assert normal["resume_position_ms"] > 0
        assert 3_000 <= normal["coverage_ms"] < duration

        # Replay mostly overlapping material. Union growth is bounded by only
        # the newly reached edge, never by the full replayed elapsed interval.
        await controller.seek_absolute(2_000, duration)
        await controller.toggle_pause()
        await asyncio.sleep(6.2)
        await controller.toggle_pause()
        overlap = database.progress_for(media_paths[0])
        assert normal["coverage_ms"] <= overlap["coverage_ms"]
        assert overlap["coverage_ms"] <= normal["coverage_ms"] + 2_500

        # A real external rate change invalidates pending evidence; subsequent
        # coherent observations use VLC's reported finite positive rate.
        await client.set_rate(1.5)
        await controller.seek_absolute(2_000, duration)
        await controller.toggle_pause()
        await asyncio.sleep(5.5)
        variable_status = await client.status()
        assert variable_status.rate == pytest.approx(1.5, rel=0.1)
        await controller.toggle_pause()
        # Restore normal rate so the short successor has a full five-second
        # qualification window before its natural end.
        await client.set_rate(1.0)

        # Seeking near the end and playing only a brief tail may transition to
        # the successor, but cannot force full coverage or completion.
        await controller.seek_absolute(max(0, duration - 800), duration)
        await controller.toggle_pause()
        for _ in range(12):
            if queue.current() is not None and queue.current().path == media_paths[1].resolve():
                break
            await asyncio.sleep(0.5)
        first_progress = database.progress_for(media_paths[0])
        assert queue.entries()[0].state == "skipped"
        assert first_progress["completion_observed"] == 0
        assert first_progress["coverage_ms"] < duration

        # Let the second item advance naturally. Its qualified recent
        # continuity—not a single near-end sample—supports the queue outcome.
        for _ in range(34):
            if queue.current() is not None and queue.current().path == media_paths[2].resolve():
                break
            await asyncio.sleep(0.5)
        assert queue.current() is not None and queue.current().path == media_paths[2].resolve()
        second_progress = database.progress_for(media_paths[1])
        assert queue.entries()[1].state == "completed", second_progress
        assert second_progress["completion_observed"] == 1
        assert second_progress["coverage_ms"] >= 5_000
        assert second_progress["coverage_ms"] < duration
        await controller.synchronize_playlist()
        window = await client.playlist()
        assert [item.path for item in window] == [
            media_paths[2].resolve(),
            media_paths[3].resolve(),
        ]
        assert len(window) <= 2

        # VLC-native Next adopts exactly the staged fourth item and records no
        # completion or unseen coverage for the manually skipped third item.
        await client.command("pl_next")
        for _ in range(12):
            if queue.current() is not None and queue.current().path == media_paths[3].resolve():
                break
            await asyncio.sleep(0.5)
        assert queue.current() is not None and queue.current().path == media_paths[3].resolve()
        assert queue.entries()[2].state == "skipped"
        third_progress = database.progress_for(media_paths[2])
        assert third_progress["completion_observed"] == 0
        assert third_progress["coverage_ms"] in {None, 0}
        await controller.synchronize_playlist()
        window = await client.playlist()
        assert [item.path for item in window] == [media_paths[3].resolve()]
        assert len(window) <= 2

        assert all(path.is_file() for path in media_paths)

        # Overlapping recovery requests share one owned lifecycle and never
        # select or autoplay the persisted stopped item.
        saved_current = queue.current()
        assert saved_current is not None
        await controller.stop()
        clients = await asyncio.gather(controller.reconnect(), controller.reconnect())
        assert clients == [None, None]
        assert controller.client is not None
        assert queue.current() is not None
        assert queue.current().id == saved_current.id
        assert queue.current().state == "stopped"
    finally:
        await controller.stop()
        database.close()
