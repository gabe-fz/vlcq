from __future__ import annotations

import asyncio
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

from .config import ffprobe_path
from .paths import VIDEO_EXTENSIONS, canonical_root, is_beneath


class SubtitleError(ValueError):
    """A subtitle payload or persisted descriptor is not safely usable."""


TriState = bool | None
SubtitleMode = Literal["off", "track"]
SubtitleSource = Literal["embedded", "sidecar"]

_MAX_LABEL_LENGTH = 160
_MAX_DESCRIPTOR_TEXT = 32
_MAX_VARIANT_LENGTH = 48
_MAX_PROBE_OUTPUT = 512 * 1024
_MAX_PROBE_SECONDS = 5.0
SIDECAR_EXTENSIONS = frozenset({".srt", ".ass", ".ssa", ".vtt", ".sub", ".idx", ".sup"})

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
class SubtitleCandidate:
    """An immutable offline subtitle identity.

    ``path`` and ``probe_index`` are deliberately ephemeral.  They are useful
    only between discovery and a validated playback operation; descriptors
    never serialize either value.
    """

    source: SubtitleSource
    language: str | None = None
    title: str | None = None
    full_dialogue: TriState = None
    signs_songs: TriState = None
    forced: TriState = None
    sdh: TriState = None
    source_order: int = 0
    media_identity: tuple[int, int, int, int] | None = None
    sidecar_variant: str | None = None
    path: Path | None = None
    probe_index: int | None = None
    sidecar_identity: tuple[int, int, int, int] | None = None

    def __post_init__(self) -> None:
        if self.source not in {"embedded", "sidecar"}:
            raise ValueError("unknown subtitle source")
        if self.source_order < 0:
            raise ValueError("subtitle source order must be nonnegative")
        if self.language is not None and normalize_language(self.language) != self.language:
            raise ValueError("subtitle candidate language must be normalized")
        if self.probe_index is not None and not 0 <= self.probe_index <= 1_000_000:
            raise ValueError("subtitle probe index is out of bounds")
        if self.source == "embedded" and self.path is not None:
            raise ValueError("embedded candidates cannot carry a sidecar path")
        if self.source == "sidecar" and self.probe_index is not None:
            raise ValueError("sidecar candidates cannot carry a probe index")
        if self.source == "sidecar" and self.path is None:
            raise ValueError("sidecar candidates require a path")
        if self.source == "embedded" and self.sidecar_identity is not None:
            raise ValueError("embedded candidates cannot carry a sidecar identity")
        if self.sidecar_variant is not None:
            variant = _normalize_variant(self.sidecar_variant)
            if variant is None:
                raise ValueError("invalid subtitle sidecar variant")
            object.__setattr__(self, "sidecar_variant", variant)
        if self.title is not None:
            bounded = _bounded_text(self.title)
            object.__setattr__(self, "title", bounded)

    @property
    def identity(self) -> tuple[object, ...]:
        return (
            self.source,
            self.media_identity,
            self.sidecar_variant,
            self.probe_index,
            self.path,
            self.sidecar_identity,
        )

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
        parts = ["Embedded" if self.source == "embedded" else "Sidecar"]
        if self.language is not None:
            parts.append("English" if self.language == "en" else self.language)
        if self.title is not None:
            parts.append(self.title)
        if self.sidecar_variant is not None and self.sidecar_variant not in parts:
            parts.append(self.sidecar_variant)
        parts.extend(self.characteristics)
        return " · ".join(parts)

    def descriptor(self) -> SubtitleDescriptor:
        return SubtitleDescriptor(
            mode="track",
            language=self.language,
            full_dialogue=self.full_dialogue,
            signs_songs=self.signs_songs,
            forced=self.forced,
            sdh=self.sdh,
            version=2,
            source=self.source,
            sidecar_variant=self.sidecar_variant,
        )


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
    source: SubtitleSource = "embedded"

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
            version=2,
            source=self.source,
        )


def _normalize_variant(value: object) -> str | None:
    text = _bounded_text(value, limit=_MAX_VARIANT_LENGTH)
    if text is None or "/" in text or "\\" in text or ":" in text:
        return None
    normalized = re.sub(r"[^a-z0-9]+", "-", text.casefold()).strip("-")
    return normalized[:_MAX_VARIANT_LENGTH] or None


