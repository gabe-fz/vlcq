from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal


class SubtitleError(ValueError):
    """A subtitle payload or persisted descriptor is not safely usable."""


TriState = bool | None
SubtitleMode = Literal["off", "track"]

_MAX_LABEL_LENGTH = 160
_MAX_DESCRIPTOR_TEXT = 32

_LANGUAGE_ALIASES = {
    "eng": "en",
    "english": "en",
    "en-us": "en",
    "en-gb": "en",
    "en-au": "en",
    "fra": "fr",
    "fre": "fr",
    "french": "fr",
    "deu": "de",
    "ger": "de",
    "german": "de",
    "spa": "es",
    "spanish": "es",
    "ita": "it",
    "italian": "it",
    "por": "pt",
    "portuguese": "pt",
    "jpn": "ja",
    "japanese": "ja",
    "kor": "ko",
    "korean": "ko",
    "zho": "zh",
    "chi": "zh",
    "chinese": "zh",
    "rus": "ru",
    "russian": "ru",
}
_LANGUAGE_NAME_ALIASES = {
    "english": "en",
    "french": "fr",
    "german": "de",
    "spanish": "es",
    "italian": "it",
    "portuguese": "pt",
    "japanese": "ja",
    "korean": "ko",
    "chinese": "zh",
    "russian": "ru",
}


def _bounded_text(value: object, *, limit: int = _MAX_LABEL_LENGTH) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join("".join(character if character.isprintable() else " " for character in value).split())
    if not cleaned:
        return None
    if len(cleaned) > limit:
        return cleaned[: max(1, limit - 1)].rstrip() + "…"
    return cleaned


def _token_text(value: object) -> str:
    text = _bounded_text(value, limit=_MAX_LABEL_LENGTH)
    return text.casefold() if text is not None else ""


def normalize_language(value: object) -> str | None:
    """Return a conservative two-letter language code, or ``None``."""
    text = _bounded_text(value, limit=32)
    if text is None:
        return None
    folded = text.casefold().replace("_", "-")
    if folded in _LANGUAGE_ALIASES:
        return _LANGUAGE_ALIASES[folded]
    if re.fullmatch(r"[a-z]{2,3}(?:-[a-z]{2,4})?", folded):
        base = folded.split("-", 1)[0]
        if len(base) == 2:
            return base
        # ISO-639 three-letter codes are accepted only for aliases above. A
        # random three-letter token is not enough evidence to classify a
        # subtitle language.
        return None
    return None


def _token_present(text: str, pattern: str) -> bool:
    return re.search(pattern, text, flags=re.IGNORECASE) is not None


def _flag(value: object) -> TriState:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool) and value in {0, 1}:
        return bool(value)
    if isinstance(value, str):
        folded = value.strip().casefold()
        if folded in {"1", "true", "yes", "on", "enabled"}:
            return True
        if folded in {"0", "false", "no", "off", "disabled"}:
            return False
    return None


def _first_flag(metadata: dict[str, object], *keys: str) -> TriState:
    for key in keys:
        if key in metadata:
            parsed = _flag(metadata[key])
            if parsed is not None:
                return parsed
    return None


def _characteristics(
    metadata: dict[str, object], title: str | None
) -> tuple[TriState, TriState, TriState, TriState]:
    """Infer only recognizable traits; unknown never becomes false."""
    text = _token_text(" ".join(value for value in (title, _bounded_text(metadata.get("Codec"), limit=80)) if value))
    kind = _token_text(metadata.get("Kind"))
    characteristics = _token_text(metadata.get("Characteristics"))
    text = f"{text} {kind} {characteristics}"

    signs = _first_flag(metadata, "SignsSongs", "signs_songs", "Signs/Songs", "signs-songs")
    if signs is None and _token_present(text, r"\b(?:signs?\s*(?:[/&]|and)\s*songs?|songs?)\b"):
        signs = True

    forced = _first_flag(metadata, "Forced", "forced")
    if forced is None and _token_present(text, r"\bforced(?:[- ]only)?\b"):
        forced = True

    sdh = _first_flag(
        metadata,
        "SDH",
        "sdh",
        "HearingImpaired",
        "hearing_impaired",
        "ClosedCaptions",
        "closed_captions",
    )
    if sdh is None and _token_present(text, r"\b(?:sdh|hi|hearing[- ]impaired|closed[- ]captions?)\b"):
        sdh = True

    full = _first_flag(metadata, "FullDialogue", "full_dialogue", "Dialogue", "dialogue")
    if full is None and _token_present(text, r"\bfull[- ]?(?:dialogue|dialog)\b"):
        full = True
    elif full is None and (signs is True or forced is True):
        full = False
    return full, signs, forced, sdh


