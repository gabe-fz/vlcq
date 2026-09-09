## Context

See `proposal.md` for motivation. Today `VLCClient.play()` sends `in_play`, VLC retains its own playlist state, and `PlaybackController._observe()` ignores any path that differs from the persisted current entry. Natural completion advances only after a near-end playing observation followed by stopped. This is safe, but VLC's native Next control has neither an authoritative successor nor a direct HTTP event that identifies the user's intent.

The integration must account for VLC transitioning directly from one playing path to another without exposing an intermediate stopped state. It must also preserve monotonic progress, conservative completion evidence, queue identity, and the active item's playback position while queue order changes.

## Goals / Non-Goals

**Goals:**

- Give native VLC Next exactly one `vlcq`-authorized successor.
- Reconcile native and natural VLC transitions through the same serialized queue boundary.
- Keep the active media uninterrupted when the staged successor changes.
- Preserve fail-closed path handling and trustworthy history semantics.

**Non-Goals:**

- Treating VLC's playlist as durable or authoritative.
- Detecting the native button event itself; reconciliation is based on observed media identity.
- Mirroring the full queue or supporting arbitrary playlist navigation.
- Supporting native Previous, externally opened media, VLC 4, or unrelated VLC instances.

## Decisions

### 1. Maintain a two-item VLC playlist window

The controller will track the VLC playlist identities for the active queue entry and at most its next eligible successor. Initial playback will replace VLC's playlist with the chosen active item, start it, then enqueue the successor. After an observed transition, the former item will be removed and the new successor enqueued.

A two-item window is the smallest representation that makes native Next meaningful while limiting drift from the authoritative queue. Mirroring the full queue was rejected because reorder/removal, missing files, resume policy, and stale VLC state would create a second queue authority. Inferring Next from a same-path jump to zero was rejected because it is indistinguishable from restart or seek-to-start.

### 2. Extend the HTTP client with typed playlist operations

`VLCClient` will expose only the additional VLC 3 operations required to replace the bounded window, enqueue a validated local URI, remove a known playlist id, and inspect playlist entries. Playlist parsing will return stable ids paired with validated local paths and reject unsafe identities. The controller, rather than UI code, owns orchestration.

Replacing the window is allowed when starting a different active item. While an item is already playing, successor refresh will delete only the previously staged successor and enqueue the replacement; it will not clear the playlist or issue `in_play`. The controller caches a window signature by queue-entry identity and invalidates it after reconnect or VLC errors.

### 3. Reconcile a staged-successor observation atomically

Polling will first compare the reported path or stable playlist id with the active and staged identities. An active match follows existing progress handling. A staged-successor match enters the transition lock, verifies the generation and current/successor identities again, flushes the last trustworthy old-item observation, and performs one serialized database transition without issuing another play command.

The old item is completed only when the existing continuous near-end evidence is present; otherwise it is skipped. The successor becomes current with VLC's observed playing, paused, or stopped state. The controller then updates its generation/status and refreshes the bounded window. This handles both native Next and VLC's direct natural transition without replaying the successor.

An unexpected local path, malformed/remote path, missing staged file, stale generation, or playlist synchronization error is not adopted. Automatic advancement pauses and the TUI receives recoverable guidance through the existing error path. No history is written for the unexpected media.

### 4. Make successor synchronization part of the controller boundary

Explicit play, `vlcq` next/previous, automatic advancement, reconnect recovery, and queue mutations that can change the immediate successor will call an idempotent synchronization method. UI and CLI actions remain responsible for their existing mutations but notify the controller afterward where needed. Polling also compares the desired successor signature, providing bounded eventual repair if a mutation path misses an immediate notification.

Missing queue entries are marked through existing queue policy and bypassed when selecting the eligible staged successor. Synchronization never adds a file to the authoritative queue and validates each path against the active root before sending its URI to VLC.

### 5. Override persisted VLC playback modes at launch

The owned process will pass explicit `--no-repeat`, `--no-loop`, and `--no-random` flags. This makes startup deterministic even when VLC preferences retain a prior mode. A user can still alter controls while VLC runs; if that prevents transition to the staged successor, `vlcq` will not guess that Next occurred.

## Risks / Trade-offs

- [VLC may transition faster than the polling interval and omit the old stopped state] -> Reconcile direct active-to-staged path changes and use only previously captured near-end evidence for completion.
- [A second native Next can occur before another successor is staged] -> Keep the window deliberately bounded; the second action stops at the window boundary rather than navigating untracked media, and polling repairs the window promptly.
- [Playlist ids and response structure vary across VLC 3 builds] -> Parse nested playlist JSON defensively, key identity by both stable id and canonical local path, and cover supported shapes with fake and real-VLC tests.
- [Deleting or replacing the staged item could disturb active playback] -> Never clear or replay merely to update a successor; delete only the verified non-current playlist id and confirm the active identity afterward.
- [Queue mutation and a native transition can race] -> Serialize reconciliation and synchronization with the existing transition lock, generation checks, and identity revalidation before database writes.
- [Users can re-enable repeat/random after launch] -> Fail closed when VLC does not transition to the staged successor; do not infer intent from a restart or arbitrary path.

## Migration Plan

No database migration is required. Introduce client parsing/commands and fake tests first, then controller window synchronization and reconciliation, then route mutation notifications and documentation. Run the full unit, lint, and type-check suites and the opt-in real VLC test on VLC 3.

Rollback removes bounded playlist synchronization and restores one-active-item behavior. Queue and history data remain schema-compatible; stopping the dedicated VLC instance before rollback discards only its ephemeral playlist window.
