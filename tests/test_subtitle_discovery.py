from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from vlcq.database import Database
from vlcq.subtitles import (
    FFProbeAdapter,
    SubtitleCandidate,
    SubtitleChoice,
    SubtitleDescriptor,
    SubtitleDiscovery,
    SubtitleDiscoveryError,
    associate_sidecars,
    choose_english_candidate,
    match_remembered_candidate,
)


class FakeProbe:
    def __init__(self) -> None:
        self.calls = 0
        self.started = asyncio.Event()
        self.release = asyncio.Event()

    async def probe(self, path: Path) -> tuple[dict[str, object], ...]:
        del path
        self.calls += 1
        self.started.set()
        if not self.release.is_set():
            await self.release.wait()
        return (
            {
                "index": 7,
                "codec_type": "subtitle",
                "codec_name": "subrip",
                "tags": {"language": "eng", "title": "Full Dialogue"},
                "disposition": {"forced": 0, "hearing_impaired": 0},
            },
        )


def media_tree(tmp_path: Path) -> tuple[Path, Path]:
    root = tmp_path / "library"
    video = root / "Example Show" / "Season 1" / "Episode 01.mkv"
    video.parent.mkdir(parents=True)
    video.write_bytes(b"media")
    return root, video


@pytest.mark.asyncio
async def test_discovery_parses_embedded_and_same_stem_sidecars_and_coalesces(
    tmp_path: Path,
) -> None:
    root, video = media_tree(tmp_path)
    (video.parent / "Episode 01.en.whisper.srt").write_text("not inspected")
    (video.parent / "Episode 01.en.whisper.idx").write_text("not inspected")
    (video.parent / "Episode 01.en.whisper.sub").write_text("not inspected")
    (video.parent / "Episode 01.en.forced.srt").write_text("not inspected")
    (video.parent / ".Episode 01.en.hidden.srt").write_text("not inspected")
    (video.parent / "Other.en.srt").write_text("not inspected")
    probe = FakeProbe()
    discovery = SubtitleDiscovery(probe, root=root)
    pending = asyncio.gather(discovery.discover(video), discovery.discover(video))
    await asyncio.wait_for(probe.started.wait(), 1)
    probe.release.set()
    first, second = await pending
    assert first == second
    assert probe.calls == 1
    assert [(item.source, item.sidecar_variant) for item in first] == [
        ("embedded", None),
        ("sidecar", None),
        ("sidecar", "whisper"),
        ("sidecar", "whisper"),
    ]
    assert any(item.path is not None and item.path.suffix == ".idx" for item in first)
    assert all(item.sidecar_identity is not None for item in first if item.source == "sidecar")
    assert first[1].forced is True and first[1].full_dialogue is False
    assert not any("Episode 01.en.hidden" in str(item.path) for item in first)


@pytest.mark.asyncio
async def test_discovery_cache_invalidates_on_directory_mutation_and_root_change(
    tmp_path: Path,
) -> None:
    root, video = media_tree(tmp_path)
    probe = FakeProbe()
    probe.release.set()
    discovery = SubtitleDiscovery(probe, root=root)
    await discovery.discover(video)
    await discovery.discover(video)
    assert probe.calls == 1
    sidecar = video.with_name("Episode 01.en.auto.srt")
    sidecar.write_text("sidecar")
    await discovery.discover(video)
    assert probe.calls == 2
    discovery.invalidate()
    await discovery.discover(video)
    assert probe.calls == 3


@pytest.mark.asyncio
async def test_discovery_failure_is_bounded_and_does_not_guess_sidecars(tmp_path: Path) -> None:
    root, video = media_tree(tmp_path)
    (video.with_name("Episode 01.en.srt")).write_text("sidecar")
    adapter = FFProbeAdapter(executable=tmp_path / "missing-ffprobe")
    discovery = SubtitleDiscovery(adapter, root=root)
    with pytest.raises(SubtitleDiscoveryError, match="ffprobe"):
        await discovery.discover(video)


def test_associate_sidecars_confines_symlinks_and_pairs_vobsub(tmp_path: Path) -> None:
    root, video = media_tree(tmp_path)
    sibling = video.with_name("Episode 01.en.srt")
    sibling.write_text("contents are never read")
    outside = tmp_path / "outside.srt"
    outside.write_text("outside")
    escaping = video.with_name("Episode 01.fr.srt")
    escaping.symlink_to(outside)
    (video.with_name("Episode 01.en.idx")).write_text("idx")
    (video.with_name("Episode 01.en.sub")).write_text("sub")
    assert associate_sidecars(video, root) == (video.with_name("Episode 01.en.idx"), sibling)