def _language(metadata: dict[str, object], title: str | None) -> str | None:
    for key in ("Language", "language", "lang", "LANGUAGE"):
        result = normalize_language(metadata.get(key))
        if result is not None:
            return result
    if title is not None:
        tokens = re.findall(r"(?<![A-Za-z])(?:en|eng|english)(?![A-Za-z])", title, re.IGNORECASE)
        if tokens:
            return "en"
        for name, code in _LANGUAGE_NAME_ALIASES.items():
            if re.search(rf"(?<![A-Za-z]){re.escape(name)}(?![A-Za-z])", title, re.IGNORECASE):
                return code
    return None


def _active(metadata: dict[str, object]) -> TriState:
    return _first_flag(metadata, "Active", "active", "Selected", "selected", "Current", "current")


@dataclass(frozen=True)
class SubtitleTrack:
    """A bounded, immutable representation of one VLC subtitle stream."""

    track_id: str
    language: str | None = None
    title: str | None = None
    full_dialogue: TriState = None
    signs_songs: TriState = None
    forced: TriState = None
    sdh: TriState = None
    active: TriState = None
    source_order: int = 0

    @property
    def id(self) -> str:
        return self.track_id

    @property
    def vlc_id(self) -> str:
        return self.track_id

    @property
    def characteristics(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.full_dialogue is True:
            values.append("full dialogue")
        if self.signs_songs is True:
            values.append("signs/songs")
        if self.forced is True:
            values.append("forced")
        if self.sdh is True:
            values.append("SDH")
        return tuple(values)

    @property
    def label(self) -> str:
        parts: list[str] = []
        if self.language is not None:
            parts.append("English" if self.language == "en" else self.language)
        if self.title is not None:
            parts.append(self.title)
        parts.extend(self.characteristics)
        return " · ".join(parts) if parts else f"Track {self.track_id}"

    def descriptor(self) -> SubtitleDescriptor:
        return SubtitleDescriptor(
            mode="track",
            language=self.language,
            full_dialogue=self.full_dialogue,
            signs_songs=self.signs_songs,
            forced=self.forced,
            sdh=self.sdh,
        )


@dataclass(frozen=True)
class SubtitleDescriptor:
    """The semantic, non-ephemeral form persisted for a remembered choice."""

    mode: SubtitleMode
    language: str | None = None
    full_dialogue: TriState = None
    signs_songs: TriState = None
    forced: TriState = None
    sdh: TriState = None
    version: int = 1

    @classmethod
    def off(cls) -> SubtitleDescriptor:
        return cls(mode="off")

    @classmethod
    def from_track(cls, track: SubtitleTrack) -> SubtitleDescriptor:
        return track.descriptor()

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "mode": self.mode,
            "language": self.language,
            "full_dialogue": self.full_dialogue,
            "signs_songs": self.signs_songs,
            "forced": self.forced,
            "sdh": self.sdh,
        }

    @classmethod
    def from_dict(cls, value: object) -> SubtitleDescriptor:
        if not isinstance(value, dict):
            raise SubtitleError("subtitle preference is not an object")
        version = value.get("version", 1)
        mode = value.get("mode")
        if version != 1 or mode not in {"off", "track"}:
            raise SubtitleError("unsupported subtitle preference")
        language = value.get("language")
        if language is not None and (not isinstance(language, str) or normalize_language(language) != language):
            raise SubtitleError("invalid remembered subtitle language")
        flags: list[TriState] = []
        for key in ("full_dialogue", "signs_songs", "forced", "sdh"):
            flag = value.get(key)
            if flag is not None and not isinstance(flag, bool):
                raise SubtitleError("invalid remembered subtitle characteristic")
            flags.append(flag)
        if mode == "off":
            language = None
            flags = [None, None, None, None]
        return cls(mode, language, flags[0], flags[1], flags[2], flags[3], 1)


