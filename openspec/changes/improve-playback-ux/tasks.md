## 1. Durable history and resume data

- [x] 1.1 Add a transactional v1-to-v2 migration with nullable resume position and last-played timestamp; verify existing queue identities, fingerprints, maxima, completion, and version-1 exports survive, and injected migration failure rolls back without replacing the database.
- [x] 1.2 Add typed bulk read-only history projections for canonical fingerprint-matched files; verify rendering queries do not insert media or alter timestamps and replaced/out-of-root files never inherit history.
- [x] 1.3 Implement independent resume/maximum merging, trustworthy timestamp updates, and labeled legacy fallback; verify rewind, start-over, transient zero, unknown duration, completion preservation, and legacy unknown-time cases with database tests.

## 2. Safe playback transitions and recovery

- [x] 2.1 Serialize controller transitions and guard poll responses with playback generation and expected identity; verify delayed prior-item and pre-seek responses cannot overwrite resume state or advance the queue.
- [x] 2.2 Capture bounded final observations before switching, stopping, active removal, and quit; verify failures retain the last valid position and wrong-media/unavailable observations are ignored.
- [x] 2.3 Add expected-media readiness and validated absolute seek for resume/start-over; verify ordering, timeout/error reporting, rewind persistence, and invalidation of pre-seek completion evidence with fake VLC clients.
- [x] 2.4 Centralize playback policy for explicit play, current-item activation, batch add-and-play, next/previous, natural advancement, and CLI resume; verify cancel has no queue/playback side effects, current-item activation never reloads, and incomplete autoplay resumes without a modal.
- [x] 2.5 Make reconnect idempotent and independent of queue selection; verify overlapping requests leave one owned process/client/poller and recovery does not autoplay or terminate unrelated VLC processes.

## 3. Deterministic queue editing and undo

- [x] 3.1 Implement identity-based atomic Add to end and Play next batch operations; verify deduplication, natural selection order, existing-entry movement, active-item exclusion, idle insertion, stable unselected order, and all-or-nothing invalid-operand failure.
- [x] 3.2 Route active removal/clear through confirmed stop before database deletion; verify no automatic successor starts, stop failure preserves the entire removal set, and post-stop storage failure leaves visible stopped entries with retained progress.
- [x] 3.3 Add single-level session-local removal/clear undo snapshots and atomic restore; verify order/history preservation, no playback restart, missing-file restoration, unsafe/replaced-file rejection, and no partial restoration.
- [x] 3.4 Invalidate undo on subsequent queue mutations, automatic advancement, root change, and shutdown but not ordinary polling/pause/seek; verify the corresponding controller/queue tests and visible expiration feedback.

## 4. History-aware library and selected details

- [x] 4.1 Render independent watch history and queue state in library/queue rows, with queued badges and readable known progress; verify completed-but-replaying, no-history, replacement, missing, and unknown-duration presentations in Textual tests.
- [x] 4.2 Add current-folder filename search and All / In progress / Not completed filters while keeping directory navigation; verify category membership, no implicit queueing, and input typing does not trigger global shortcuts.
- [x] 4.3 Add selected-item details and selection summaries with hidden/other-folder counts and clear-selection; verify filtering/navigation preserve explicit selections and single-item play does not accidentally target a batch.
- [x] 4.4 Preserve row identity, highlight, scrolling, and details target during history refresh; verify delayed filesystem checks do not block input and repeated polling does not rebuild unchanged rows or create history.

## 5. Explicit keyboard and mouse interaction

- [x] 5.1 Move library actions into its pane and add contextual queue controls plus a persistent active-player bar; verify each button and keyboard equivalent targets the correct object regardless of current pane focus.
- [x] 5.2 Add Resume / Start over / Cancel dialogs and direct contextual actions, including the CLI resume entry point and legacy fallback label; verify choices dispatch the centralized policy and cancel does not insert or reorder media.
- [x] 5.3 Add non-playing row clicks, clickable checkboxes, mouse folder navigation, and buttons for all confirmation/help/quit dialogs; verify mouse-only completion and cancellation paths using Pilot clicks rather than keyboard shortcuts.
- [x] 5.4 Add visible transport/reconnect controls, readable elapsed/total/remaining times, and guarded seek-by-click; verify duration-to-coordinate mapping, clamping, unknown-duration/disconnected disabling, and no accidental seek on unrelated UI clicks.
- [x] 5.5 Implement compact layout and mouse-accessible overflow/details at 80x24 while preserving wide layout and long-name scrolling; verify essential actions remain reachable at 80x24 and 120x40 without focus or scroll jumps.

## 6. Integration, documentation, and delivery

- [x] 6.1 Add an end-to-end mouse workflow covering folder opening, selection, queue insertion, resume choice, transport, reorder, removal/clear, undo, reconnect, and quit; verify it at narrow and wide sizes alongside existing keyboard regression tests.
- [x] 6.2 Extend opt-in real VLC tests with temporary generated media for resume seek, rewind, pause, natural advancement, stop-before-removal, reconnect, and shutdown; run on supported macOS VLC 3 and record results without user media or credentials, explicitly reporting environmental skips.
- [x] 6.3 Update README with controls, progress-versus-coverage wording, resume fallback, active removal, undo limits, and consistent private backup/rollback instructions; verify documented labels and workflows against the implemented UI and CLI.
- [x] 6.4 Run `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`, `.venv/bin/python -m mypy src`, and strict OpenSpec validation; verify pass results and manually check mouse behavior in a macOS terminal at 80x24 and wide size.
- [x] 6.5 Reinstall the implemented project with `pipx install --force .` and verify the globally resolved `vlcq --help` plus an isolated temporary-library smoke test; report installation path and outcome without touching the user's normal queue database.
