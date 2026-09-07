## Context

See proposal.md for motivation and specs/vlcq/spec.md for the observable contract. This design is required because the change crosses persistence, queue semantics, asynchronous playback, and UI interaction.

Observed implementation:
- Database schema version 1 stores monotonic `position_ms`, `duration_ms`, completion, and first/last observation timestamps. Creating a media row initializes timestamps even without playback. `progress_for()` calls `ensure_media()`, so it is not suitable for read-only browsing.
- Queue rows expose identity, path, order, and state but not history. Library rows contain a filename and selection marker. Routine queue updates already reuse rows to avoid flicker.
- `PlaybackController.play_index()` sends `in_play` without seeking to saved progress. CLI `resume` restores the saved root but does not implement the resume-position offer required by the existing spec. This change closes that gap rather than treating the spec as proof of existing behavior.
- Queue removal and clear mutate the database without coordinating with VLC. Polling and UI commands can interleave. `start()` can create another polling task; process lifecycle needs an idempotent recovery boundary.
- Textual toolbars exist, but dialogs are label-and-keybinding screens, Add lives in the queue pane while targeting the browser, and the app has no explicit row activation mouse contract.
- Verification found that `resume` targets queue index zero rather than the persisted current entry, queue-row clicks can activate playback, and an eager mount-time queue refresh can reuse rows before their children mount. Rendering also synchronously validates each file and reads history per path, so it can block input despite the intended projection design.

## Goals / Non-Goals

**Goals:** Separate durable history, resume intent, queue state, and live playback; unify keyboard and mouse actions; make transitions testable against stale responses; preserve existing export compatibility and private storage.

**Non-Goals:** No general media catalog, codec probing, network metadata, event-sourced watch coverage, cross-session undo, or wholesale replacement of the existing TUI framework. Unrelated gaps between the broad baseline specification and implementation are outside this change.

## Decisions

### 1. Add resume metadata without redefining the export

Migrate to schema version 2 with nullable `resume_position_ms` and `last_played_at`. Keep `position_ms` as historical maximum and keep version-1 export keys and meanings unchanged. Existing fields remain available for compatibility; new UI reads use the new timestamp rather than inferring playback from row creation.

Migration leaves the new fields null for legacy rows. For incomplete legacy media with positive maximum progress, offer a clearly labeled "Resume from furthest recorded progress" fallback until a trustworthy new position is recorded. Last played remains unknown for legacy rows. This avoids inventing a last position or timestamp. Completed items explicitly replay from zero.

A trustworthy observation for the matching active media updates resume and playback time; maximum position only increases. A deliberate rewind or start-over authorizes a lower resume value after the new position is confirmed. A generic stopped response reporting zero does not erase the prior resume point. Completion history remains monotonic and separate.

Alternative rejected: reinterpret `position_ms` as last position. That would break the public progress contract. Tracking actual watched intervals is unnecessary for the selected outcome.

### 2. Use bulk, non-mutating history projections

Add a typed history projection keyed by canonical path and fingerprint, and a batch SELECT path that never calls `ensure_media()`. Validate browser file identities asynchronously, then perform SQLite access on its owning thread; do not move the existing connection into arbitrary worker threads. Fetch only relevant displayed-folder and active-queue history in bounded batches. Cache projections for the current view and invalidate on actual progress changes or file identity changes. Queue and browser refreshes must defer row reuse until mounted children exist; unchanged projections update existing labels without replacing row widgets.

History categories are derived: Completed takes precedence, positive maximum without completion is In progress, otherwise No recorded progress. Missing data is not zero-duration evidence. A details region shows last position, maximum reached, and last played; the active player always uses live status for the matching media.

Search is case-insensitive filename substring matching within the current folder. History filters apply to videos while directories remain navigable. Selection is stored by canonical identity independently of the filtered listing. A summary discloses selections elsewhere/hidden and supports clearing them. Revalidate all selected paths before actions.