@dataclass(frozen=True)
class SubtitleDescriptor:
    """The semantic, non-ephemeral form persisted for a remembered choice."""

    mode: SubtitleMode
    language: str | None = None
    full_dialogue: TriState = None
    signs_songs: TriState = None
    forced: TriState = None
    sdh: TriState = None
    version: int = 2
    source: SubtitleSource | None = None
    sidecar_variant: str | None = None

    def __post_init__(self) -> None:
        if self.mode not in {"off", "track"}:
            raise ValueError("unknown subtitle mode")
        if self.version not in {1, 2}:
            raise ValueError("unsupported subtitle descriptor version")
        if self.source not in {None, "embedded", "sidecar"}:
            raise ValueError("unknown subtitle source")
        if self.language is not None and normalize_language(self.language) != self.language:
            raise ValueError("subtitle descriptor language must be normalized")
        if self.sidecar_variant is not None:
            normalized = _normalize_variant(self.sidecar_variant)
            if normalized is None:
                raise ValueError("invalid subtitle sidecar variant")
            object.__setattr__(self, "sidecar_variant", normalized)
        if self.mode == "off" and (
            self.language is not None or self.source is not None or self.sidecar_variant is not None
        ):
            raise ValueError("Off cannot carry subtitle metadata")
        if self.sidecar_variant is not None and self.source not in {None, "sidecar"}:
            raise ValueError("only sidecar descriptors may carry a sidecar variant")

    @classmethod
    def off(cls) -> SubtitleDescriptor:
        return cls(mode="off")

    @classmethod
    def from_track(cls, track: SubtitleTrack) -> SubtitleDescriptor:
        return track.descriptor()

    def to_dict(self) -> dict[str, object]:
        result: dict[str, object] = {
            "version": self.version,
            "mode": self.mode,
            "language": self.language,
            "full_dialogue": self.full_dialogue,
            "signs_songs": self.signs_songs,
            "forced": self.forced,
            "sdh": self.sdh,
        }
        if self.version >= 2:
            result["source"] = self.source
            result["sidecar_variant"] = self.sidecar_variant
        return result

    @classmethod
    def from_dict(cls, value: object) -> SubtitleDescriptor:
        if not isinstance(value, dict):
            raise SubtitleError("subtitle preference is not an object")
        version = value.get("version", 1)
        mode = value.get("mode")
        if isinstance(version, bool) or not isinstance(version, int) or version not in {1, 2} or mode not in {"off", "track"}:
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
        source: SubtitleSource | None = None
        variant: str | None = None
        if version == 2:
            raw_source = value.get("source")
            if raw_source is not None and raw_source not in {"embedded", "sidecar"}:
                raise SubtitleError("invalid remembered subtitle source")
            source = cast(SubtitleSource | None, raw_source)
            raw_variant = value.get("sidecar_variant")
            if raw_variant is not None:
                variant = _normalize_variant(raw_variant)
                if variant is None:
                    raise SubtitleError("invalid remembered subtitle sidecar variant")
        if mode == "off":
            language = None
            flags = [None, None, None, None]
            source = None
            variant = None
        return cls(mode, language, flags[0], flags[1], flags[2], flags[3], int(version), source, variant)


@dataclass(frozen=True)
class SubtitleTarget:
    generation: int
    queue_entry_id: int
    path: Path
    playlist_id: str | None
    root_generation: int = 0
    media_identity: tuple[int, int, int, int] | None = None
    active: bool = False


@dataclass(frozen=True)
class SubtitleSnapshot:
    target: SubtitleTarget
    tracks: tuple[SubtitleTrack, ...]
    candidates: tuple[SubtitleCandidate, ...] = ()
    planned: SubtitleCandidate | None = None
    discovery_error: str | None = None
    planned_choice: SubtitleChoice | None = None


@dataclass(frozen=True)
class SubtitleChoice:
    mode: SubtitleMode
    track: SubtitleTrack | None = None
    candidate: SubtitleCandidate | None = None

    def __post_init__(self) -> None:
        if self.mode == "off" and (self.track is not None or self.candidate is not None):
            raise ValueError("Off cannot carry a subtitle track")
        if self.mode == "track" and (self.track is None) == (self.candidate is None):
            raise ValueError("track choice requires exactly one subtitle item")

    @classmethod
    def candidate_choice(cls, candidate: SubtitleCandidate) -> SubtitleChoice:
        return cls("track", candidate=candidate)

    @classmethod
    def off(cls) -> SubtitleChoice:
        return cls("off")

    @classmethod
    def track_choice(cls, track: SubtitleTrack) -> SubtitleChoice:
        return cls("track", track)

    def descriptor(self) -> SubtitleDescriptor:
        if self.mode == "off":
            return SubtitleDescriptor.off()
        if self.candidate is not None:
            return self.candidate.descriptor()
        assert self.track is not None
        return self.track.descriptor()


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


