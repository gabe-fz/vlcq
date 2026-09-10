from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest

from vlcq.controller import PlaybackController
from vlcq.database import Database
from vlcq.models import VLCStatus
from vlcq.queue import QueueService
from vlcq.subtitles import (
    SubtitleChoice,
    SubtitleDescriptor,
    SubtitleDiscovery,
    SubtitleTrack,
)
from vlcq.vlc import VLCError, VLCPlaylistItem


class SubtitleClient:
    def __init__(self, tracks: tuple[SubtitleTrack, ...]) -> None:
        self.tracks = tracks
        self.playlist_items: list[VLCPlaylistItem] = []
        self.selected: list[str | None] = []
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.delay = False

    async def replace_playlist(self, path: Path) -> VLCStatus:
        item = VLCPlaylistItem("vlc-1", path.resolve())
        self.playlist_items = [item]
        return VLCStatus("playing", path=item.path, playlist_id=item.playlist_id)

    async def playlist(self) -> list[VLCPlaylistItem]:
        return list(self.playlist_items)

    async def enqueue(self, path: Path) -> VLCStatus:
        item = VLCPlaylistItem("vlc-2", path.resolve())
        self.playlist_items.append(item)
        return VLCStatus("playing", path=self.playlist_items[0].path, playlist_id="vlc-1")

    async def remove(self, playlist_id: str) -> VLCStatus:
        self.playlist_items = [item for item in self.playlist_items if item.playlist_id != playlist_id]
        return VLCStatus("playing", path=self.playlist_items[0].path if self.playlist_items else None)

    async def subtitle_tracks(self) -> tuple[SubtitleTrack, ...]:
        if self.delay:
            self.started.set()
            await self.release.wait()
        return self.tracks

    async def select_subtitle(self, track_id: str | None) -> VLCStatus:
        self.selected.append(track_id)
        item = self.playlist_items[0]
        return VLCStatus("playing", path=item.path, playlist_id=item.playlist_id)

    async def close(self) -> None:
        pass


class Process:
    def __init__(self, client: SubtitleClient) -> None:
        self.client = client

    async def start(self) -> SubtitleClient:
        return self.client

    async def stop(self) -> None:
        pass


def setup(tmp_path: Path) -> tuple[Database, QueueService, PlaybackController, SubtitleClient, Path]:
    root = tmp_path / "library"
    video = root / "Example Show" / "Season 1" / "S01E01.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"video")
    db = Database(tmp_path / "state.sqlite3")
    queue = QueueService(db)
    queue.open(root)
    queue.add([video])
    client = SubtitleClient(
        (
            SubtitleTrack("1", "en", "English Full Dialogue", True, False, False, False, None, 0),
            SubtitleTrack("2", "en", "English Signs Songs", False, True, False, False, None, 1),
            SubtitleTrack("3", "fr", "Français", True, False, False, False, None, 2),
        )
    )
    controller = PlaybackController(queue, process=Process(client))  # type: ignore[arg-type]
    controller.client = client  # type: ignore[assignment]
    return db, queue, controller, client, video


@pytest.mark.asyncio
async def test_manual_subtitle_choice_is_generation_bound_and_does_not_move_playback(
    tmp_path: Path,
) -> None:
    db, queue, controller, client, video = setup(tmp_path)
    await controller.play_index(0)
    client.selected.clear()
    target = (await controller.discover_subtitles()).target
    before = [(entry.id, entry.position, entry.state) for entry in queue.entries()]
    selected = await controller.select_subtitle(target, SubtitleChoice.off())
    assert selected.mode == "off"
    assert client.selected == [None]
    assert [(entry.id, entry.position, entry.state) for entry in queue.entries()] == before
    assert db.show_subtitle_preference(video, root=queue.root) is None
    db.set_remember_subtitles_by_show(True)
    target = (await controller.discover_subtitles()).target
    await controller.select_subtitle(target, SubtitleChoice.off())
    assert db.show_subtitle_preference(video, root=queue.root) == SubtitleDescriptor.off()
    db.close()


@pytest.mark.asyncio
async def test_automatic_precedence_remembered_off_and_english_fallback(tmp_path: Path) -> None:
    db, queue, controller, client, video = setup(tmp_path)
    db.set_remember_subtitles_by_show(True)
    db.upsert_subtitle_preference(video, SubtitleDescriptor("track", "fr", True), root=queue.root)
    await controller.play_index(0)
    assert client.selected == ["3"]

    controller._generation += 1
    db.upsert_subtitle_preference(video, SubtitleDescriptor.off(), root=queue.root)
    choice = await controller.apply_automatic_subtitles()
    assert choice is not None and choice.mode == "off"
    assert client.selected[-1] is None
    db.close()


@pytest.mark.asyncio
async def test_stale_subtitle_target_is_rejected_without_a_command(tmp_path: Path) -> None:
    db, _queue, controller, client, _video = setup(tmp_path)
    db.set_prefer_english_subtitles(False)
    await controller.play_index(0)
    target = (await controller.discover_subtitles()).target
    controller._generation += 1

    with pytest.raises(VLCError, match="expired because playback changed"):
        await controller.select_subtitle(target, SubtitleChoice.off())

    assert client.selected == []
    db.close()


