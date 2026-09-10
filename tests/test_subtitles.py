from __future__ import annotations

import json
from pathlib import Path

import pytest

from vlcq.database import Database
from vlcq.subtitles import (
    SubtitleDescriptor,
    SubtitleTrack,
    choose_english_track,
    infer_show_identity,
    match_remembered_track,
    parse_subtitle_tracks,
    scoped_show_key,
)


def track(
    track_id: str,
    *,
    language: str | None = "en",
    full: bool | None = None,
    signs: bool | None = None,
    forced: bool | None = None,
    sdh: bool | None = None,
    order: int = 0,
) -> SubtitleTrack:
    return SubtitleTrack(track_id, language, None, full, signs, forced, sdh, None, order)


def test_english_ranking_is_deterministic_and_conservative() -> None:
    tracks = [
        track("1", full=False, signs=True, order=0),
        track("2", full=True, forced=True, order=1),
        track("3", full=True, sdh=True, order=2),
        track("4", full=True, order=3),
        track("5", language="fr", full=True, order=4),
    ]
    assert choose_english_track(tracks).track_id == "4"  # type: ignore[union-attr]
    assert choose_english_track([track("a", signs=True), track("b", order=1)]).track_id == "b"  # type: ignore[union-attr]
    assert choose_english_track([track("a", language=None)]) is None


def test_remembered_descriptor_matches_semantics_not_vlc_id() -> None:
    descriptor = SubtitleDescriptor("track", "en", True, None, False, True)
    candidates = [
        track("99", full=True, forced=False, sdh=True, order=2),
        track("100", full=True, forced=True, sdh=True, order=0),
        track("101", language="fr", full=True, sdh=True, order=1),
    ]
    assert match_remembered_track(descriptor, candidates).track_id == "99"  # type: ignore[union-attr]
    assert match_remembered_track(SubtitleDescriptor.off(), candidates) is None


def test_parser_preserves_unknown_traits_and_bounds_labels() -> None:
    payload = {
        "information": {
            "category": {
                "Stream 7": {
                    "Type": "Subtitle",
                    "Description": "x" * 500,
                    "Language": "not-a-language",
                }
            }
        }
    }
    parsed = parse_subtitle_tracks(payload)
    assert parsed[0].language is None
    assert parsed[0].full_dialogue is None
    assert len(parsed[0].label) <= 160


@pytest.mark.parametrize(
    ("relative", "expected"),
    [
        (Path("Example Show") / "Season 02" / "Example Show - S02E03.mkv", "example show"),
        (Path("Example Show") / "S01" / "episode-01.mkv", "example show"),
        (Path("Example Show") / "episodes" / "episode-01.mkv", "episodes"),
        (Path("Example Show S01E03.mkv"), "example show"),
        (Path("Show 03.mkv"), None),
        (Path("Episode 03.mkv"), None),
    ],
)
def test_show_identity_is_conservative(tmp_path: Path, relative: Path, expected: str | None) -> None:
    root = tmp_path / "library"
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"video")
    assert infer_show_identity(path, root) == expected


def test_show_keys_are_root_scoped_and_never_paths(tmp_path: Path) -> None:
    root = tmp_path / "library"
    path = root / "Example Show" / "Season 1" / "S01E01.mkv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    other_root = tmp_path / "other"
    other_path = other_root / "Example Show" / "Season 1" / "S01E01.mkv"
    other_path.parent.mkdir(parents=True)
    other_path.write_bytes(b"video")
    first = scoped_show_key(path, root)
    second = scoped_show_key(other_path, other_root)
    assert first is not None and second is not None and first != second
    assert str(root) not in first


def test_subtitle_preferences_migrate_restart_and_keep_queue_data(tmp_path: Path) -> None:
    root = tmp_path / "library"
    path = root / "Example Show" / "Season 1" / "S01E01.mkv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    db_path = tmp_path / "state.sqlite3"
    db = Database(db_path)
    db.set_root(root)
    db.set_remember_subtitles_by_show(True)
    db.upsert_subtitle_preference(path, SubtitleDescriptor("track", "en", True), root=root)
    assert db.show_subtitle_preference(path, root=root) == SubtitleDescriptor("track", "en", True)
    db.close()
    reopened = Database(db_path)
    assert reopened.remember_subtitles_by_show()
    assert reopened.prefer_english_subtitles()
    assert reopened.show_subtitle_preference(path, root=root) == SubtitleDescriptor("track", "en", True)
    assert reopened.connection.execute("SELECT user_version FROM pragma_user_version").fetchone()[0] == 4
    reopened.close()


def test_malformed_preference_falls_back_without_exposing_path(tmp_path: Path) -> None:
    root = tmp_path / "library"
    path = root / "Example Show" / "Season 1" / "S01E01.mkv"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"video")
    db = Database(tmp_path / "state.sqlite3")
    key = scoped_show_key(path, root)
    assert key is not None
    db.connection.execute(
        "INSERT INTO subtitle_preferences VALUES(?,?,?,?)",
        (key, 1, json.dumps({"mode": "invalid"}), "now"),
    )
    assert db.show_subtitle_preference(path, root=root) is None
    assert db.last_preference_error is not None
    assert str(root) not in db.last_preference_error
    db.close()
