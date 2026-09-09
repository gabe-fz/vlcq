## Why

VLC's native Next control currently operates on an internal playlist that does not represent the `vlcq` queue, so it can restart or stop the active item instead of advancing `vlcq`. Users should be able to use the visible VLC window's Next control without losing deterministic queue state or playback history.

## What Changes

- Keep a bounded VLC playlist window containing the active `vlcq` item and its eligible successor rather than sending only one item to VLC.
- Reconcile an observed VLC transition to that successor as a `vlcq` queue transition, classifying the old item as completed only with independent near-end evidence and otherwise as skipped.
- Explicitly disable VLC repeat-current, repeat-all, and random playback at owned-process startup so saved VLC preferences cannot make native Next restart or reorder media.
- Refresh the bounded VLC window after playback and relevant queue mutations while retaining `vlcq` as the authoritative queue.
- Fail closed on unexpected VLC media identities, missing successors, malformed status, or synchronization failures.
- Add fake-client and opt-in real-VLC coverage for native Next, natural completion, history capture, queue mutation, and failure behavior.
- Non-goals: exposing the entire queue in VLC, adopting arbitrary files opened directly in VLC, supporting native Previous in this change, changing media files, supporting VLC 4, or controlling an unrelated VLC process.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `vlcq`: Change the owned-VLC and queue-transition contract so a bounded successor may be staged in VLC and a native VLC Next transition advances the authoritative `vlcq` queue safely.

## Impact

- `src/vlcq/vlc.py`: owned-process flags and authenticated HTTP playlist commands/identity parsing.
- `src/vlcq/controller.py`: playlist-window synchronization and externally observed successor transitions.
- Queue-mutating CLI/TUI paths may trigger synchronization through the controller boundary.
- Unit/controller/CLI/Textual tests and `tests/test_integration_real_vlc.py`; README documentation for native versus `vlcq` transport controls.
- No new dependency, network exposure, telemetry, or destructive media operation. VLC remains dedicated and loopback-controlled, and absolute media paths and credentials remain private.
