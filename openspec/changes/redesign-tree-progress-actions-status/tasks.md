## 1. Watched Threshold and Shared Classification

- [x] 1.1 Add `VLCQ_WATCHED_PERCENT` resolution with a 90-percent default and strict 1–100 integer validation, and verify focused configuration tests cover default, override, and invalid startup behavior.
- [x] 1.2 Add shared clamped-percentage and threshold-based watched helpers that preserve explicit completion and handle unknown duration, and verify unit tests cover boundary, overrun, explicit-completion, and unknown-duration cases.
- [x] 1.3 Thread the resolved threshold through CLI startup, TUI construction, resume/playback decisions, and version-1 progress export, and verify CLI/core tests show consistent default and custom-threshold classification without changing the export shape.
- [x] 1.4 Update Clear completed and watched replay/resume behavior to use derived watched classification without rewriting queue outcome or completion evidence, and verify queue/controller tests cover safe active removal, no threshold-triggered advancement, and start-from-zero replay.

## 2. Root-Confined Tree Discovery

- [x] 2.1 Add path-keyed tree entry models and root-confined recursive discovery with canonical deduplication and symlink-cycle prevention, and verify path tests cover nesting, natural order, dot/unsupported exclusion, aliases, unreadable folders, and escape rejection.
- [x] 2.2 Replace current-folder browser state with lazy root/branch caches, expansion state, and depth-first visible flattening, and verify headless tests can expand multiple branches while keeping their ancestors and sibling branches in one Files view.
- [x] 2.3 Preserve path-based highlight, explicit/hidden selections, row identity, expansion, and scroll anchors across branch changes, section collapse, resize, and history refresh, and verify focused TUI tests exercise each preservation case.
- [x] 2.4 Implement cancellable asynchronous whole-tree search and All/In progress/Not watched filtering with ancestor context and stale-generation rejection, and verify delayed-discovery tests cover nested matches, custom thresholds, root changes, branch collapse, and responsive input.

## 3. Filename and Item Progress Presentation

- [x] 3.1 Implement literal semantic filename-span rendering for folders, stems, bracketed/numeric/punctuation parts, and extensions without episode inference, and verify rendering tests reconstruct exact markup-like and long filenames while exposing distinct styles.
- [x] 3.2 Add fixed-width read-only item progress components to Files and Queue for positive history, including partial/watched colors, whole percentages, and unknown-duration presentation, and verify tests cover no-history omission, 75 percent, threshold boundary, explicit completion, and click non-interactivity.
- [x] 3.3 Integrate item bars with fingerprint-matched asynchronous history updates and stable row reuse, and verify polling/history tests retain highlight, tree expansion, scroll, queue state indicators, and unchanged observation timestamps.
- [x] 3.4 Update Details and filter labels from completion-only language to threshold-aware watched information while retaining resume/furthest/duration evidence, and verify Details/filter tests cover absent, partial, watched, legacy, and replaced files.

## 4. Compact Action Bars

- [x] 4.1 Add one-cell-high Files controls for Open, Search/filter, Add, and overflow using existing identity-safe dispatch paths, and verify mouse tests cover highlighted versus selected-batch targeting, disabled states, focus restoration, and overflow completeness.
- [x] 4.2 Add one-cell-high Queue controls for Play/pause, Next, Clear completed, and overflow, and verify mouse tests cover confirmation, current-item safety, empty/disabled states, undo/reorder/removal overflow actions, and no media-file mutation.
- [x] 4.3 Add Previous, Play/pause, Next, and overflow controls to the player area, and verify transport/reconnect/help/quit tests show direct and overflow actions share connection and targeting semantics.
- [x] 4.4 Update context menus, tooltips, keyboard navigation, and responsive labels so promoted actions are not needlessly duplicated yet every existing action remains reachable, and verify right-click interception and 80-column headless tests.

## 5. Reworked Player and Status Area

- [x] 5.1 Recompose the bottom area as a fixed bounded summary, thick live progress track/percentage, player action bar, and one-line notice, and verify idle, connected, unknown-duration, long-error, and active-playback rendering tests.
- [x] 5.2 Restrict absolute seeking to the live bar while keeping item history bars inert, and verify seek tests cover valid clicks, unknown duration, disconnected/wrong-media state, and natural-end inference invalidation.
- [x] 5.3 Tune responsive CSS so section headers/actions and the taller status remain reachable and each populated pane shows at least five compact items at 80-by-24, 120-by-40, and 80-by-50, and verify resize tests preserve all view state.

## 6. Documentation and Release Verification

- [x] 6.1 Update README documentation for the expandable tree, filename syntax colors, watched threshold/environment setting, per-item bars, direct/overflow controls, filters, and taller status area, and verify documented keys and labels match the implemented UI.
- [x] 6.2 Run `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`, and `.venv/bin/python -m mypy src`, fixing all regressions and verifying the complete offline suite passes; no real-VLC smoke run is required because process/HTTP integration is unchanged.
- [x] 6.3 Reinstall the completed CLI globally with `pipx install --force .` and verify the globally resolved `vlcq` reports/runs the updated installation in offline mode.

## 7. Verification Remediation

- [x] 7.1 Make recursive search cancellation cooperative inside the discovery worker and verify superseded scans stop without applying stale results.
- [x] 7.2 Serialize shared SQLite access and move TUI/controller database mutations off the event loop, with a delayed-write responsiveness test.
- [x] 7.3 Remove promoted direct actions from section/player overflow menus while retaining contextual and less-common actions.
- [x] 7.4 Add focused coverage for watched configuration/classification/export/replay, recursive confinement and deduplication, multi-branch tree state, progress bars, direct controls, live seeking, filtering, cancellation, and responsive writes.