def _candidate_rank_flags(candidate: SubtitleCandidate) -> tuple[int, int, int, int]:
    return (
        1 if candidate.forced is True else 0,
        1 if candidate.sdh is True else 0,
        1 if candidate.signs_songs is True else 0,
        1 if candidate.full_dialogue is False else 0,
    )


def rank_english_candidates(
    candidates: tuple[SubtitleCandidate, ...] | list[SubtitleCandidate],
) -> tuple[SubtitleCandidate, ...]:
    english = [candidate for candidate in candidates if candidate.language == "en"]
    full_dialogue = [candidate for candidate in english if candidate.full_dialogue is True]
    eligible = full_dialogue if full_dialogue else english
    return tuple(
        sorted(
            eligible,
            key=lambda candidate: (
                *_candidate_rank_flags(candidate),
                candidate.source_order,
            ),
        )
    )


def choose_english_candidate(
    candidates: tuple[SubtitleCandidate, ...] | list[SubtitleCandidate],
) -> SubtitleCandidate | None:
    ranked = rank_english_candidates(candidates)
    return ranked[0] if ranked else None


def match_remembered_candidate(
    descriptor: SubtitleDescriptor,
    candidates: tuple[SubtitleCandidate, ...] | list[SubtitleCandidate],
) -> SubtitleCandidate | None:
    """Resolve a semantic descriptor without comparing ephemeral paths or IDs."""
    if descriptor.mode == "off":
        return None
    available = [
        candidate
        for candidate in candidates
        if descriptor.language is None or candidate.language == descriptor.language
    ]
    if not available:
        return None

    def score(candidate: SubtitleCandidate) -> tuple[int, int, int, int, int, int, int]:
        agreements = 0
        conflicts = 0
        for expected, actual in (
            (descriptor.full_dialogue, candidate.full_dialogue),
            (descriptor.signs_songs, candidate.signs_songs),
            (descriptor.forced, candidate.forced),
            (descriptor.sdh, candidate.sdh),
        ):
            if expected is None:
                continue
            if actual is expected:
                agreements += 1
            elif actual is not None:
                conflicts += 1
        source_match = int(descriptor.source is None or candidate.source == descriptor.source)
        variant_match = int(
            descriptor.sidecar_variant is not None
            and candidate.sidecar_variant == descriptor.sidecar_variant
        )
        return (
            int(candidate.language == descriptor.language) if descriptor.language else 0,
            source_match,
            variant_match,
            agreements,
            -conflicts,
            int(candidate.full_dialogue is True),
            -candidate.source_order,
        )

    return max(available, key=score)


def candidate_for_track(track: SubtitleTrack) -> SubtitleCandidate:
    return SubtitleCandidate(
        source=track.source,
        language=track.language,
        title=track.title,
        full_dialogue=track.full_dialogue,
        signs_songs=track.signs_songs,
        forced=track.forced,
        sdh=track.sdh,
        source_order=track.source_order,
    )


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
            int(descriptor.language is None or track.language == descriptor.language),
            int(descriptor.source is None or track.source == descriptor.source),
            agreements,
            -conflicts,
            1 if track.full_dialogue is True else 0,
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


class SubtitleDiscoveryError(RuntimeError):
    """A bounded local subtitle inspection failed."""


@dataclass(frozen=True)
class _FileIdentity:
    device: int
    inode: int
    size: int
    mtime_ns: int

    @classmethod
    def for_path(cls, path: Path) -> _FileIdentity:
        stat = path.stat()
        return cls(stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)

    def as_tuple(self) -> tuple[int, int, int, int]:
        return self.device, self.inode, self.size, self.mtime_ns


def _stream_metadata(stream: dict[str, object]) -> tuple[dict[str, object], dict[str, object]]:
    tags_value = stream.get("tags", {})
    disposition_value = stream.get("disposition", {})
    if not isinstance(tags_value, dict) or not isinstance(disposition_value, dict):
        raise SubtitleDiscoveryError("ffprobe returned malformed stream metadata")
    tags = tags_value
    disposition = disposition_value
    metadata: dict[str, object] = {str(key): value for key, value in tags.items()}
    for key, value in disposition.items():
        metadata[str(key)] = value
    if "codec_name" in stream:
        metadata["Codec"] = stream["codec_name"]
    return metadata, {str(key): value for key, value in disposition.items()}


