## 1. Baseline and specification ordering

- [ ] 1.1 Review the predecessor `improve-playback-ux` and preserve its existing uncommitted edits; synchronize its accepted requirements before this delta, verifying that all MODIFIED requirement names exist in the resulting main spec and strict validation passes.
- [ ] 1.2 Preserve the user's private baseline and create a synthetic populated UI fixture with bracketed long filenames, absent/progress/completed history, selection, and active/failed queue states; capture and inspect pre-change images at 80x24, 120x40, and 80x50 and record private image paths.

## 2. Stacked sections and compact rows

- [ ] 2.1 Add failing layout tests for Files above Queue, one-line pinned headers, all four collapse combinations, resize preservation, focus transfer out of collapsed bodies, and at least five visible rows per expanded list at 80x24; implement the section layout and verify those tests pass.
- [ ] 2.2 Add failing row-rendering tests for one-line literal bracketed filenames, compact checkbox hit targets, filename truncation, independent semantic state/history styling, and no absent-history badge; implement shared row presentation and inspect focused/unfocused rendered rows as well as passing tests.
- [ ] 2.3 Add failing tests for read-only Details showing full path, absent/known history, resume/furthest positions, unknown duration/time, and fingerprint replacement; implement the on-demand view and verify no media-record or observation-time mutation.

## 3. Context menus and action routing

- [ ] 3.1 Add failing tests for right-click versus ordinary click on files/folders/queue rows, visible-trigger and Shift+F10 parity, Escape/outside dismissal, focus restoration, and edge-clamped scrollable menus; implement the popup and verify all entry points at 80x24.
- [ ] 3.2 Add failing tests for stable menu identity under queue reordering/removal, root changes, polling, and unsafe path replacement; implement typed context targets and centralized enablement/dispatch, verifying no stale-index action or partial unsafe mutation occurs.
- [ ] 3.3 Replace persistent browser/queue toolbars with context actions using existing operation paths; update failing mouse tests to cover Add to end, Play next, single-item Play now versus batch Add & play, Resume/Start over/Cancel, sorting, reordering, queue-only removal, confirmed clear, and undo, retaining media-byte and playback-safety assertions.
- [ ] 3.4 Add failing tests for on-demand search and filters, protected text input, cross-folder selections, visible active filters and selected/hidden counts including collapse, and clear selection; implement compact discovery and header summaries and verify targeting remains explicit.

## 4. Bottom status and remaining controls

- [ ] 4.1 Add failing tests for a single live player summary, idle/disconnected state, unknown time/percentage, bounded long errors with full notice access, and player visibility when both lists collapse; replace duplicated status and permanent details/footer areas and verify a maximum three-line bottom area.
- [ ] 4.2 Add failing bottom-menu and compact-dialog workflow tests for pause/resume, previous/next, relative and absolute seek, reconnect with an empty queue, active-player details, Help, and Quit/Cancel; implement controls via existing actions and verify no seek with unknown/mismatched media or autoplay on reconnect.
- [ ] 4.3 Remove obsolete hidden toolbar dependencies and update shortcut/help documentation; verify repository searches show no dispatch/enablement dependence on removed widget IDs and existing keyboard workflow tests pass.

## 5. Acceptance and delivery

- [ ] 5.1 Run end-to-end mouse-only and keyboard-fallback workflows at 80x24, 120x40, and 80x50, including menu-open polling, collapse/expand, and resize; verify equivalent queue/history outcomes and unchanged source media.
- [ ] 5.2 Capture and personally inspect post-change images using the baseline fixture and sizes, plus collapsed states, long notices, and edge menus; record image paths and verify colors, readable rows, compact controls, and lack of duplicate prose. Verify right-click delivery in an isolated real PTY; if OS capture is unavailable, document the limitation and request/inspect a post-install user screenshot before claiming visual acceptance.
- [ ] 5.3 Run `.venv/bin/python -m pytest`, `.venv/bin/python -m ruff check .`, and `.venv/bin/python -m mypy src`; require all applicable checks to pass and document skips. Run `VLCQ_REAL_VLC=1 .venv/bin/python -m pytest tests/test_integration_real_vlc.py` if process/controller/HTTP behavior changes, with additional real playback coverage for any changed playback boundary.
- [ ] 5.4 Update README to the verified menus, collapse behavior, compact history and status model; verify examples against the installed help and accepted UI rather than old toolbar screenshots.
- [ ] 5.5 Install the accepted checkout globally with `pipx install --force .`, verify the globally resolved `vlcq --help` and package version from the pipx environment's installed metadata (there is no current `--version` option), and inspect an installed-application UI capture using an isolated database without disrupting existing playback.
- [ ] 5.6 Run strict OpenSpec validation, record acceptance evidence, then synchronize/archive in predecessor-first order only after implementation is accepted; verify scoped main-branch commit/push excludes pre-existing unrelated edits, user data, and private screenshots.
