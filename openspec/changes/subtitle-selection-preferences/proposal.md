## Why

Choosing the desired subtitle in VLC for every episode is repetitive, and VLC's default track choice may not favor accessible English full-dialogue subtitles. vlcq should expose subtitle control where playback is managed and safely reuse an explicit choice for later episodes it can identify as the same show.

## What Changes

- Add subtitle-track selection, including Off, to vlcq's right-click/keyboard context menu for the currently loaded media.
- Add a persistent toggle that remembers an explicit subtitle choice and attempts to select the same language and characteristics for subsequent media identified as the same show.
- Add a persistent general English preference. When no remembered show choice applies, automatically prefer an English full-dialogue track, then another English track, if available.
- Define deterministic precedence, track matching, show inference, timing, and safe fallback behavior for unavailable or ambiguous metadata.
- Keep inspection and selection local to the owned VLC process; do not open, enqueue, or begin playing inactive media merely to discover its tracks.

## Capabilities

### New Capabilities
- `subtitle-selection`: Subtitle discovery and manual selection in vlcq, persistent per-show matching, and English-first automatic selection.

### Modified Capabilities

None.

## Impact

- Affects VLC HTTP response parsing and command support, playback transition/controller logic, Textual row and application context menus, private SQLite settings/preferences, and documentation.
- Adds migration or versioned persistence for global preferences and bounded per-show subtitle descriptors; existing queue and watch-history data must be preserved transactionally.
- Tests require mocked VLC payload/command coverage, TUI behavior coverage, migration coverage, and opt-in real VLC 3 verification with generated multi-track media.
- Privacy remains local: inferred show keys and subtitle descriptors are stored only in the private application database, with no telemetry or non-loopback requests. Media remains root-confined and unmodified.
- Non-goals include downloading subtitles, editing/muxing media, controlling unrelated VLC instances, probing inactive files by playing them, and guaranteeing matches when VLC omits usable language/type metadata.