Alternative rejected: call the existing per-file history helper while rendering. It creates misleading history and incurs repeated filesystem/database work.

### 3. One transition boundary owns VLC and queue changes

Centralize UI and CLI-originated playback operations through the controller. Use an asynchronous operation lock plus a playback generation token. Poll requests capture generation and expected media identity; discard their responses if either has changed. Queue mutations that affect active playback share this boundary; no HTTP wait occurs inside an open SQLite transaction.

Before transitions, attempt bounded final status capture, retaining the last trustworthy cached observation on failure. Starting an item validates root/fingerprint, issues playback, waits with a bounded timeout for the expected media identity, then performs validated absolute seek for Resume. Do not seek on a response still naming the previous media. Relative/absolute seek invalidates prior near-end evidence; only fresh post-seek observations can establish natural completion. Seek failure remains visible and does not claim a successful resume.

Explicit Play now/Enter on an inactive incomplete item opens Resume / Start over / Cancel before any insertion or reorder. Direct Resume and Start over controls in selected-item details encode that choice without requiring a modal. `vlcq resume` resolves the persisted current queue identity when it is unfinished, then falls back to the first unfinished entry; it never treats index zero as the saved target. Activating the current item does not reload it; paused current media resumes. Natural advancement and explicit Next/Previous use saved resume for incomplete items without modal prompts; completed items replay from zero. Batch add-and-play resolves the first target's decision before committing the batch, so Cancel has no side effects.

Alternative rejected: add seek only in the TUI. Automatic advancement and CLI resume would then disagree, and stale polls would still corrupt resume intent.

### 4. Queue commands operate on identity, not a stale row index

Resolve highlighted identity at action time. Add to end deduplicates existing paths without moving existing entries. Play next validates the full batch first, excludes the active entry, removes selected existing entries from their old positions, and inserts the naturally ordered block after the active item (at the front if idle). Unselected order is stable. Persist the operation in an explicit transaction; the connection currently uses autocommit, so a context manager alone must not be assumed to make multiple statements atomic.

Play now ensures membership without clearing the queue. Playback choice and queue insertion share the controller transition boundary, with operational failures exposed rather than presented as success.

### 5. Stop-before-remove, with bounded session undo

The proposed default for active-item removal is stop playback, retain history, remove the entry, and remain idle. Clear follows the same rule if its set contains the active entry. Obtain a matching stop confirmation (or verified owned-process exit) before deletion. A disconnected/unverifiable VLC session blocks active removal with recovery guidance; it does not silently leave an unobserved player running. If storage fails after a confirmed stop, retain entries, reconcile them as stopped, and report the failure.

Record one in-memory snapshot of the latest successful removal/clear: queue identity, ordered entry identities, media fingerprints, and prior non-live states. Undo restores atomically without reloading media; formerly playing/paused entries restore as stopped. Validate the entire snapshot first; reject out-of-root or replaced files, and restore absent files as missing. Missing-path validation still checks canonical ancestry so symlink escapes cannot be accepted as merely missing. Any subsequent queue mutation, including automatic advancement, invalidates undo; ordinary polling and pause/seek do not. Root change or application exit discards it. Playback changes during the stop operation are excluded by the transition boundary.

Alternative rejected: keep playing after removal. That requires a separate detached active-media model and more surprising queue semantics. Multi-level persistent undo adds migration and stale-identity complexity without being needed for accidental removal recovery.

### 6. Separate library, queue, selected details, and active player

Library controls target the explicit selection or highlighted playable video; no library-selection action is rendered in the queue pane. Queue controls target the highlighted queue identity. The player bar targets active playback regardless of pane focus. Keep existing keyboard commands, add discoverable keyboard access for new commands, and suspend app-level character shortcuts while editing input fields. A click on any video or queue row only updates highlight and details; only a browser directory row opens a folder, and only an explicit Open/Play control activates playback. Checkbox clicks toggle selection without triggering playback. Explicit Open/Play controls avoid reliance on double-click timing or terminal-specific right-click behavior.

