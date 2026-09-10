## 1. Coverage model and safe persistence

- [x] 1.1 Add pure interval-union, clipped coverage, floor-percentage, and coverage-threshold helpers separate from legacy position classification; verify unit tests for disjoint/adjacent/overlapping ranges, duplicates, rewatches, unknown/corrected duration, and 99.x% never displaying 100%.
- [x] 1.2 Add transactional coverage-range storage and nullable tracking initialization with fresh-schema and v1/v2 migrations; verify migration tests preserve every legacy field and queue/current/selected identity, create no inferred ranges, roll back injected failures, and retain private state permissions.
- [x] 1.3 Add serialized range merges and bounded read-only history projections tied to canonical fingerprints; verify idempotent repeated writes, reopen durability, sparse-range growth, replacement rejection, out-of-root/symlink safety, and browsing without record/timestamp mutation.

## 2. Trustworthy playback evidence

- [x] 2.1 Expose validated finite positive VLC playback rate and capture monotonic observation request/response timing; verify parser tests for normal/variable rates, missing/invalid/nonfinite rate, malformed status, and unchanged path/playlist validation.
- [x] 2.2 Implement the pure continuity accumulator with an injected clock, five-second qualification, five-second maximum gap, and bounded second-resolution tolerance; verify deterministic tests for normal/slow/fast playback, quantized repeated positions, stalls, short previews, large seeks, repeated scrubbing, backwards jumps, long latency, and rejected gaps without sleeping.
- [x] 2.3 Integrate evidence into current-item observations and reset before seek/resume/start-over, pause/stop, next/previous, item switch, reconnect, and failed commands; verify controller tests show matching live/resume state changes without awarding skipped ranges or crediting stale/wrong-item responses.
- [x] 2.4 Flush only qualified ranges through existing serialized writes and bounded final observations; verify pause, removal, quit, crash/reopen, failed final polling, and native successor transitions preserve committed coverage without interpolating unobserved gaps or end tails.
- [x] 2.5 Replace single near-end-sample inference with recent qualified continuity while keeping natural queue advancement separate from coverage; verify seek-near-end plus stop/Next never forces full history, genuine native advancement adopts the successor once, and playback of only the final portion credits only that portion.

## 3. Resume policy, filtering, and compatible export

- [x] 3.1 Switch internal watched/replay/filter policy to coverage while retaining explicit queue outcomes for finished-entry navigation and Clear completed; verify default/custom threshold tests, legacy-only Not watched, coverage-based In progress, no threshold-triggered auto-advance, and clear confirmation/non-destructive semantics.
- [x] 3.2 Separate row current-view projection from historical coverage: live matching position for active media, trustworthy saved resume for inactive media, zero on Start over, and explicit unknown for legacy-only current position; verify rewatch, restart/resume, rewind, cancel, and unknown-duration tests.
- [x] 3.3 Add coverageMs/coveragePercent/coverageWatched to version-1 JSON without reinterpreting existing fields; verify CLI golden/compatibility tests for divergent legacy and coverage values, uninitialized versus zero coverage, unknown duration, unchanged legacy timestamps, root confinement, and replaced-file rejection.

## 4. Files + Queue presentation

- [x] 4.1 Replace row history bars/badges with Queue filename/current bar/current percent/current-total time/historical percent and Files filename/historical percent; verify TUI tests show independent rewatch progress, no Files bar, compact unknown values, literal filename styling, current/highlight distinction, and retained actionable missing/failed indicators.
- [x] 4.2 Remove the persistent status pane, player toolbar/menu, visible transport entries, seek surface, and obsolete widget/CSS/event references; verify idle/playing/paused TUI tests find no player widgets, state badge repetition, duplicate active summary, or widget-query exceptions during polling.
- [x] 4.3 Preserve keyboard transport and contextual Play/Resume/Start over; move Help/reconnect/Quit to selection-independent Queue overflow and restore on-demand Queue Details; verify mouse activation, empty queue, both panes collapsed, text-entry shortcut isolation, unavailable transport, reconnect idempotence, and inert row bars.
- [x] 4.4 Add transient dismissible one-line connection/error notices with on-demand full text and no idle reserved space; verify error/recovery/dismissal tests and no persistent healthy/idle summary.
- [x] 4.5 Verify stable responsive two-pane layout at 80x24, 120x40, and 80x50 with long filenames and durations; assert at least five rows per populated expanded pane and preserved row identity, selection, highlight, expansion, scrolling, and menu targets during slow identity checks and live updates.

## 5. Documentation and integrated verification

- [x] 5.1 Update README/Help with the two-pane workflow, VLC versus keyboard transport, supported native Next limitation, current-view versus historical coverage, five-second qualification/undercount limitations, legacy export distinction, and safe migration/rollback; verify examples and action references match the implemented UI and exported fields.
- [x] 5.2 Update affected existing suites (`test_redesign_progress_tree.py`, `test_simplify_ui.py`, `test_cli_tui.py`, `test_queue_controller.py`, `test_ux_e2e.py`) without dropping unrelated safety coverage; run `.venv/bin/pytest`, `.venv/bin/ruff check .`, and `.venv/bin/mypy src` and record passing results or concrete blockers.
- [x] 5.3 Extend and run real installed VLC 3 integration checks with `VLCQ_REAL_VLC=1 .venv/bin/pytest tests/test_integration_real_vlc.py`; verify rate/status timing assumptions, normal playback coverage, VLC-native seek/scrubbing/pause/Next, seek-near-end, replay overlap, successor identity, and clean owned-process shutdown. A skipped environment-dependent check is not passing evidence; report it as a release blocker.
- [x] 5.4 Run `openspec validate simplify-progress-with-watched-coverage --strict` and review implementation against every delta scenario; verify no conflicting old player/badge/furthest-classification behavior remains in the affected contracts.
- [x] 5.5 Reinstall the implemented CLI globally using the established installer or `pipx install --force .`; verify the globally resolved `vlcq --help` and an isolated temporary-library smoke test use the updated installation, then commit/push completed changes directly to main while excluding unrelated user files.
