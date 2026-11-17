# Idle rendering and battery use

## Investigation

An earlier live `vlcq` process was observed using roughly 23–56% CPU. It had
exited before profiling, so the exact historical workload was not reproduced.
A separate, temporary 100-file library reproduced substantial idle UI work
without launching VLC or touching the user's database.

The application refreshes playback once per second. Two rendering paths made
that unnecessarily expensive:

- Textual 1.x `Static.update()` calls `refresh(layout=True)` even if the supplied
  text is identical. Browser/queue labels, subtitles, coverage, progress, and
  headers repeatedly invalidated layout without changing their visible content.
- The width-based responsive pass showed state-dependent actions such as queue
  Sort/Undo; the action-state pass immediately hid them again. Even an empty
  queue therefore caused repeated whole-screen layout work at medium/wide sizes.

## Fix

Use a content-aware update helper on regularly refreshed fields. Compare Rich
Text spans/content **and** base style/layout attributes: Rich's own equality
ignores base style, so equality alone could hide real visual changes. Progress
still refreshes whenever its displayed second, percentage, bar or style changes.

Only apply width-only visibility to width-only controls. Let the action-state
pass remain the sole owner of buttons whose visibility also depends on queue,
selection, undo or browsing state.

Playback polling, observation intervals, coverage accounting, history reloads,
file identity validation, subtitle inspection and user data are unchanged.
This is a rendering optimization, not a pause in playback observation or a
cache that can conceal file replacement.

## Measurement and checks

A headless Textual app at 100×35 with 100 synthetic `.mkv` entries, an empty
queue, a temporary database and `no_vlc=True` was warmed for three seconds,
then profiled for six seconds with `cProfile` and `time.process_time()`:

| | Before | After |
|---|---:|---:|
| Process CPU time | 2.006 s | 0.453 s |
| Python calls | 6.35 million | 0.79 million |

That is about **77% less profiled CPU time** in this isolated idle scenario.
Profiling adds overhead; this is not a claim of a 77% battery-life improvement
or a measurement of real VLC video decoding. Filesystem/history scanning still
scales with library size and remains a possible future optimization.

The 100×35 before/after screenshots rasterized to identical PNG hashes.
Regression tests cover unchanged rows, sub-second progress, real progress
changes, watched styling, base-style-only changes, and transient action
visibility. The full suite passed (183 tests; four opt-in VLC integrations
skipped), along with targeted Ruff and mypy checks.

## Follow-up: queue rows (2026-11-17)

With 30 queue entries and 100 files, the same harness measured 1.83 s of
process CPU per 6 s idle. Each tick replaced every queue row's classes with
`set_classes`, which re-applied the stylesheet to all rows even when nothing
changed, refreshed each row twice, and called `QueueService.entries()` (one
SQLite query plus a resolve/stat per entry) about 26 times.

Rows now use `set_class`, which is a no-op when unchanged, are refreshed once,
and a single `entries()` result is threaded through header and button updates.
The same scenario measured 0.67 s (about 63% less), with 8 `entries()` calls
per tick instead of 26. `set_classes` also stripped ListView's own `-highlight`
class every second, so the focused Queue row lost its focus highlight; that is
now preserved.

## Follow-up: history reloads (2026-11-17)

Every tick previously re-read SQLite history for all visible Files and Queue
paths (`force=True`), resolved each path on the event loop, used one worker
thread hop per file, and re-rendered both lists even when nothing changed.

- `Database` records which canonical paths this connection wrote
  (`ensure_media`, `merge_progress`, `merge_coverage`) and checks
  `PRAGMA data_version` for commits from other connections (for example a
  concurrent CLI). `history_changes_since()` returns the changed paths, or
  `None` when an external commit means everything must be revalidated.
- The TUI drops only those cache entries. **File fingerprints are still
  re-stat'ed on every tick**, so replaced files never show stale history.
- Canonicalization and stat run off the event loop in one batch per 200 paths.
- A history epoch advances whenever loaded history changes; Files/Queue skip
  their extra re-render pass when they have already applied the latest epoch
  (Files also re-renders when queue membership changes).

Same harness (100 files, 30 queued, idle): 0.67 s → about 0.27 s process CPU
per 6 s, roughly 85% below the original 1.83 s. `tests/test_history_cache.py`
covers own writes, external commits, and file replacement.