All dialogs get buttons and cancel paths, including folder entry, resume, clearing, help, and quit. An in-app folder chooser browses allowed directory choices by mouse and allows typed paths as an alternative. Reconnect is always reachable when disconnected, independent of highlighted entries; it retires stale clients/tasks and reconciles owned-process identity before starting another process. Recovery does not autoplay.

Wide layout:

```text
+---------------------------+---------------------------+
| Library: search / filter  | Queue                     |
| Files + history + queued  | Entries + history + state |
| Selection summary         |                           |
| Add to end / Next / Play  | Up / Down / Remove / Undo |
+---------------------------+---------------------------+
| Selected item: resume / furthest reached / last played |
| Resume / Start over / Details                         |
+-------------------------------------------------------+
| Active media: elapsed / duration / remaining           |
| Previous / -10s / Pause / +10s / Next / Reconnect      |
+-------------------------------------------------------+
```

At 80x24, use compact pane controls and a clickable overflow action menu/details modal instead of wide fixed-width toolbars. All essential actions, including direct Resume and Start over, remain mouse-reachable. Preserve existing row reuse and restore highlight by identity after structural changes. Preserve horizontal/vertical scrolling for long names. Progress-track clicks map clamped coordinates to duration only with trustworthy active status; dragging is deferred.

Queue mutations and root changes return whether they invalidated an available undo snapshot. The TUI immediately displays that expiration; a later Undo click remains a secondary confirmation, not the first feedback.

Alternative rejected: put all controls in one global toolbar. Target ambiguity would remain and narrow terminals would worsen it.

## Risks / Trade-offs

- Legacy history lacks a true last position/time -> retain unknown timestamps and label the maximum-position fallback explicitly.
- VLC replies may identify old media or report transient zero -> generation checks, expected-media readiness, bounded timeouts, and regression tests with delayed replies.
- Stop-before-remove cannot make HTTP and SQLite one atomic transaction -> stop first, commit second, retain visible stopped entries on storage failure.
- Richer rows can increase render/database cost or race widget mounting -> bounded bulk read-only projections, asynchronous filesystem checks, cached view data, and mount-safe stable row updates.
- Mouse interactions vary across terminals -> Textual Pilot click tests plus manual macOS terminal verification at 80x24 and a wide size; no drag/right-click dependency.
- Existing natural-end inference is simple -> preserve conservative history semantics and test seeks around completion; do not expand into watch-coverage analytics.
- Single-level undo can expire during autoplay -> clearly communicate expiration; never silently restore against a changed queue.

## Migration Plan

1. Implement and test explicit transactional v1-to-v2 migration, preserving all old columns, queues, fingerprints, maxima, completion flags, and export values. Failure rolls back and preserves the database with actionable guidance.
2. Add read models and controller transitions before wiring the UI; verify fake-client ordering, malformed/stale response handling, persisted-current resume resolution, and mount-safe refresh behavior.
3. Add mouse/keyboard UI behavior and CLI resume offer. Verify browser-only directory activation, non-playing queue/video row clicks, direct Resume/Start over controls, pane-local targeting, asynchronous history refresh, and immediate undo-expiration notices. Update README with progress-label meaning, resume choices, active removal behavior, and undo limitations.
4. Run normal pytest, ruff, and `mypy src`; run an opt-in real VLC test using temporary generated media for persisted resume seek, rewind, pause, natural advancement, stop/removal, reconnect, and clean shutdown. Record environmental skips and manual 80x24 and wide-terminal mouse results. Keep credentials and user history out of evidence.
5. Before release testing on user data, create a consistent private SQLite backup using its backup API. Downgrading to a schema-v1 binary requires restoring that backup; do not decrement user_version or discard v2 fields in place. Document that restoring a backup loses observations recorded after the backup.
6. Reinstall the implemented CLI globally using the project workflow and verify its resolved command. This step belongs to implementation, not this planning-only change.
