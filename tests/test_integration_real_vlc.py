from __future__ import annotations

import asyncio
import os
import shutil
import struct
import subprocess
import wave
from pathlib import Path

import pytest

from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.queue import QueueService
from vlcq.subtitles import FFProbeAdapter, SubtitleChoice, SubtitleDiscovery
from vlcq.vlc import VLCError, VLCProcess


def _ebml_id(value: int) -> bytes:
    return value.to_bytes((value.bit_length() + 7) // 8, "big")


def _ebml_size(value: int) -> bytes:
    for width in range(1, 9):
        if value < (1 << (7 * width)) - 1:
            return ((1 << (7 * width)) | value).to_bytes(width, "big")
    raise ValueError("temporary fixture is too large")


def _ebml_element(identifier: int, value: object) -> bytes:
    if isinstance(value, int):
        value = value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")
    elif isinstance(value, float):
        value = struct.pack(">d", value)
    elif isinstance(value, str):
        value = value.encode()
    elif not isinstance(value, bytes):
        raise TypeError("unsupported EBML value")
    return _ebml_id(identifier) + _ebml_size(len(value)) + value


def _write_generated_multitrack_mkv(path: Path) -> None:
    """Create disposable PCM plus two semantic subtitle tracks without user media."""
    ebml = b"".join(
        (
            _ebml_element(0x4286, 1),
            _ebml_element(0x42F7, 1),
            _ebml_element(0x42F2, 4),
            _ebml_element(0x42F3, 8),
            _ebml_element(0x4282, "matroska"),
            _ebml_element(0x4287, 4),
            _ebml_element(0x4285, 2),
        )
    )
    info = _ebml_element(
        0x1549A966,
        _ebml_element(0x2AD7B1, 1_000_000)
        + _ebml_element(0x4D80, "vlcq-real-vlc-probe")
        + _ebml_element(0x5741, "vlcq-real-vlc-probe"),
    )

    def subtitle_entry(number: int, uid: int, title: str, *, forced: bool = False) -> bytes:
        fields = (
            _ebml_element(0xD7, number)
            + _ebml_element(0x73C5, uid)
            + _ebml_element(0x83, 17)
            + _ebml_element(0x88, 0)
            + _ebml_element(0x536E, title)
            + _ebml_element(0x22B59C, "eng")
            + _ebml_element(0x86, "S_TEXT/UTF8")
        )
        if forced:
            fields += _ebml_element(0x55AA, 1)
        return _ebml_element(0xAE, fields)

    audio_fields = (
        _ebml_element(0xD7, 1)
        + _ebml_element(0x73C5, 101)
        + _ebml_element(0x83, 2)
        + _ebml_element(0x88, 1)
        + _ebml_element(0x86, "A_PCM/INT/LIT")
        + _ebml_element(0x536E, "Generated audio")
        + _ebml_element(
            0xE1,
            _ebml_element(0xB5, 8_000.0)
            + _ebml_element(0x9F, 1)
            + _ebml_element(0x6264, 16),
        )
    )
    tracks = _ebml_element(
        0x1654AE6B,
        _ebml_element(0xAE, audio_fields)
        + subtitle_entry(2, 202, "English Full Dialogue")
        + subtitle_entry(3, 303, "English Signs Songs", forced=True),
    )
    blocks = []
    for index in range(1_500):
        blocks.append(
            _ebml_element(
                0xA3,
                b"\x81" + struct.pack(">hB", index * 20, 0) + b"\0\0" * 160,
            )
        )
    blocks.extend(
        (
            _ebml_element(0xA3, b"\x82\0\0\x00Hello generated full dialogue"),
            _ebml_element(0xA3, b"\x83\0\0\x00[MUSIC] generated signs"),
        )
    )
    cluster = _ebml_element(0x1F43B675, _ebml_element(0xE7, 0) + b"".join(blocks))
    segment = info + tracks + cluster
    path.write_bytes(
        _ebml_element(0x1A45DFA3, ebml)
        + _ebml_id(0x18538067)
        + _ebml_size(len(segment))
        + segment
    )


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


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("VLCQ_REAL_VLC") != "1",
    reason="set VLCQ_REAL_VLC=1 for installed VLC 3 subtitle smoke test",
)
async def test_real_vlc_generated_embedded_sidecar_discovery_selection_off(
    tmp_path: Path,
) -> None:
    """Exercise offline discovery and VLC 3 sidecar attachment on disposable media."""
    executable = Path("/Applications/VLC.app/Contents/MacOS/VLC")
    if not executable.is_file():
        pytest.fail("VLCQ_REAL_VLC=1 requires an installed VLC 3 application")
    ffmpeg = shutil.which("ffmpeg")
    ffprobe = shutil.which("ffprobe")
    if ffmpeg is None or ffprobe is None:
        pytest.fail("VLCQ_REAL_VLC=1 requires ffmpeg and ffprobe")
    media = tmp_path / "GeneratedShow" / "Episode01.mkv"
    media.parent.mkdir(parents=True)
    embedded = media.with_name("embedded.srt")
    embedded.write_text("1\n00:00:00,000 --> 00:00:02,000\nembedded dialogue\n")
    sidecar = media.with_name("Episode01.en.whisper.srt")
    sidecar.write_text("1\n00:00:00,000 --> 00:00:02,000\nsidecar dialogue\n")
    result = await asyncio.to_thread(
        subprocess.run,
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=black:s=320x240:r=10",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=8000:cl=mono",
            "-i",
            str(embedded),
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-map",
            "2:0",
            "-t",
            "8",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-c:s",
            "subrip",
            "-metadata:s:s:0",
            "language=eng",
            "-metadata:s:s:0",
            "title=Embedded Full Dialogue",
            str(media),
        ],
        check=False,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    database = Database(tmp_path / "subtitle-integration.sqlite3")
    queue = QueueService(database)
    queue.open(tmp_path)
    queue.add([media])
    database.set_prefer_english_subtitles(False)
    discovery = SubtitleDiscovery(FFProbeAdapter(ffprobe), root=tmp_path)
    controller = PlaybackController(
        queue,
        process=VLCProcess(executable),
        subtitle_discovery=discovery,
    )
    try:
        offline = await controller.discover_subtitles_for_path(media)
        assert any(candidate.source == "embedded" for candidate in offline.candidates)
        assert any(candidate.path == sidecar for candidate in offline.candidates)
        await controller.start()
        await controller.play_index(0)
        snapshot = await controller.discover_subtitles()
        sidecar_candidate = next(
            candidate for candidate in snapshot.candidates if candidate.path == sidecar
        )
        selected = await controller.select_subtitle(
            snapshot.target, SubtitleChoice.candidate_choice(sidecar_candidate)
        )
        assert selected.mode == "track"
        assert controller.client is not None
        assert (await controller.client.status()).path == media.resolve()
        await controller.select_subtitle(snapshot.target, SubtitleChoice.off())
        assert controller.current_subtitle_choice == SubtitleChoice.off()
        assert (await controller.client.status()).path == media.resolve()
        assert media.is_file() and sidecar.is_file()
    finally:
        await controller.stop()
        database.close()


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("VLCQ_REAL_VLC") != "1",
    reason="set VLCQ_REAL_VLC=1 for installed VLC 3 subtitle smoke test",
)
async def test_real_vlc_subtitle_enumeration_selection_and_off(tmp_path: Path) -> None:
    """Exercise the VLC 3 subtitle adapter with only a generated disposable file."""
    executable = Path("/Applications/VLC.app/Contents/MacOS/VLC")
    if not executable.is_file():
        pytest.skip("macOS VLC application is not installed")
    media = tmp_path / "generated-subtitle-probe.mkv"
    _write_generated_multitrack_mkv(media)
    database = Database(tmp_path / "subtitle-integration.sqlite3")
    queue = QueueService(database)
    queue.open(tmp_path)
    queue.add([media])
    # Do not let the default English policy consume the explicit command under test.
    database.set_prefer_english_subtitles(False)
    controller = PlaybackController(queue, process=VLCProcess(executable))
    try:
        try:
            await controller.start()
            await controller.play_index(0)
        except (OSError, TimeoutError, VLCError) as exc:
            pytest.skip(f"VLC subtitle startup unavailable in this environment: {exc}")
        snapshot = await controller.discover_subtitles()
        assert len(snapshot.tracks) >= 2
        assert {track.language for track in snapshot.tracks} >= {"en"}
        client = controller.client
        assert client is not None
        before = await client.status()
        selected = await controller.select_subtitle(
            snapshot.target, SubtitleChoice.track_choice(snapshot.tracks[0])
        )
        assert selected.track is not None
        assert controller.current_subtitle_choice == selected
        after_track = await client.status()
        assert after_track.path == before.path == media.resolve()
        await controller.select_subtitle(snapshot.target, SubtitleChoice.off())
        assert controller.current_subtitle_choice == SubtitleChoice.off()
        after_off = await client.status()
        assert after_off.path == media.resolve()
        assert queue.current() is not None and queue.current().path == media.resolve()
        assert media.is_file()
    finally:
        await controller.stop()
        database.close()
