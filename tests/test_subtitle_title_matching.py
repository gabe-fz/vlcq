from pathlib import Path

import pytest
from test_subtitle_controller import setup

from vlcq.subtitles import (
    SubtitleCandidate,
    SubtitleChoice,
    SubtitleDescriptor,
    SubtitleTrack,
    match_remembered_candidate,
    match_remembered_track,
)


def test_same_language_tracks_preserve_selected_title_through_serialization() -> None:
    candidates = tuple(
        SubtitleCandidate("embedded", "en", title, source_order=index)
        for index, title in enumerate(("Translation A", "Translation B"))
    )
    descriptor = SubtitleDescriptor.from_dict(candidates[1].descriptor().to_dict())
    assert match_remembered_candidate(descriptor, candidates) == candidates[1]
    tracks = tuple(
        SubtitleTrack(str(index + 2), "en", candidate.title, source_order=index)
        for index, candidate in enumerate(candidates)
    )
    assert match_remembered_track(descriptor, tracks) == tracks[1]
    assert tracks[1].descriptor().title == "Translation B"
    # A changed title in a later episode still permits semantic fallback.
    assert match_remembered_track(descriptor, tracks[:1]) == tracks[0]


@pytest.mark.asyncio
async def test_saved_and_explicit_candidates_select_correct_vlc_track(tmp_path: Path) -> None:
    db, queue, controller, client, video = setup(tmp_path)
    client.tracks = (
        SubtitleTrack("2", "en", "Translation A", source_order=0),
        SubtitleTrack("3", "en", "Translation B", source_order=1),
    )
    candidate = SubtitleCandidate("embedded", "en", "Translation B", source_order=1)
    db.set_remember_subtitles_by_show(True)
    db.upsert_subtitle_preference(video, candidate.descriptor(), root=queue.root)
    await controller.play_index(0)
    assert client.selected == ["3"]
    target = (await controller.discover_subtitles()).target
    selected = await controller.select_subtitle(target, SubtitleChoice.candidate_choice(candidate))
    assert client.selected == ["3", "3"]
    assert selected.track == client.tracks[1]
    assert db.show_subtitle_preference(video, root=queue.root).title == "Translation B"
    db.close()
