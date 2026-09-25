## Why

Queue highlighting is currently transient and visually competes with the persisted current-playback marker, while per-entry transient states can become stale and produce impossible displays such as two stopped items. The queue needs one durable selection identity and a clear invariant separating the single active playback state from terminal item outcomes and implicit pending position.

## What Changes

- Persist the queue entry highlighted by the user independently of the current playback entry, update it immediately on click or keyboard highlight, and restore it by entry identity across polling refreshes and application restart.
- Keep highlighting non-operative: selecting a queue row does not start, stop, or switch playback.
- Enforce that at most one queue entry is `playing`, `paused`, or `stopped`, and that any such entry is the persisted current playback entry.
- Normalize a prior current entry to pending when current playback moves elsewhere, including when the prior state was stopped, and repair stale transient states in existing queues without deleting entries or history.
- Simplify queue-row presentation: pending/queued is implicit, stopped is shown only for the current item, and meaningful terminal or diagnostic outcomes remain visible.
- Clarify the independent meanings of queue selection, current playback, playback history, and item outcome in user documentation.
- Non-goals: changing VLC transport behavior, queue ordering, automatic advancement, playback-history semantics, media files, or path-security boundaries.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `vlcq`: Make queue selection durable and identity-based, enforce singular active playback state, and simplify queue status presentation.

## Impact

- Queue persistence and compatibility normalization in `src/vlcq/database.py` and `src/vlcq/queue.py`.
- Textual selection synchronization and queue rendering in `src/vlcq/tui.py`.
- Focused database, queue-service, and TUI regression tests, plus README status guidance.
- The SQLite schema may require a forward migration or compatible settings entry; existing queue entries and history must be preserved.
- No new dependency, network, credential, privacy, or media-write surface is introduced. The change only updates private local application state and never modifies media files.