def _candidate_from_probe_stream(stream: dict[str, object], order: int) -> SubtitleCandidate:
    raw_index = stream.get("index")
    if not isinstance(raw_index, int) or isinstance(raw_index, bool) or not 0 <= raw_index <= 1_000_000:
        raise SubtitleDiscoveryError("ffprobe returned an invalid subtitle stream index")
    metadata, disposition = _stream_metadata(stream)
    title = next(
        (
            _bounded_text(metadata.get(key))
            for key in ("title", "Title", "name", "Name")
            if _bounded_text(metadata.get(key)) is not None
        ),
        None,
    )
    full, signs, forced, sdh = _characteristics(metadata, title)
    forced = _flag(disposition.get("forced")) if _flag(disposition.get("forced")) is not None else forced
    sdh = (
        _flag(disposition.get("hearing_impaired"))
        if _flag(disposition.get("hearing_impaired")) is not None
        else sdh
    )
    return SubtitleCandidate(
        source="embedded",
        language=_language(metadata, title),
        title=title,
        full_dialogue=full,
        signs_songs=signs,
        forced=forced,
        sdh=sdh,
        source_order=order,
        probe_index=raw_index,
    )


def _sidecar_tokens(video: Path, sidecar: Path) -> tuple[str, ...]:
    video_stem = video.stem
    stem = sidecar.stem
    suffix = stem[len(video_stem) + 1 :] if stem.casefold().startswith(video_stem.casefold() + ".") else ""
    return tuple(token for token in re.split(r"[._ -]+", suffix.casefold()) if token)


def _sidecar_candidate(video: Path, path: Path, order: int) -> SubtitleCandidate:
    tokens = list(_sidecar_tokens(video, path))
    language: str | None = None
    semantic_tokens = {"forced", "only", "sdh", "hi", "hearing", "impaired", "closed", "captions", "signs", "songs", "full", "dialogue", "dialog"}
    remaining: list[str] = []
    for token in tokens:
        parsed = normalize_language(token)
        if parsed is not None and language is None:
            language = parsed
        elif token in semantic_tokens:
            continue
        else:
            remaining.append(token)
    folded = " ".join(tokens)
    signs = True if re.search(r"\b(?:signs?|songs?)\b", folded) else None
    forced = True if re.search(r"\bforced(?:[- ]only)?\b", folded) else None
    sdh = True if re.search(r"\b(?:sdh|hi|hearing[- ]impaired|closed[- ]captions?)\b", folded) else None
    full = True if re.search(r"\bfull[- ]?(?:dialogue|dialog)\b", folded) else None
    if full is None and (signs is True or forced is True):
        full = False
    return SubtitleCandidate(
        source="sidecar",
        language=language,
        full_dialogue=full,
        signs_songs=signs,
        forced=forced,
        sdh=sdh,
        source_order=order,
        sidecar_variant=_normalize_variant("-".join(remaining)),
        path=path,
    )


def associate_sidecars(video: Path, root: Path) -> tuple[Path, ...]:
    """Return one logical, immediate-sibling sidecar per subtitle stem."""
    canonical_video = video.expanduser().resolve(strict=True)
    base = canonical_root(root)
    if not canonical_video.is_file() or not is_beneath(canonical_video, base):
        raise SubtitleDiscoveryError("video is outside the active library root")
    try:
        children = list(canonical_video.parent.iterdir())
    except OSError as exc:
        raise SubtitleDiscoveryError("subtitle directory cannot be read") from exc
    matching: dict[tuple[str, str], dict[str, Path]] = {}
    for child in children:
        if child.name.startswith("."):
            continue
        if child.suffix.casefold() not in SIDECAR_EXTENSIONS:
            continue
        stem = child.stem
        if not (
            stem.casefold() == canonical_video.stem.casefold()
            or stem.casefold().startswith(canonical_video.stem.casefold() + ".")
        ):
            continue
        try:
            candidate = child.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not is_beneath(candidate, base) or not candidate.is_file():
            continue
        suffix = candidate.suffix.casefold()
        group = "vobsub" if suffix in {".idx", ".sub"} else suffix
        key = (stem.casefold(), group)
        matching.setdefault(key, {})[suffix] = candidate
    result: list[Path] = []
    for files in matching.values():
        if ".idx" in files:
            result.append(files[".idx"])
        elif ".sub" in files:
            result.append(files[".sub"])
        else:
            result.append(next(iter(files.values())))
    return tuple(sorted(result, key=lambda path: path.name.casefold()))


