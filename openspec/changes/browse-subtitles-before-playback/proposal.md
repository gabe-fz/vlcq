## Why

Subtitle choices are currently discoverable only after a file is loaded into the owned VLC process, which hides the feature during normal library browsing and prevents users from reviewing or setting a show-wide choice ahead of playback. Offline inspection should expose embedded and associated sidecar subtitles directly from Files and Queue rows without launching or mutating VLC playback.

## What Changes

- Add bounded, asynchronous offline subtitle discovery for supported video files through a required `ffprobe`/FFmpeg runtime dependency.
- Discover both embedded subtitle streams and root-confined sidecar subtitle files associated with an episode, with conservative language and characteristic extraction.
- Show an always-visible left-aligned **Subtitles** subitem beneath every eligible Files and Queue video row, with clear active/selected-for-playback/unresolved status, and open its picker directly without loading, enqueueing, or playing inactive media.
- Let a pre-playback choice, including **Off**, persist as the semantic preference for the confidently inferred whole show; show the resolved remembered or English-fallback choice when browsing other episodes.
- Reconcile offline descriptors with VLC-reported tracks at playback time, and safely attach/select a matching sidecar when needed, without trusting ffprobe stream identifiers as VLC identifiers.
- Fail closed with bounded feedback when probing, sidecar validation, show inference, or VLC reconciliation is unavailable.
- Do not download subtitles, scan unrelated directories, alter media/sidecar files, or add per-episode subtitle preference storage.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `subtitle-selection`: Extend subtitle discovery and show preference selection to inactive media, embedded streams, and associated sidecar files while preserving current-generation VLC safety.

## Impact

- Affected areas: subtitle domain models and matching, Files/Queue context actions and picker state, playback-controller automatic application, root-confined path handling, tests, installation checks, and documentation.
- Dependency: `ffprobe` from FFmpeg becomes a documented and validated runtime prerequisite on macOS; subprocess use must avoid a shell, enforce time/output bounds, and expose no unrelated paths.
- VLC integration: sidecar application may require a narrowly allowlisted VLC 3 HTTP operation, characterized with fixtures and opt-in real-VLC verification before use.
- Privacy and safety: inspection remains local and read-only, only canonical regular files beneath the active library root are eligible, no subtitle contents or absolute paths are persisted, and failures never change queue order or playback.
