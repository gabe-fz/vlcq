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
