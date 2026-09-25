## 1. VLC Playlist Interface

- [x] 1.1 Add explicit `--no-repeat`, `--no-loop`, and `--no-random` owned-process flags and verify launch-argument tests prove saved playback modes are overridden.
- [x] 1.2 Add defensive nested playlist parsing that returns stable VLC ids and canonical local paths, and verify tests reject malformed, remote, and ambiguous entries without exposing raw payloads.
- [x] 1.3 Add typed authenticated HTTP operations to inspect, enqueue, remove, and replace the bounded VLC playlist window, and verify mock-transport tests cover commands, URL encoding, content validation, active-item preservation, and failures.

## 2. Controller Synchronization

- [x] 2.1 Add controller state for the active/staged VLC identities and an idempotent two-item window synchronizer; verify fake-client tests cover initial play, no successor, reconnect invalidation, and successor replacement without active replay or seek.
- [x] 2.2 Resolve the next eligible queue entry with existing missing-file and root-confinement policy, and verify missing/unsafe entries are never staged or modified and remain diagnostically represented.
- [x] 2.3 Route explicit play, `vlcq` next/previous, natural advancement, and successor-affecting queue mutations through synchronization; verify focused controller and TUI/CLI tests cover add, play-next, reorder, remove, clear, undo, and sort while active playback remains uninterrupted.

## 3. Observed VLC Transitions

- [x] 3.1 Reconcile an observed staged-successor identity through the transition lock without issuing `in_play`, and verify tests cover direct playing-to-playing native Next, stale generations, duplicate observations, and at-most-one transient queue state.
- [x] 3.2 Preserve conservative outcome/history semantics across observed transitions, and verify near-end evidence records completion while earlier native Next records skipped and both retain the last trustworthy old-item progress.
- [x] 3.3 Fail closed for unexpected, malformed, remote, missing, or no-longer-staged media and surface recoverable guidance; verify no unrelated queue state or history changes and no media bytes are modified.
- [x] 3.4 Remove the former VLC item and stage the following successor after reconciliation, and verify repeated advancement keeps the VLC playlist bounded to the current item plus at most one successor.

## 4. Documentation and Verification

- [x] 4.1 Update `README.md` to explain native VLC Next support, bounded synchronization, repeat/random startup behavior, native Previous limitations, and the `vlcq` controls; verify the documented controls match implemented behavior.
- [x] 4.2 Extend the opt-in generated-media VLC 3 integration test to exercise native `pl_next`, direct natural transition, bounded playlist contents, queue outcomes, and clean shutdown without user media or credentials; run it with `VLCQ_REAL_VLC=1` or record an explicit environmental skip.
- [x] 4.3 Run `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`, and `.venv/bin/python -m mypy src`; verify all applicable checks pass.
- [x] 4.4 Reinstall the CLI globally with `pipx install --force .` and verify the globally resolved `vlcq` starts from the updated installation.
