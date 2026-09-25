## 1. Persistence and State Invariants

- [x] 1.1 Add validated `selected_entry` persistence for the active queue, including clear-on-root-change and clear-on-removal behavior, and verify focused database tests cover valid, stale, and cross-queue identities.
- [x] 1.2 Add an atomic current-entry transition that normalizes non-current playing, paused, and stopped rows to queued while preserving terminal outcomes, and verify queue tests cover switching away from a stopped item and terminal-state preservation.
- [x] 1.3 Normalize stale current/selected identities and duplicate transient states when an existing queue opens, and verify a compatibility test preserves entry IDs, order, media fingerprints, progress, and terminal outcomes without touching media files.
- [x] 1.4 Route queue and controller current-state mutations through the invariant-preserving boundary, and verify existing transition, stop, retry, completion, removal, and resume tests pass.

## 2. Durable TUI Selection and Presentation

- [x] 2.1 Persist queue highlights immediately from mouse and keyboard events and restore them by entry ID without triggering playback, and verify TUI tests cover click, keyboard movement, periodic refresh, reorder, collapse/expand, and application restart.
- [x] 2.2 Safely clear a selected identity when its entry is removed or the library changes without retargeting an unrelated row, and verify focused stale-selection and removal tests pass.
- [x] 2.3 Omit the queued badge, restrict playing/paused/stopped rendering to the current row, and retain terminal/diagnostic outcomes and independent history labels; verify queue-rendering tests include repaired multiple-stopped legacy data.
- [x] 2.4 Update README guidance to distinguish queue highlight, current playback, pending position, outcomes, and history, and verify documented labels match the TUI constants and tests.

## 3. Validation and Delivery

- [x] 3.1 Run the complete pytest suite and verify all tests pass without enabling unmanaged media playback.
- [x] 3.2 Run Ruff and mypy with the project commands and resolve all reported issues.
- [x] 3.3 Reinstall the CLI into the global tool environment with the established installer and verify the globally resolved `vlcq` command uses the updated installation.

## 4. Verification Remediation

- [x] 4.1 Preserve stale-selection repair through initial TUI rendering so an invalid or cross-queue selected identity remains cleared instead of selecting the first unrelated row, and add a restart test verifying no selection or playback retargeting occurs.
- [x] 4.2 Report actionable recovery guidance when a database migration fails while preserving the original database, and add an injected-failure test verifying rollback, unchanged source data, and useful CLI guidance.
- [x] 4.3 Keep the persisted queue highlight visibly marked while Files has focus, render compact dialog button labels within their one-line height, and add restart and small-terminal regression coverage.
