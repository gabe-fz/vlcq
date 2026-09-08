## Why

The current folder-at-a-time browser hides library context, while one-line textual history and menu-only actions make progress and frequent controls harder to scan and use. A persistent tree, visual per-item progress, and compact action bars will make the TUI faster to understand without weakening its deterministic, non-destructive behavior.

## What Changes

- Replace folder-by-folder Files navigation with an inline, recursively expandable/collapsible tree that keeps the library hierarchy in one scrollable view.
- Color-code literal names and name components so folders, playable files, and relevant filename parts remain visually distinguishable; reserve progress colors for progress bars rather than using filename color as watch status.
- Add thick video-game-style progress bars with numeric percentages to Files and Queue items that have positive recorded progress.
- Treat furthest recorded progress at or above a configurable watched threshold as watched/completed; default the threshold to 90%.
- Replace each header's menu-only affordance with a one-line compact action bar: Files exposes Open, Search/filter, and Add; Queue exposes Play/pause, Next, and Clear completed; the player exposes Previous, Play/pause, and Next. Each bar retains an ellipsis overflow menu for less-common and contextual actions.
- Rework the bottom player/status area into a slightly taller, bounded layout with a thick live progress bar, percentage, player summary, and notice.
- Preserve keyboard access, right-click context actions, identity-safe targeting, responsive asynchronous filesystem/history loading, and usable 80-by-24 behavior.
- Non-goals: media-file management, metadata scraping, fuzzy episode detection, playback-coverage measurement, and changes to VLC ownership or queue ordering.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `vlcq`: Changes the main TUI's library-navigation, watch classification, row progress, action-bar, and bottom-status behavior.

## Impact

- Primarily affects `src/vlcq/tui.py`, with supporting changes to browser models/path discovery, configuration, and progress classification.
- Requires updates to Textual headless UI tests, core progress/configuration tests, README controls and presentation documentation, and the existing `vlcq` specification.
- The progress-export document remains version 1, while its `completed` classification will reflect the configured threshold; field names and root confinement remain unchanged.
- No new network access or media mutation is introduced. Recursive discovery must remain canonical-root-confined, hide unsupported/dot entries, validate file identity before showing history, and avoid blocking the event loop.
