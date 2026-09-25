## Context

See `proposal.md` for motivation and `specs/subtitle-selection/spec.md` for behavior. The existing subtitle model is built around ephemeral VLC stream IDs, while its persisted descriptor intentionally retains only cross-episode semantics. Each supported video now has an indented subtitle subitem that owns subtitle interaction and status, instead of hiding that interaction in the video's general context menu. The browser already restricts visible videos to canonical supported files beneath one root, and controller transitions serialize all VLC commands.

Offline discovery crosses three trust boundaries: parsing untrusted media metadata in a subprocess, associating neighboring files without escaping the library, and later translating an offline choice into a VLC-generation-local action. ffprobe and VLC do not share stream IDs, and a sidecar selected for one episode has a different path for the next episode.

## Goals / Non-Goals

**Goals:**
- Inspect embedded streams and associated sidecars without starting or mutating VLC.
- Persist one user choice as a semantic whole-show preference and resolve it visibly on other episodes.
- Revalidate every file and translate the preference safely when playback eventually begins.
- Keep Textual responsive and make cancellation, stale results, malformed media, and missing tools harmless.

**Non-Goals:**
- Downloading, generating, editing, synchronizing, or rendering subtitles.
- Recursively searching the library or accepting arbitrary subtitle paths.
- Guaranteeing support for every format accepted by VLC when ffprobe cannot describe it.
- Treating offline resolution as proof that a subtitle is active in VLC.

## Decisions

### Use the ffprobe executable as a required runtime dependency

Install FFmpeg on macOS and invoke `ffprobe` directly with `asyncio` subprocess APIs, an argument vector (never a shell), machine-readable JSON output, a short timeout, a strict output-size ceiling, and cancellation cleanup. Pass the canonical local video path as the value immediately following `-i`; using a percent-encoded `file:` URI is incompatible with the supported installed ffprobe build. Use a narrow `-show_entries` projection for subtitle stream index, codec, tags, and dispositions. Treat a nonzero exit, timeout, decoding error, schema violation, output overflow, or unavailable executable as one bounded discovery error; never accept a partial result.

Add a startup/dependency diagnostic and installation documentation (`brew install ffmpeg`). Keep the executable path configurable for tests, resolve it once from trusted application configuration/PATH, and do not allow a media filename to become an option. This is an external runtime prerequisite rather than a Python wrapper package because wrappers do not ship ffprobe and add no isolation.

Alternatives: parse Matroska in pure Python, which would solve only one container and expand the untrusted-parser surface; use MediaInfo, which still needs a native dependency and has less direct correspondence to stream dispositions; or ask VLC to preload every inspected file, which recreates the UX and media-safety problem.

### Associate only bounded sibling sidecars

List only the target video's immediate parent directory once. A candidate must be a non-hidden regular file whose canonical path remains beneath the active root, whose final extension is in an explicit subtitle allowlist (`.srt`, `.ass`, `.ssa`, `.vtt`, `.sub`, `.idx`, `.sup`), and whose stem is either the exact video stem or begins with the video stem followed by a dot. This supports names such as `Episode 01.en.whisper.srt` without capturing subtitles belonging to another episode. Case-fold comparisons for matching while preserving literal display text. Treat a `.idx`/`.sub` pair with the same stem as one logical VobSub candidate and never display or apply the data file independently.

Derive sidecar language and characteristics conservatively from suffix tokens and bounded ffprobe metadata where available; preserve a bounded normalized variant made from non-language/non-characteristic suffix tokens (for example `whisper` or `auto`) so a generated sidecar chosen on one episode can win over an otherwise identical official English candidate on later episodes. Do not read sidecar text to classify it.

Alternatives: scan a shared `Subs` tree or fuzzy-match filenames, both of which can disclose unrelated names and cause false associations; support arbitrary user-entered paths, which broadens the path-security and stale-choice surface.

### Separate offline candidates from VLC tracks

Introduce an immutable offline candidate identity containing source kind (`embedded` or `sidecar`), canonical target identity, bounded display metadata, semantic traits, stable discovery order, and—only in ephemeral memory—the embedded probe index or sidecar path. Keep `SubtitleTrack` as the VLC adapter object. Extend the persisted descriptor to version 2 with source kind and optional normalized sidecar variant; continue reading version-1 descriptors as source-agnostic so existing preferences remain usable. The existing JSON preference column can store the new version without a schema migration.

The offline picker receives a target token containing root generation plus a media fingerprint/stat identity. It marks a remembered or English candidate as `planned`, never `active`. Current-media discovery additionally obtains VLC tracks and may show a confirmed active marker under existing generation validation. Selecting an inactive candidate writes only its descriptor, and only when Remember subtitles by show is enabled and show identity is confident. Inspection remains available otherwise, with an explanatory refusal if the user attempts to save.