@pytest.mark.asyncio
async def test_reported_active_state_must_confirm_subtitle_command(tmp_path: Path) -> None:
    db, _queue, controller, client, _video = setup(tmp_path)
    db.set_prefer_english_subtitles(False)
    await controller.play_index(0)
    target = (await controller.discover_subtitles()).target
    client.tracks = tuple(
        replace(track, active=track.track_id == "2") for track in client.tracks
    )

    with pytest.raises(VLCError, match="inconsistent active subtitle metadata"):
        await controller.select_subtitle(
            target, SubtitleChoice.track_choice(client.tracks[0])
        )

    assert client.selected == ["1"]
    assert controller.status.state == "playing"
    assert db.show_subtitle_preference(target.path, root=controller.queue.root) is None
    db.close()


@pytest.mark.asyncio
async def test_inactive_choice_persists_semantically_without_vlc(tmp_path: Path) -> None:
    db, queue, controller, client, video = setup(tmp_path)
    sidecar = video.with_name("S01E01.en.whisper.srt")
    sidecar.write_text("not read")

    class Probe:
        async def probe(self, path: Path) -> tuple[dict[str, object], ...]:
            assert path == video.resolve()
            return ()

    # The fake probe is only needed to exercise the controller boundary; the
    # sidecar is associated from its immediate sibling name without reading it.
    controller.subtitle_discovery = SubtitleDiscovery(Probe(), root=queue.root)
    db.set_remember_subtitles_by_show(True)
    snapshot = await controller.discover_subtitles_for_path(video)
    candidate = next(item for item in snapshot.candidates if item.path == sidecar.resolve())
    before = [(entry.id, entry.position, entry.state) for entry in queue.entries()]
    selected = await controller.select_inactive_subtitle(
        snapshot.target, SubtitleChoice.candidate_choice(candidate)
    )
    assert selected.candidate == candidate
    assert client.selected == []
    assert [(entry.id, entry.position, entry.state) for entry in queue.entries()] == before
    descriptor = db.show_subtitle_preference(video, root=queue.root)
    assert descriptor is not None
    assert descriptor.source == "sidecar" and descriptor.sidecar_variant == "whisper"
    assert str(video) not in json.dumps(descriptor.to_dict())
    db.close()


@pytest.mark.asyncio
async def test_sidecar_application_revalidates_and_avoids_duplicate_attachment(tmp_path: Path) -> None:
    db, queue, controller, client, video = setup(tmp_path)
    sidecar = video.with_name("S01E01.en.whisper.srt")
    sidecar.write_text("not read")

    class Probe:
        async def probe(self, path: Path) -> tuple[dict[str, object], ...]:
            del path
            return ()

    attached: list[Path] = []

    async def add_subtitle(path: Path) -> VLCStatus:
        attached.append(path)
        client.tracks = (*client.tracks, SubtitleTrack("4", "en", None, None, None, None, None, None, 3))
        return VLCStatus("playing", path=video.resolve(), playlist_id="vlc-1")

    client.add_subtitle = add_subtitle  # type: ignore[attr-defined]
    controller.subtitle_discovery = SubtitleDiscovery(Probe(), root=queue.root)
    db.set_prefer_english_subtitles(False)
    await controller.play_index(0)
    snapshot = await controller.discover_subtitles()
    candidate = next(item for item in snapshot.candidates if item.path == sidecar.resolve())
    selected = await controller.select_subtitle(
        snapshot.target, SubtitleChoice.candidate_choice(candidate)
    )
    assert selected.track is not None and selected.track.track_id == "4"
    assert attached == [sidecar.resolve()]
    client.tracks = tuple(track for track in client.tracks if track.track_id != "4")
    sidecar.write_text("changed")
    with pytest.raises(VLCError, match="sidecar changed"):
        await controller.select_subtitle(
            snapshot.target, SubtitleChoice.candidate_choice(candidate)
        )
    assert attached == [sidecar.resolve()]
    db.close()


@pytest.mark.asyncio
async def test_delayed_automatic_selection_cannot_override_manual_choice(tmp_path: Path) -> None:
    db, _queue, controller, client, _video = setup(tmp_path)
    await controller.play_index(0)
    client.selected.clear()
    controller._generation += 1
    target = (await controller.discover_subtitles()).target
    client.delay = True
    automatic = asyncio.create_task(controller.apply_automatic_subtitles())
    await asyncio.wait_for(client.started.wait(), 1)
    manual = asyncio.create_task(controller.select_subtitle(target, SubtitleChoice.off()))
    await asyncio.sleep(0)
    client.release.set()
    await asyncio.wait_for(manual, 1)
    await asyncio.wait_for(automatic, 1)
    assert client.selected == [None]
    db.close()
