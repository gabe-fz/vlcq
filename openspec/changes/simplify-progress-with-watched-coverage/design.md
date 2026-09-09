## Context

See proposal.md for motivation and approved scope. `Database.merge_progress` currently takes the maximum supplied position even for untrustworthy observations. Explicit seek/resume paths also call it. `HistoryProjection` and `is_watched` classify maximum position or completion, while `ItemProgress` displays that same history in both panes. Controller end inference uses a near-end observation, not a sustained-playback tracker. SQLite schema version 2 already separates nullable resume position from legacy maximum.

The implemented TUI has Files and Queue header actions plus a large `#status-pane`; removing that pane also removes the current home of reconnect, Help, Quit, transport, and active-file metadata. Those dependencies must be moved or retired, not left as widget queries that fail during polling. The main spec still describes an older header/player arrangement; this change replaces the conflicting presentation contracts.

## Goals / Non-Goals

**Goals:** separate live position, durable resume position, historical coverage, and queue outcomes; make observation evidence testable without sleeping; keep the main screen limited to two useful lists; preserve existing data and export consumers.

**Non-Goals:** human-attention detection, complete reconstruction between HTTP polls, coverage while the controller is absent, per-view event archives, destructive schema cleanup, or expanded native VLC playlist navigation.

## Decisions

### 1. Coverage is a union, not a maximum position or elapsed-time counter

Store sorted, disjoint half-open integer-millisecond ranges keyed to the existing media fingerprint identity, merging overlaps and exactly adjacent ranges transactionally. Sum the union, clipped to known duration, to derive historical coverage. Never bridge an unobserved gap merely to compact storage. Repeated observations and rewatches are idempotent. This is more storage than one maximum but much smaller than per-poll events and answers the actual question; an accumulated timer would wrongly credit repeated segments.

Keep legacy maximum/completion columns for compatibility and resume fallback; do not use them in coverage math. Existing queue state remains an internal transport/outcome concern. Coverage needs only ranges plus a nullable tracking-start marker, not a session log or per-view database table. Browsing alone creates neither marker nor media rows.

Use floor for coverage whole percentages so 99.6% never prints 100%. Unknown/nonpositive duration produces unknown percentage. A corrected longer duration can reduce the percentage, but confirmed ranges never shrink for unchanged content. Raw threshold comparisons, not rounded display values, drive watched policy.

### 2. Qualify continuous playback conservatively

Introduce a pure accumulator with an injected monotonic clock. Each sample includes request/response timing, generation, canonical media identity, VLC playlist identity where available, playing state, media position, duration, and validated playback rate. Extend VLC parsing to expose a finite positive rate; missing/invalid rate means no coverage credit until reliable evidence returns, rather than guessing faster playback is a seek or crediting it incorrectly.

Proposed initial constants: require five seconds of coherent playing observations before a run qualifies, reject observation gaps over five seconds, and allow at most one media-second of timestamp quantization error when comparing position delta with elapsed time times rate. Keep these named and unit-tested; validate against real VLC 3's second-resolution status before release. Pending intervals from the qualifying run can be credited once qualified, but never the jump into that run. Sub-five-second previews intentionally earn no coverage; this conservative rule also applies to very short clips.

Both endpoints must belong to the same validated active identity and generation, show playing at a stable known rate, and advance plausibly over a bounded request-time window. Excessive HTTP latency, backwards movement, rate change, explicit seek (including resume seeks), stop/pause, item change, stale response, reconnect, polling failure, and long gaps break the run. Repeated integer-second positions earn no credit; tolerate them only within the one-media-second quantization window at the known rate, retaining the last advancing anchor, then reset if progress remains stalled. Qualification requires actual positive advancement, not merely time spent reporting playing. A fresh valid sample establishes a baseline without credit; a native VLC seek is detected as a discontinuity by the same timing checks. Explicit transport invalidates pending evidence before commands, even when a command fails.

After qualification, persist accepted intervals promptly using the existing serialized database worker. On pause/switch/quit attempt the existing bounded final observation, but do not interpolate stopped zero, the cross-item interval, or an unseen tail. Natural end can advance the queue and record an outcome without filling uncovered portions or forcing 100%. Near-end inference itself must use recent qualified continuity and be invalidated by seeks; coverage and end inference must not call each other to manufacture evidence.

Polling cannot distinguish every tiny external seek that exactly resembles ordinary playback, nor prove attention. Document this as conservative observed playback, not an anti-cheating guarantee. Prefer undercounting ambiguous playback to awarding skipped portions.

### 3. Current view is playback position, independent of coverage