@dataclass(frozen=True)
class SubtitleTarget:
    generation: int
    queue_entry_id: int
    path: Path
    playlist_id: str | None


@dataclass(frozen=True)
class SubtitleSnapshot:
    target: SubtitleTarget
    tracks: tuple[SubtitleTrack, ...]


@dataclass(frozen=True)
class SubtitleChoice:
    mode: SubtitleMode
    track: SubtitleTrack | None = None

    def __post_init__(self) -> None:
        if self.mode == "off" and self.track is not None:
            raise ValueError("Off cannot carry a subtitle track")
        if self.mode == "track" and self.track is None:
            raise ValueError("track choice requires a subtitle track")

    @classmethod
    def off(cls) -> SubtitleChoice:
        return cls("off")

    @classmethod
    def track_choice(cls, track: SubtitleTrack) -> SubtitleChoice:
        return cls("track", track)

    def descriptor(self) -> SubtitleDescriptor:
        return SubtitleDescriptor.off() if self.mode == "off" else self.track.descriptor()  # type: ignore[union-attr]


def _stream_entries(category: dict[str, object]) -> list[tuple[int, dict[str, object]]]:
    result: list[tuple[int, dict[str, object]]] = []
    for key, value in category.items():
        match = re.fullmatch(r"Stream[ \t]+([0-9]{1,6})", key)
        if match is None:
            if key.casefold().startswith("stream"):
                raise SubtitleError("VLC subtitle stream identity is malformed")
            continue
        if not isinstance(value, dict):
            raise SubtitleError("VLC subtitle stream metadata is malformed")
        result.append((int(match.group(1)), value))
    return sorted(result)


def parse_subtitle_tracks(payload: object) -> tuple[SubtitleTrack, ...]:
    """Parse recognized VLC 3 status variants and fail closed otherwise."""
    if not isinstance(payload, dict):
        raise SubtitleError("VLC subtitle response is malformed")
    information = payload.get("information")
    category: object = information.get("category") if isinstance(information, dict) else payload.get("category")
    if category is None:
        return ()
    if not isinstance(category, dict):
        raise SubtitleError("VLC subtitle category is malformed")
    tracks: list[SubtitleTrack] = []
    for stream_id, metadata in _stream_entries(category):
        stream_type = _bounded_text(metadata.get("Type"), limit=32)
        codec = _bounded_text(metadata.get("Codec"), limit=96)
        if (stream_type or "").casefold() != "subtitle" and "subtitle" not in (codec or "").casefold():
            continue
        title = None
        for key in ("Description", "description", "Title", "title", "Name", "name"):
            title = _bounded_text(metadata.get(key))
            if title is not None:
                break
        full, signs, forced, sdh = _characteristics(metadata, title)
        tracks.append(
            SubtitleTrack(
                track_id=str(stream_id),
                language=_language(metadata, title),
                title=title,
                full_dialogue=full,
                signs_songs=signs,
                forced=forced,
                sdh=sdh,
                active=_active(metadata),
                source_order=len(tracks),
            )
        )
    return tuple(tracks)


def _rank_flags(track: SubtitleTrack) -> tuple[int, int, int, int]:
    # Unknown traits deliberately tie with the neutral class and then use VLC
    # source order. A known undesirable trait is never better than a known
    # neutral one.
    return (
        1 if track.forced is True else 0,
        1 if track.sdh is True else 0,
        1 if track.signs_songs is True else 0,
        1 if track.full_dialogue is False else 0,
    )


def rank_english_tracks(tracks: tuple[SubtitleTrack, ...] | list[SubtitleTrack]) -> tuple[SubtitleTrack, ...]:
    english = [track for track in tracks if track.language == "en"]
    full_dialogue = [track for track in english if track.full_dialogue is True]
    candidates = full_dialogue if full_dialogue else english
    return tuple(sorted(candidates, key=lambda track: (*_rank_flags(track), track.source_order)))


