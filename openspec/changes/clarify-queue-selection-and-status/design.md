## Context

See `proposal.md` for motivation and `specs/vlcq/spec.md` for behavior. The database currently stores a global `current_entry` setting and a state on every queue row. Switching playback resets the old state only when it is playing or paused, so a stopped prior item remains stale. The Textual `ListView.index` is preserved opportunistically by path during refresh but has no durable identity and is separate from the diamond that marks current playback.

The existing `settings` table can hold another identity without exposing a new public API or requiring destructive schema work. Queue entry IDs are preferable to paths or positions because reorder changes positions and path reuse can refer to replacement media.

## Goals / Non-Goals

**Goals:**
- Make the queue highlight immediately durable by entry identity while keeping it independent from playback.
- Make transient playback state singular and consistent with the current pointer.
- Repair existing stale states safely and atomically.
- Reduce row-label noise without hiding terminal outcomes or playback history.

**Non-Goals:**
- Persist browser multi-selection or browser highlight.
- Turn queue highlight into playback activation.
- Redefine completion, progress, resume, automatic advancement, or queue ordering.
- Rewrite historical states as an event log.

## Decisions

### Store selected queue identity in `settings`

Add a `selected_entry` setting alongside `current_entry`, with database accessors that verify the ID belongs to the active queue. Textual highlight events write this identity immediately. Queue rendering restores `ListView.index` by ID, not path or prior index. A root change clears selection, and a missing selected entry clears the stale setting rather than allowing a reused position to target another item.

Alternative: persist list position or path. Rejected because reorder invalidates positions and a path can outlive a replaced media identity. A schema column on `queues` was also considered, but the existing key/value settings mechanism avoids an unnecessary schema migration.

### Centralize current transitions in one transaction

Introduce a database transition operation that validates the target in the active queue, changes all non-terminal transient rows other than the target to `queued`, updates `current_entry`, and assigns the target state atomically. Clearing current similarly normalizes stale transient rows. Queue and controller paths that currently call `set_current` and `set_state` separately will use this boundary.

Terminal and diagnostic states (`completed`, `skipped`, `failed`, `missing`) are preserved unless an existing explicit workflow already changes the target, such as retrying it. This avoids converting useful outcomes merely because another item starts.

Alternative: expand the condition in `QueueService.play_now` from playing/paused to include stopped. That fixes the demonstrated path but leaves legacy corruption, multiple stale rows, and non-atomic pointer/state updates possible.

### Normalize compatibility state when an active queue is opened

Run a bounded transaction after resolving the active queue. If current points outside that queue, clear it and normalize all transient states to queued. If current is valid, normalize transient states on every other entry while preserving the current row's state. Validate selected identity independently and clear it if stale.

No queue entries, media rows, progress, order, or fingerprints are removed or rewritten. Because this uses existing tables and values, no schema-version increment is required.

Alternative: repair only during rendering. Rejected because the database would remain contradictory and non-TUI consumers would continue seeing multiple stopped states.

### Render pending as absence of an outcome badge

Keep `queued` as the internal pending state for compatibility and transition logic, but omit `○ QUEUED` from queue labels. Show playing, paused, or stopped only on the current row; retain skipped, completed, missing, and failed indicators. Keep the current diamond and Textual selection highlight distinct. Playback history remains a separate suffix.

Alternative: replace states with timeline labels such as past/current/future. Rejected because skipped/completed/failed/missing remain useful outcomes and queue reorder means position is not immutable history.

## Risks / Trade-offs

- [Frequent keyboard highlight events add SQLite writes] -> Write only when the selected entry ID changes; each update is a single local settings upsert.
- [Programmatic index restoration could feed back into persistence] -> Suppress or make idempotent writes while restoring and compare identities before updating.
- [Normalization could erase a useful terminal diagnosis] -> Normalize only transient playing/paused/stopped states on non-current rows; never rewrite completed/skipped/failed/missing.
- [A crash between VLC switching and state commit could leave imperfect observation state] -> Use one local database transaction for pointer/state changes and retain existing controller generation/path checks.
- [Older binaries ignore `selected_entry`] -> The additional settings key is backward-compatible; rollback does not require schema downgrade.

## Migration Plan

1. Back up important private SQLite state using the documented SQLite backup procedure.
2. Deploy database accessors, atomic transition/normalization behavior, and TUI identity persistence together.
3. On first active-queue open, normalize stale transient states and invalid pointer settings transactionally.
4. Verify existing queue order, terminal outcomes, media fingerprints, and progress are unchanged.
5. Rollback by reinstalling the prior binary; its code ignores `selected_entry`. Restore the backup only if exact pre-normalization transient labels are required, since those labels are not playback history.
