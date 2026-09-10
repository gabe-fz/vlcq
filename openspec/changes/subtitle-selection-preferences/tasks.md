## 1. VLC Subtitle Protocol

- [ ] 1.1 Characterize the installed supported VLC 3 HTTP subtitle-track payload and selection/off commands using only generated temporary multi-track media, record sanitized fixtures, and verify no user media or non-loopback endpoint is touched
- [ ] 1.2 Add immutable subtitle track/descriptor models plus strict tolerant payload parsing for IDs, active state, language, title, and tri-state characteristics; verify focused parser tests cover valid variants, missing metadata, malformed input, and bounded labels
- [ ] 1.3 Add narrow VLC client operations to list and select/off subtitles while preserving the command allowlist and status validation; verify mocked HTTP tests assert exact parameters and safe handling of rejected or malformed responses

## 2. Preference Semantics and Persistence

- [ ] 2.1 Implement conservative language and characteristic normalization plus deterministic remembered/English ranking; verify table-driven tests cover full-dialogue priority, signs/songs, forced, SDH, unknown traits, stable ties, and no-English fallback
- [ ] 2.2 Implement conservative scoped show-key inference for season folders, episodic root filenames, unrelated libraries, generic names, and unsafe/out-of-root paths; verify unit tests demonstrate both intended matches and deliberate no-match cases without persisting absolute paths
- [ ] 2.3 Add a transactional schema migration and typed database APIs for default-on English preference, default-off show remembering, and versioned bounded show descriptors; verify migration, rollback injection, restart, malformed-value fallback, root scoping, upsert, and file-permission tests preserve existing queue/history data

## 3. Controller Arbitration

- [ ] 3.1 Add generation-bound subtitle discovery and explicit selection under the controller transition lock, including identity revalidation and successful-choice persistence; verify async tests reject stale targets and show manual track/Off choices do not alter queue, position, or media
- [ ] 3.2 Add bounded automatic application with precedence remembered show choice (including Off) over English full-dialogue over other English over VLC default; verify async tests cover delayed tracks, native successor generations, reconnect/cancellation, missing metadata, and one successful automatic application per generation
- [ ] 3.3 Add explicit-wins race protection and recoverable failure reporting without command loops; verify concurrent tests show delayed automation never overrides a successful manual selection and failed selections leave playback running

## 4. Textual User Experience

- [ ] 4.1 Add a compact scrollable subtitle picker with Off, active marker, metadata labels, mouse/keyboard activation, cancellation, and focus restoration; verify headless Textual tests at 80-by-24 cover selection and dismissal
- [ ] 4.2 Add Subtitles to matching current Files and Queue right-click/Shift+F10 menus with immutable generation-aware targets; verify TUI tests omit or disable it for inactive/stale rows and prove opening menus never loads, enqueues, probes, or plays media
- [ ] 4.3 Add checkmarked Remember subtitles by show and Prefer English subtitles actions to the selection-independent Queue/application overflow; verify they remain reachable with an empty queue, persist across restart, default false/true respectively, and do not delete remembered choices when disabled
- [ ] 4.4 Route subtitle discovery/selection failures through bounded notices and update help/README with precedence, show inference limits, and privacy behavior; verify UI tests retain full error details on demand and documentation matches observable controls

## 5. End-to-End Verification

- [ ] 5.1 Extend the opt-in real-VLC test to generate a disposable multi-track file, enumerate its subtitle tracks, select a track, select Off, and verify playback remains on the same media; run `VLCQ_REAL_VLC=1 .venv/bin/pytest tests/test_integration_real_vlc.py`
- [ ] 5.2 Run `.venv/bin/pytest`, `.venv/bin/ruff check .`, and `.venv/bin/mypy src`, then verify all pass without regressions in queue, coverage, migration, or TUI behavior
- [ ] 5.3 Reinstall the completed CLI with `pipx install --force .` and verify the globally resolved `vlcq` starts the updated installation without touching a user library