def choose_english_track(tracks: tuple[SubtitleTrack, ...] | list[SubtitleTrack]) -> SubtitleTrack | None:
    ranked = rank_english_tracks(tracks)
    return ranked[0] if ranked else None


def match_remembered_track(
    descriptor: SubtitleDescriptor, tracks: tuple[SubtitleTrack, ...] | list[SubtitleTrack]
) -> SubtitleTrack | None:
    if descriptor.mode == "off":
        return None
    candidates = [track for track in tracks if descriptor.language is None or track.language == descriptor.language]
    if descriptor.language is not None:
        candidates = [track for track in candidates if track.language == descriptor.language]
    if not candidates:
        return None

    def score(track: SubtitleTrack) -> tuple[int, int, int, int, int, int]:
        agreements = 0
        conflicts = 0
        for expected, actual in (
            (descriptor.full_dialogue, track.full_dialogue),
            (descriptor.signs_songs, track.signs_songs),
            (descriptor.forced, track.forced),
            (descriptor.sdh, track.sdh),
        ):
            if expected is None:
                continue
            if actual is expected:
                agreements += 1
            elif actual is not None:
                conflicts += 1
        return (
            0 if track.language != descriptor.language else 1,
            agreements,
            -conflicts,
            1 if track.full_dialogue is True else 0,
            -_rank_flags(track)[0],
            -track.source_order,
        )

    return max(candidates, key=score)


_SEASON_RE = re.compile(r"^(?:season|series|s)[ ._-]*([0-9]{1,3})$", re.IGNORECASE)
_EPISODE_RE = re.compile(
    r"(?<![A-Za-z0-9])(?:S[0-9]{1,3}E[0-9]{1,3}|[0-9]{1,2}X[0-9]{1,3}|Episode[ ._-]*[0-9]{1,4})(?![A-Za-z0-9])",
    re.IGNORECASE,
)
_GENERIC_SHOW_NAMES = {"", "show", "series", "season", "episode", "video", "videos", "media", "library", "root"}


def _normalized_component(value: str) -> str:
    value = re.sub(r"[\[\](){}]", " ", value)
    value = re.sub(r"[^A-Za-z0-9]+", " ", value).strip().casefold()
    return re.sub(r"\s+", " ", value)


def infer_show_identity(path: Path, root: Path) -> str | None:
    """Infer a conservative root-relative show identity, never an absolute path."""
    try:
        canonical_root = root.expanduser().resolve(strict=True)
        canonical_path = path.expanduser().resolve(strict=False)
        relative = canonical_path.relative_to(canonical_root)
    except (OSError, RuntimeError, ValueError):
        return None
    if not relative.parts or canonical_path.is_dir():
        return None
    directories = list(relative.parts[:-1])
    stem = Path(relative.parts[-1]).stem
    season_index = next(
        (index for index in range(len(directories) - 1, -1, -1) if _SEASON_RE.fullmatch(directories[index])),
        None,
    )
    if season_index is not None:
        prefix = [_normalized_component(part) for part in directories[:season_index]]
        prefix = [part for part in prefix if part and part not in {"season", "series"}]
        if not prefix:
            return None
        identity = "/".join(prefix)
        return None if identity in _GENERIC_SHOW_NAMES else identity
    if directories:
        identity = _normalized_component(directories[-1])
        return None if identity in _GENERIC_SHOW_NAMES else identity
    match = _EPISODE_RE.search(stem)
    if match is None:
        return None
    identity = _normalized_component(stem[: match.start()])
    return None if identity in _GENERIC_SHOW_NAMES else identity


def scoped_show_key(path: Path, root: Path) -> str | None:
    identity = infer_show_identity(path, root)
    if identity is None:
        return None
    try:
        canonical_root = root.expanduser().resolve(strict=True)
    except (OSError, RuntimeError):
        return None
    root_hash = hashlib.sha256(str(canonical_root).encode("utf-8")).hexdigest()[:24]
    identity_hash = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"{root_hash}:{identity_hash}"


def descriptor_for_choice(choice: SubtitleChoice) -> SubtitleDescriptor:
    return choice.descriptor()