def test_candidate_matching_prefers_source_variant_and_known_traits() -> None:
    candidates = (
        SubtitleCandidate("embedded", "en", full_dialogue=True, source_order=0),
        SubtitleCandidate(
            "sidecar",
            "en",
            full_dialogue=True,
            sidecar_variant="whisper",
            path=Path("/tmp/whisper.srt"),
            source_order=1,
        ),
    )
    descriptor = SubtitleDescriptor(
        "track", "en", True, None, None, None, 2, "sidecar", "whisper"
    )
    assert match_remembered_candidate(descriptor, candidates) == candidates[1]
    assert choose_english_candidate(candidates) == candidates[0]
    assert SubtitleChoice.candidate_choice(candidates[1]).descriptor() == descriptor


def test_version_one_descriptor_is_source_agnostic_and_v2_has_no_path() -> None:
    old = SubtitleDescriptor.from_dict(
        {"version": 1, "mode": "track", "language": "en", "full_dialogue": True}
    )
    assert old.version == 1 and old.source is None
    descriptor = SubtitleDescriptor("track", "en", True, version=2, source="sidecar", sidecar_variant="auto")
    encoded = json.dumps(descriptor.to_dict())
    assert "/" not in encoded and "auto" in encoded


def test_database_persists_v2_semantics_without_media_paths(tmp_path: Path) -> None:
    root, video = media_tree(tmp_path)
    db_path = tmp_path / "state.sqlite3"
    db = Database(db_path)
    db.set_root(root)
    db.set_remember_subtitles_by_show(True)
    descriptor = SubtitleDescriptor("track", "en", True, version=2, source="sidecar", sidecar_variant="whisper")
    assert db.upsert_subtitle_preference(video, descriptor, root=root)
    row = db.connection.execute("SELECT descriptor_version, descriptor_json FROM subtitle_preferences").fetchone()
    assert row[0] == 2 and str(root) not in row[1]
    db.close()
    reopened = Database(db_path)
    assert reopened.show_subtitle_preference(video, root=root) == descriptor
    reopened.close()


@pytest.mark.asyncio
async def test_ffprobe_rejects_nonzero_malformed_excess_and_timeout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "video.mkv"
    video.write_bytes(b"media")

    class Process:
        returncode: int | None = None

        def __init__(self, stdout_data: bytes, returncode: int | None = 0, *, hang: bool = False) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.returncode = returncode
            if not hang:
                self.stdout.feed_data(stdout_data)
                self.stdout.feed_eof()
                self.stderr.feed_eof()

        async def wait(self) -> int:
            if self.returncode is None:
                await asyncio.sleep(10)
            return self.returncode or 0

        def terminate(self) -> None:
            self.returncode = -15

        def kill(self) -> None:
            self.returncode = -9

    async def run_case(data: bytes, returncode: int | None = 0, *, hang: bool = False) -> None:
        async def create(*args: object, **kwargs: object) -> Process:
            del args, kwargs
            return Process(data, returncode, hang=hang)

        monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
        with pytest.raises(SubtitleDiscoveryError):
            await FFProbeAdapter(executable="ffprobe", timeout=0.01, output_limit=1024).probe(video)

    await run_case(b"{}", 1)
    await run_case(b"not-json")
    await run_case(b"x" * 2048)
    await run_case(b"", None, hang=True)


@pytest.mark.asyncio
async def test_ffprobe_argument_vector_uses_canonical_path_and_no_shell(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    video = tmp_path / "-leading dash [test].mkv"
    video.write_bytes(b"media")
    calls: list[tuple[object, ...]] = []

    class Process:
        returncode: int | None = None

        def __init__(self) -> None:
            self.stdout = asyncio.StreamReader()
            self.stderr = asyncio.StreamReader()
            self.stdout.feed_data(b'{"streams": []}')
            self.stdout.feed_eof()
            self.stderr.feed_eof()

        async def wait(self) -> int:
            self.returncode = 0
            return 0

        def terminate(self) -> None:
            self.returncode = 0

        def kill(self) -> None:
            self.returncode = -9

    async def create(*args: object, **kwargs: object) -> Process:
        calls.append(args)
        assert kwargs["stdout"] is asyncio.subprocess.PIPE
        assert kwargs["stderr"] is asyncio.subprocess.PIPE
        return Process()

    monkeypatch.setattr(asyncio, "create_subprocess_exec", create)
    streams = await FFProbeAdapter(executable="ffprobe").probe(video)
    assert streams == ()
    assert calls and calls[0][0] == "ffprobe"
    assert calls[0][-2:] == ("-i", str(video.resolve()))
    assert "shell" not in calls[0]
