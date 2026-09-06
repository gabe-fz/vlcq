## Why

Users cannot easily tell which videos have recorded progress before adding them, where playback will resume, or which object a control affects. Existing mouse controls leave essential workflows keyboard-dependent, while queue removal can detach still-playing VLC media from observation.

## What Changes

- Surface local watch history in library and queue rows, with All / In progress / Not completed filters, filename search, selected-item details, and already-queued indicators. Distinguish recorded progress from proof of time watched.
- Persist the last trustworthy playback position separately from historical maximum progress, and expose Resume / Start over choices without erasing history.
- Clarify Add to end / Play next / Play now actions and move library-selection actions into the library pane; retain keyboard equivalents.
- Add readable playback times, remaining time, visible transport and reconnect controls, clickable selection, mouse-operable dialogs, and seek-by-click for known durations.
- Add selection summaries, session-local undo for removal/clear, stable selection and scrolling, and explicit safe handling of active-item removal.
- Preserve the version-1 progress export contract and non-destructive, root-confined media handling. No breaking CLI or export changes are intended; toolbar placement and playback-choice UI deliberately change.

Non-goals: episode-title or codec enrichment, external metadata services, media scanning for unknown durations, watch-time coverage analytics, manual watched/unwatched overrides, drag-and-drop, right-click menus, persisted undo history, and remote synchronization.

## Capabilities

### New Capabilities

None. These additions extend the existing application capability rather than create a separate subsystem contract.

### Modified Capabilities

- `vlcq`: Watch-history presentation, trustworthy resume persistence, explicit queue/play actions, complete mouse operation, safe queue mutation, and responsive selection handling.

## Impact

- `src/vlcq/database.py` and `models.py`: transactional schema migration, separate resume position and playback timestamp, bulk read-only history projections, undo-compatible queue snapshots.
- `src/vlcq/queue.py`, `controller.py`, and `vlc.py`: deterministic play-next placement, serialized playback transitions, validated absolute seek, final observation capture, safe stop-before-remove behavior, and reconnect lifecycle.
- `src/vlcq/tui.py` and `cli.py`: history-aware UI, explicit playback choices including `resume`, mouse controls, filters, responsive layout, and contextual actions.
- Unit/controller/CLI/Textual tests and opt-in real VLC verification; update README controls and resume semantics during implementation.
- No new runtime dependency is planned. Existing databases require a preservation-safe migration. History remains local; UI reads must not create media history or leak unrelated paths. All new mutation and undo paths must retain canonical root and fingerprint validation and never modify media files.