Alternatives: reuse ffprobe stream indexes as VLC IDs, which is unsafe across adapters; persist sidecar paths, which breaks cross-episode matching and leaks absolute paths; silently enable the Remember toggle, which changes a global preference as a side effect.

### Resolve preferences with source-aware deterministic matching

Generalize semantic matching to offline candidates. Exact source kind and exact sidecar variant are strongest criteria after language eligibility, followed by known characteristic agreements/conflicts and stable discovery order. A version-1 source-agnostic descriptor can match either source. English fallback uses the same candidate set and a documented stable source-order tie break; it does not infer English from position.

At playback, rediscover the unchanged media and resolve the descriptor again rather than retaining a stale sidecar path. Embedded candidates are matched semantically against VLC-reported tracks. For a sidecar, revalidate its canonical identity immediately before calling a new typed VLC client operation wrapping VLC 3's allowlisted `addsubtitle` command with a canonical `file:` URI. Then re-read VLC tracks, semantically identify/select the resulting track, and confirm where VLC exposes active metadata. Every operation remains under the controller transition lock and current generation/path/playlist validation. If VLC already reports a matching sidecar track, skip attachment to prevent duplicates.

Alternatives: launch VLC with a precomputed `--sub-file`, which complicates successor staging and process ownership; assume VLC autoload will expose every sibling, which depends on user/native settings; repeatedly attach until a match appears, which risks duplicate tracks and command loops.

### Run discovery asynchronously with small-lived caching

Run probes outside Textual's event loop through cancellable workers. Cache successful discovery by canonical path plus file stat identity and sibling-directory stat identity for the application session only; invalidate on mismatch, root change, explicit refresh, or before playback application. Do not persist track inventories or sidecar paths. Coalesce concurrent requests for the same identity and cap probe concurrency so opening menus cannot spawn an unbounded process set.

Every supported video keeps one row and places a compact, focusable subtitle status immediately after the literal video filename. Visible video rows are inspected in the background through the bounded, coalescing discovery layer, while off-screen and unrelated library media are not scanned. Resolved status shows only the subtitle's literal embedded title or sidecar filename as white text on a compact gray background, without status glyphs. Language, source, characteristics, active/planned explanation, and alternatives remain in the picker. Activating or right-clicking the inline status presents a loading state, then a persistent picker or one bounded notice; the video's general context menu has no duplicate subtitle action. Successful discovery and selection update both Files and Queue instances of the same path. Root-generation and media-identity checks discard stale completions and restore focus. A currently playing row still uses controller generation validation for any immediate selection command.

Alternatives: probe every library row during tree discovery, which makes browsing expensive and exposes many off-screen malformed files unnecessarily; persist inventories, which quickly become stale and stores more path-derived data than needed.

## Risks / Trade-offs

- **[ffprobe is missing or installed outside PATH]** → Validate the configured executable, provide the exact installation remedy, and leave every media and queue operation unchanged.
- **[Malformed media hangs or floods output]** → Enforce process timeout, output ceiling, concurrency cap, cancellation termination, and all-or-nothing JSON validation.
- **[Sidecar filename association is too strict]** → Document the deterministic same-stem convention; prefer missing a candidate over applying an unrelated subtitle.
- **[Semantic metadata cannot distinguish two tracks]** → Preserve tri-state uncertainty, source/variant information, and stable order; show enough literal metadata for users to understand the choice.
- **[VLC 3 sidecar behavior varies by build]** → Fixture-test request construction and run an opt-in real-VLC test that adds a generated sidecar; fail without retries beyond the existing generation bound.
- **[A file changes after browsing]** → Revalidate canonical/stat identities and rediscover before application; never persist an ephemeral path or stream ID.
- **[Additional probing cost]** → Probe on demand, cache only successful unchanged results, coalesce requests, and never probe all visible rows eagerly.

## Migration Plan

1. Add the offline model/prober and sidecar association behind focused tests, then expose it through always-visible subtitle subitems beneath video rows.
2. Extend descriptors to version 2 while retaining version-1 reads; no SQLite schema version change is required because descriptors are already bounded JSON.
3. Add source-aware policy resolution and the typed VLC sidecar operation, guarded by existing generation validation.
4. Update install guidance to require FFmpeg and reinstall the CLI globally; verify the globally resolved command reports actionable diagnostics when ffprobe is absent.
5. Rollback requires only reinstalling the prior vlcq version. Version-2 preference rows must fail closed in older code; users needing preference continuity should back up the private database before rollback, while queue/history remain unaffected.