The active row uses matching live position. Inactive rows use the last trustworthy saved resume position for their current/latest viewing, never historical maximum; legacy-only rows show unknown current position until trustworthy state exists. New unstarted rows may show zero current position; unknown duration remains `--:--` and unknown percent `--%`. Start over explicitly writes zero resume position and updates the row immediately without clearing ranges. Returning with Resume restores that saved position. No distinct durable viewing-session model is needed.

Example:

```text
Files
  episode.mkv                                      hist 85%
Queue
> episode.mkv  [####------] 40%  12:00/30:00          hist 85%
```

Use `hist`, not `max`, because the value is unique played coverage. No history uses a compact dash; initialized tracking with known duration and no qualified coverage shows 0%. Legacy-only history remains unknown in the main row and is explicitly described in Details. A single non-color current marker is independent of highlight. Missing/failed icons remain actionable; ordinary state/outcome badges disappear. Bars in queue rows are read-only, not replacement seek controls.

### 4. Two-pane wrapper, not another player panel

Remove status-pane widgets, CSS, timer/render references, player toolbar/menu, and absolute mouse-seek handler tied to its track. Keep existing keyboard transport shortcuts and contextual Play/Resume/Start over as queue activation actions. Visible pause/previous/next/seek controls live in the owned VLC window. Preserve supported native Next reconciliation; native Previous and arbitrary playlist changes remain unsupported and must fail safely rather than associate wrong history.

Keep compact Files/Queue management actions and collapsible headers. Put application Help, reconnect, and Quit in a small group in the existing Queue overflow, available even with no selected row. Restore on-demand Queue Details now that the status pane no longer supplies its metadata. Details is a modal, not a persistent substitute player. Row-specific actions remain correctly targeted.

Use a transient, dismissible one-line notice only for relevant connection transitions or errors, hidden with no reserved space otherwise. Longer error text is available on demand. No healthy/idle summary or duplicate active filename sits below the lists. Retain at least five rows per expanded pane at 80x24, preserve scroll/highlight/widget identities across polling, and prioritize progress columns while truncating long filenames.

### 5. Coverage policy and legacy export remain explicit

Internal watched classification uses coverage reaching `VLCQ_WATCHED_PERCENT` (default 90). In progress means positive coverage below threshold; Not watched includes unknown/legacy-only coverage. Replay policies use this classification; explicit completed queue outcomes still determine finished queue entries and existing Clear completed eligibility, but never increase history. Keep this distinction documented instead of deleting queue state.

Preserve version-1 `positionMs`, `durationMs`, `watchedPercent`, `completionObserved`, and timestamps with their existing legacy meanings. Add `coverageMs`, `coveragePercent`, and `coverageWatched`; use null for uninitialized coverage, and null percentage/classification for unknown duration. `coverageMs` is zero once tracking is initialized with no qualified ranges. Explicitly document that version-1 legacy classification can differ from coverage-based UI policy. This avoids silently redefining existing consumer fields; a new export version or removal of legacy fields is deferred.

## Risks / Trade-offs

- Conservative qualification undercounts very short previews, final tails, and unstable polling -> document the five-second rule and do not round incomplete coverage up to 100%.
- Native seeks within timing tolerance can resemble playback -> test substantial jumps/scrubbing, keep bounded conservative tolerances, and avoid promising proof of attention or perfect seek detection.
- Migration makes old seemingly complete videos display unknown coverage -> preserve legacy values in Details/export with an explanation; never seed fabricated ranges.
- Interval fragmentation -> merge only real overlaps/adjacency, batch bounded visible-history queries, and test repeated/sparse playback for stable storage growth; do not store raw samples.
- Removing player widgets can break unrelated handlers and app-action access -> audit all widget lookups and mouse/keyboard workflows; cover empty queue and both panes collapsed.
- Recommending VLC controls exposes external seeks/rate changes more often -> real VLC verification is a release gate, not just mocked controller tests.

## Migration Plan

1. Add a transactional schema migration for coverage ranges and tracking initialization, supporting fresh databases and each existing schema version. Preserve every old field, queue identity, and outcome; seed no ranges.
2. Add model/database coverage APIs before switching controller evidence collection and coverage-based internal policy. Keep legacy projection/export helpers explicitly separate.
3. Replace row presentation and remove the player surface; update documentation and automated tests together.
4. Run unit/TUI tests, lint/type checks, and opt-in installed VLC tests, including native seek/Next and variable playback rate.
5. For implementation deployment, reinstall with the established installer if present, otherwise `pipx install --force .`, and verify the globally resolved `vlcq` command. Planning-only artifact changes require no application reinstall.
6. Migration failure rolls back transactionally and reports recovery guidance without replacing the original. Before deploying a schema upgrade, take a consistent SQLite backup. Older binaries must reject newer schema versions; rollback requires restoring the backup, not deleting coverage tables in place, and must disclose that post-backup progress would be lost.