class FFProbeAdapter:
    """Bounded, shell-free async adapter for the local ``ffprobe`` binary."""

    def __init__(
        self,
        executable: str | Path | None = None,
        *,
        timeout: float = _MAX_PROBE_SECONDS,
        output_limit: int = _MAX_PROBE_OUTPUT,
        concurrency: int = 2,
        output_ceiling: int | None = None,
        max_concurrency: int | None = None,
    ) -> None:
        resolved = str(executable) if executable is not None else (
            str(ffprobe_path()) if ffprobe_path() is not None else None
        )
        self.executable = resolved
        self.timeout = max(0.1, float(timeout))
        self.output_limit = max(1024, int(output_ceiling if output_ceiling is not None else output_limit))
        self._semaphore = asyncio.Semaphore(
            max(1, int(max_concurrency if max_concurrency is not None else concurrency))
        )

    @staticmethod
    async def _read_capped(stream: asyncio.StreamReader, limit: int) -> bytes:
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = await stream.read(min(65536, limit - total + 1))
            if not chunk:
                return b"".join(chunks)
            chunks.append(chunk)
            total += len(chunk)
            if total > limit:
                raise SubtitleDiscoveryError("ffprobe output exceeded the safety limit")

    async def probe(self, path: Path) -> tuple[dict[str, object], ...]:
        try:
            canonical = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SubtitleDiscoveryError("subtitle target is unavailable") from exc
        if not canonical.is_file():
            raise SubtitleDiscoveryError("subtitle target is not a regular file")
        if self.executable is None:
            raise SubtitleDiscoveryError("ffprobe was not found; install FFmpeg (brew install ffmpeg)")
        args = [
            self.executable,
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_entries",
            "stream=index,codec_name,codec_type:stream_tags=language,title:stream_disposition=forced,hearing_impaired,default",
            "-i",
            canonical.as_uri(),
        ]
        async with self._semaphore:
            process: asyncio.subprocess.Process | None = None
            stdout_task: asyncio.Task[bytes] | None = None
            stderr_task: asyncio.Task[bytes] | None = None
            try:
                process = await asyncio.create_subprocess_exec(
                    *args,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                if process.stdout is None or process.stderr is None:
                    raise SubtitleDiscoveryError("ffprobe did not provide bounded output pipes")
                stdout_task = asyncio.create_task(self._read_capped(process.stdout, self.output_limit))
                stderr_task = asyncio.create_task(self._read_capped(process.stderr, self.output_limit))
                stdout, _stderr = await asyncio.wait_for(
                    asyncio.gather(stdout_task, stderr_task), self.timeout
                )
                returncode = await asyncio.wait_for(process.wait(), self.timeout)
                if returncode != 0:
                    raise SubtitleDiscoveryError("ffprobe could not inspect the media file")
            except asyncio.CancelledError:
                if stdout_task is not None:
                    stdout_task.cancel()
                if stderr_task is not None:
                    stderr_task.cancel()
                raise
            except (FileNotFoundError, OSError) as exc:
                raise SubtitleDiscoveryError("ffprobe could not be started") from exc
            except (TimeoutError, SubtitleDiscoveryError) as exc:
                if isinstance(exc, TimeoutError):
                    raise SubtitleDiscoveryError("ffprobe timed out") from exc
                raise
            finally:
                if process is not None and process.returncode is None:
                    process.terminate()
                    try:
                        await asyncio.wait_for(process.wait(), 1)
                    except (TimeoutError, OSError):
                        if process.returncode is None:
                            process.kill()
                            await process.wait()
                for task in (stdout_task, stderr_task):
                    if task is not None and not task.done():
                        task.cancel()
        try:
            decoded = json.loads(stdout.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SubtitleDiscoveryError("ffprobe returned malformed JSON") from exc
        if not isinstance(decoded, dict) or not isinstance(decoded.get("streams", []), list):
            raise SubtitleDiscoveryError("ffprobe returned malformed stream metadata")
        streams: list[dict[str, object]] = []
        for value in decoded["streams"]:
            if not isinstance(value, dict):
                raise SubtitleDiscoveryError("ffprobe returned malformed stream metadata")
            if value.get("codec_type") == "subtitle":
                streams.append({str(key): item for key, item in value.items()})
        return tuple(streams)

    inspect = probe
    run = probe


class SubtitleDiscovery:
    """On-demand embedded/sidecar discovery with session-only coalescing cache."""

    def __init__(
        self,
        probe: FFProbeAdapter | object | None = None,
        *,
        root: Path | None = None,
    ) -> None:
        self.probe = probe if probe is not None else FFProbeAdapter()
        self.root = root.resolve() if root is not None else None
        self._cache: dict[tuple[object, ...], tuple[SubtitleCandidate, ...]] = {}
        self._inflight: dict[tuple[object, ...], asyncio.Task[tuple[SubtitleCandidate, ...]]] = {}

    def set_root(self, root: Path) -> None:
        canonical = canonical_root(root)
        if self.root != canonical:
            self.root = canonical
            self.invalidate()

    def invalidate(self) -> None:
        self._cache.clear()
        for task in self._inflight.values():
            if not task.done():
                task.cancel()
        self._inflight.clear()

    @staticmethod
    def _cache_key(path: Path, root: Path) -> tuple[object, ...]:
        try:
            media = _FileIdentity.for_path(path).as_tuple()
            directory = _FileIdentity.for_path(path.parent).as_tuple()
        except OSError as exc:
            raise SubtitleDiscoveryError("subtitle target is unavailable") from exc
        return (root, path, media, directory)

    async def _discover_uncached(self, path: Path, root: Path) -> tuple[SubtitleCandidate, ...]:
        try:
            raw_streams = await self.probe.probe(path)  # type: ignore[attr-defined]
        except SubtitleDiscoveryError:
            raise
        except (OSError, RuntimeError, TypeError, ValueError) as exc:
            raise SubtitleDiscoveryError("offline subtitle inspection failed") from exc
        if not isinstance(raw_streams, (tuple, list)):
            raise SubtitleDiscoveryError("ffprobe returned malformed stream metadata")
        candidates: list[SubtitleCandidate] = []
        for value in raw_streams:
            if not isinstance(value, dict) or value.get("codec_type") != "subtitle":
                raise SubtitleDiscoveryError("ffprobe returned malformed stream metadata")
            candidates.append(_candidate_from_probe_stream(value, len(candidates)))
        media_identity = _FileIdentity.for_path(path).as_tuple()
        for sidecar in associate_sidecars(path, root):
            candidate = _sidecar_candidate(path, sidecar, len(candidates))
            candidates.append(
                SubtitleCandidate(
                    source=candidate.source,
                    language=candidate.language,
                    title=candidate.title,
                    full_dialogue=candidate.full_dialogue,
                    signs_songs=candidate.signs_songs,
                    forced=candidate.forced,
                    sdh=candidate.sdh,
                    source_order=candidate.source_order,
                    media_identity=media_identity,
                    sidecar_variant=candidate.sidecar_variant,
                    path=sidecar,
                    sidecar_identity=_FileIdentity.for_path(sidecar).as_tuple(),
                )
            )
        return tuple(candidates)

    async def discover(self, path: Path, root: Path | None = None) -> tuple[SubtitleCandidate, ...]:
        selected_root = root if root is not None else self.root
        if selected_root is None:
            raise SubtitleDiscoveryError("subtitle discovery requires an active library root")
        active_root = canonical_root(selected_root)
        canonical = path.expanduser().resolve(strict=True)
        if not canonical.is_file() or not is_beneath(canonical, active_root):
            raise SubtitleDiscoveryError("subtitle target is outside the active library root")
        if canonical.suffix.casefold() not in VIDEO_EXTENSIONS:
            raise SubtitleDiscoveryError("subtitle target is not a supported video")
        key = self._cache_key(canonical, active_root)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        task = self._inflight.get(key)
        if task is None:
            task = asyncio.create_task(self._discover_uncached(canonical, active_root))
            self._inflight[key] = task
        try:
            result = await asyncio.shield(task)
        finally:
            if task.done() and self._inflight.get(key) is task:
                self._inflight.pop(key, None)
        self._cache[key] = result
        return result

    async def inspect(self, path: Path, root: Path | None = None) -> tuple[SubtitleCandidate, ...]:
        return await self.discover(path, root)


# Names used by integrations that describe the same adapter as an inspector.
SubtitleInspector = SubtitleDiscovery
FFProbe = FFProbeAdapter
OfflineSubtitleCandidate = SubtitleCandidate
SubtitleDiscoveryTarget = SubtitleTarget
discover_sidecars = associate_sidecars
