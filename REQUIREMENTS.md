# vlcq Requirements

## 1. Purpose

`vlcq` is a small macOS terminal application for managing a deterministic video
queue in VLC. It replaces VLC's awkward playlist workflow while recording
playback progress for every item, including items reached automatically.

The application owns the queue and launches/controls a dedicated VLC process.
It must not depend on VLC's incomplete `recentlyPlayedMedia` preferences.

## 2. Goals

- Provide a simple Textual TUI for adding, ordering, playing, skipping, and
  removing videos.
- Send VLC one queued item at a time rather than delegating queue semantics to
  VLC's playlist.
- Observe the active VLC item and persist progress across playlist transitions,
  application restarts, and VLC restarts.
- Support natural ordering of episode-oriented filenames.
- Provide a stable, read-only progress export for consumers such as
  `plex-truenas-sync`.
- Keep media paths, HTTP credentials, and playback history local and private.
- Fail safely: loss of observation or database access must never delete media.

## 3. Non-goals for the initial release

- Replacing VLC's video window or embedding LibVLC.
- Streaming, transcoding, downloading, or managing a media library.
- Remote/LAN control.
- Synchronizing history between computers.
- Editing, moving, copying, or deleting media files.
- Supporting Windows or Linux in the first release.
- Requiring a particular macOS Finder extension architecture in the initial
  release.
- Inferring episodes by title through fuzzy matching.

## 4. Target environment and stack

- macOS.
- VLC 3 initially; VLC 4 support requires separate compatibility testing.
- Python 3.12 or newer.
- [Textual](https://textual.textualize.io/) for the TUI.
- SQLite through Python's standard-library `sqlite3` module.
- An asynchronous HTTP client for VLC's HTTP interface.
- Packaging as a `vlcq` console command using `pyproject.toml`.

The implementation should keep third-party dependencies small. The expected
initial runtime dependencies are Textual and an async HTTP client such as
`httpx`.

## 5. User workflows

### 5.1 Open a library root and select videos

```sh
vlcq
vlcq play ./Odd\ Taxi/
```

Running `vlcq` without a subcommand reopens the last valid library root, or
opens a native folder chooser when no root has been saved. The primary workflow
opens one canonical library/root folder and then opens the TUI. Opening the folder alone must not enqueue or start every video discovered
under it, recursively or otherwise. The TUI presents the root and its
subfolders for browsing; users explicitly select one or more supported videos
to add to the queue or play.

Selected videos are deterministically naturally ordered, so episode 9 precedes
episode 10, and canonical-path deduplication includes a file only once.
Unsupported, missing, and out-of-root entries are clearly reported and cannot
be played or added.

### 5.2 Add items to an existing or new queue

```sh
vlcq add ./next-season/
vlcq add movie.mkv
```

A directory operand opens or changes the library root; it never recursively
auto-enqueues its contents. Explicit local video operands represent a user
selection and enter the same validated add/play workflow as TUI selections.
They must be validated against a canonical root before they can be added or
played; a file operand cannot bypass root confinement. When no controller or
root exists, the canonical explicit video operands establish their nearest
common parent directory as the root; a single file uses its parent directory.
When a root already exists, explicit operands outside it are rejected with a
clear error. If an active `vlcq` controller exists, the command forwards the
root change or valid explicit additions to it. If none exists, it creates a
queue and opens the TUI. Inter-process communication must be local to the
current user and authenticated or protected by filesystem permissions.

### 5.3 Optional macOS context-menu entry points

An optional macOS right-click/context-menu action may pass either a folder or
selected local video files to `vlcq`. A folder opens or changes the library root
without recursively enqueueing its contents; selected files are explicit
candidates. Context-menu file selections use the same nearest-common-parent
root-establishment rule when no root exists and the same out-of-root rejection
when one does. Both forms use the same canonicalization, root-confinement,
supported-extension, missing-file, and non-destructive validation as the CLI
and TUI. The initial release does not require a particular Finder extension
architecture.

### 5.4 Resume

```sh
vlcq resume
```

This restores the most recent unfinished queue and offers to resume the current
item at its last persisted position.

### 5.5 Export progress

```sh
vlcq progress --root /Users/final/plex-tv --json
```

The command returns records strictly beneath the canonical root, keyed by
root-relative path. This is the supported integration boundary for
`plex-truenas-sync`; consumers must not depend directly on the SQLite schema.
No unrelated paths may be included.

## 6. TUI requirements

The initial UI should be deliberately simple and usable in an ordinary terminal.

### 6.1 Main screen

Display:

- VLC/controller connection state.
- The active library/root folder and a browser for that root and its subfolders.
- Supported videos with explicit-selection state. Hidden dotfiles and
  unsupported file types are filtered out of the browser; invalid explicit
  operands still receive clear errors.
- Distinct colors for folders, videos, selected items, playback states, and the
  currently focused browser or queue pane. Empty browser and queue panes explain
  the next useful action, and each queue row includes a textual state indicator
  for queued, playing, paused, stopped, skipped, completed, failed, or missing.
- Current filename (not the full absolute path by default).
- Playing, paused, stopped, or unavailable state.
- Elapsed time, duration, and percentage when available.
- A progress bar.
- The ordered queue with current-item highlighting.
- Per-item state: queued, playing, paused, skipped, completed, failed, or
  missing.
- A concise footer containing keybindings and transient status/error messages.

A second details view may show the canonical path and stored history when the
user explicitly requests it.

### 6.2 Required controls

| Key | Action |
| --- | --- |
| `o` | Open or change the library/root folder |
| Up/Down | Browse entries in the root or an opened subfolder |
| Right | Open the highlighted subfolder; outside the browser, seek forward |
| Left / `Backspace` | Return to the parent folder while browsing; at the library root, remain at the root. Left seeks backward outside the browser |
| `Enter` | Open a highlighted subfolder, or play a highlighted video now |
| `v` | Toggle the highlighted supported video in the selection |
| `a` | Add the highlighted playable video, or explicit `v` selection(s), to the queue |
| `A` | Add and play the highlighted playable video, or the first explicit selection in natural order |
| `Space` | Play/pause |
| `d` / `Delete` | Remove highlighted item from the queue only |
| `J` / `K` | Move highlighted item down/up |
| `n` | Skip to next item |
| `p` | Return to previous item |
| Left / `[` / `]` | Seek backward, or explicitly backward/forward, by a configurable interval |
| `r` | Resume/retry highlighted item |
| `c` | Clear completed queue entries after confirmation |
| `?` | Show help |
| `q` | Quit with an explicit choice about the VLC process |

Destructive-looking actions affect queue state only. They must never delete the
underlying media file. Each pane also exposes visible buttons for its primary
actions: open/up/select/add-and-play/sort for the browser and add/play/sort/clear
for the queue. Clearing the full queue requires confirmation.

### 6.3 Responsiveness

- TUI input must remain responsive while HTTP polling, VLC startup, root and
  subfolder browsing, filesystem validation, and database writes occur.
- Root browsing, path validation, and other slow operations run asynchronously.
- Resize events must preserve usable browser and queue viewports.
- Recoverable errors appear in the UI without closing the application.

## 7. Queue semantics

- `vlcq`, not VLC, is authoritative for queue order.
- VLC receives one active item at a time.
- Opening or changing a library/root folder never implicitly enqueues files;
  only explicit video selections enter the queue.
- A multiple-video selection is naturally ordered and canonical-path deduplicated
  before it is added or played.
- When natural completion is observed, persist final progress and start the
  next queued item.
- Manual `next` marks the current queue entry as skipped, not completed, unless
  completion was independently established.
- Manual selection of another queued item changes the current queue position.
- Reordering must be persisted immediately.
- Missing files remain visible as `missing` and are skipped with a visible
  warning.
- A queue survives controller crashes and can be resumed.
- Only one controller may mutate a queue database at a time. A second invocation
  forwards commands or exits with a clear message.

## 8. VLC process and HTTP interface

### 8.1 Process ownership

- Launch the VLC executable directly rather than relying on Finder/open-file
  behavior.
- Use a dedicated VLC instance so arguments are not silently forwarded to an
  unrelated existing VLC.app process.
- Validate supported VLC 3 command-line flags at startup and report an
  actionable error when the installed VLC is incompatible.
- Record the owned VLC PID and verify process identity before sending signals.
- Never terminate an unrelated VLC process.

### 8.2 HTTP configuration

The owned VLC instance should enable its HTTP interface with settings equivalent
to:

```text
--extraintf=http
--http-host=127.0.0.1
--http-port=<selected-local-port>
--http-password=<generated-secret>
```

Exact flags must be verified against the supported VLC release during
implementation.

Requirements:

- Bind exclusively to loopback.
- Generate a high-entropy per-session password.
- Never print or log the password, Authorization header, raw status response, or
  full media history.
- Store session connection data in a mode-0700 directory and mode-0600 file.
- Probe readiness with a bounded startup timeout.
- Treat HTTP as a control-capable interface, not a read-only status service.
- Reject redirects and unexpected response content/types.

### 8.3 Observation

Poll VLC's status endpoint while the controller runs:

```text
/requests/status.json
```

Observe only required fields, including current media URI, state, elapsed time,
duration, and position. The parser must tolerate missing or changed fields and
fail closed for malformed or non-local media URIs.

Suggested intervals:

- Playing: once per second.
- Paused: every 2–5 seconds.
- Stopped/unavailable: exponential backoff with a small upper bound.
- Flush immediately on pause, stop, item change, skip, shutdown, and observed
  completion.

## 9. Playback progress semantics

- Persist raw position and duration separately; derive display percentages.
- Progress is cumulative for the same unchanged file and must never decrease
  because of a stale observation.
- A zero position must not erase historical positive progress.
- Seeking near the end does not by itself prove completion.
- Manual stop does not imply completion.
- Prefer an explicit natural-ended state when VLC exposes one reliably.
- Otherwise infer natural completion only when all conservative conditions hold,
  such as continuous recent playback near the known duration followed by the
  expected stopped transition.
- Store both maximum progress and a separate `completion_observed` flag.
- Completion thresholds must be configurable and documented; the default should
  be conservative.
- If completion cannot be established, retain the observed percentage without
  marking the item complete.

## 10. Media identity and path safety

Root opening and video selection are local, root-confined, asynchronous, and
non-destructive. For every opened root and selected, observed, or queued item:

1. Accept only a local folder for the root, and only local `file:` URIs or
   explicit local TUI/CLI/context-menu paths for videos. When explicit CLI or
   context-menu file operands arrive without an existing root, establish the
   canonical nearest common parent as the root; otherwise enforce the existing
   root and reject out-of-root operands.
2. Reject remote hosts, credentials, query strings, fragments, malformed
   escapes, NULs, and non-file schemes.
3. Resolve canonical paths. Require the root to be a regular directory and
   every selected or queued video to be a regular file beneath that root.
   Containment must be checked on canonical paths, including symlink traversal,
   rather than by string-prefix matching.
4. Do not follow a queue/cache/database symlink into an unsafe location.
5. Report unsupported extensions, missing paths, invalid roots, and
   out-of-root paths clearly; never add or play them.
6. Do not modify, delete, move, or copy anything while browsing or selecting.
7. Store a file fingerprint containing device, inode, size, and modification
   time.
8. Treat a changed fingerprint as a replacement rather than applying old
   progress silently.

Renames on the same filesystem may be recognized by device/inode while the
controller can still observe the file. Content-based hashing and fuzzy title
matching are out of scope initially.

## 11. Persistence

Default location:

```text
~/Library/Application Support/vlcq/vlcq.sqlite3
```

Requirements:

- Application directory mode 0700 and database/state files mode 0600.
- SQLite WAL mode and transactions for queue and progress updates.
- Versioned schema with explicit migrations.
- Serialized writes and bounded busy timeout.
- No database contents in ordinary logs or error messages.
- Corruption or migration failure must preserve the original database and fail
  with recovery instructions rather than silently replacing it.

Minimum logical entities:

### Media

- Stable internal ID.
- Canonical path.
- Device/inode/size/mtime fingerprint.
- Maximum observed position and duration.
- Completion-observed flag.
- First/last observed timestamps.

### Queue

- Queue ID and timestamps.
- Ordered entries.
- Media ID.
- State and last error category.
- Current-entry marker.

### Playback observations

A full high-frequency event log is not required. If observations are retained,
they should be compacted and bounded.

## 12. Integration contract

`vlcq progress --root <path> --json` should emit a versioned document such as:

```json
{
  "version": 1,
  "root": "<requested canonical root or an opaque root identifier>",
  "records": {
    "Show/episode.mkv": {
      "positionMs": 1349000,
      "durationMs": 1440000,
      "watchedPercent": 94,
      "completionObserved": false,
      "observedAt": "2026-09-03T22:00:00Z"
    }
  }
}
```

The implementation must not emit records outside the requested root. A future
`plex-truenas-sync` adapter can merge these records with Plex and its existing
VLC cache using maximum valid progress.

## 13. Logging and privacy

- Default logs contain operation categories and counts, not absolute media
  paths, credentials, raw HTTP payloads, or playback history.
- An explicit verbose/debug mode may show the current media path but must still
  redact credentials and HTTP headers.
- Rotating logs, if introduced, must be bounded and private.
- No telemetry or network access other than loopback VLC HTTP requests.

## 14. Failure handling

- VLC unavailable: keep the queue and offer retry/relaunch.
- HTTP authentication failure: stop polling and show configuration guidance;
  never repeatedly brute-force credentials.
- VLC crash: flush the latest observation and offer restart at the saved
  position.
- Controller crash: database transactions preserve a resumable queue.
- Media missing/replaced: mark the entry and skip it; do not delete or mutate
  the file.
- Database unavailable: do not begin unmanaged autoplay; show a clear error.
- Unsupported HTTP response: pause automatic advancement rather than guessing.

## 15. Initial acceptance criteria

1. `vlcq play <directory>` opens that folder as the library/root in the TUI
   without recursively auto-enqueueing or starting every discovered video.
2. The TUI browses the root and subfolders and supports explicit individual and
   multiple-video selection, natural ordering, and canonical-path
   deduplication for add/play actions.
3. Open/change-root, parent-navigation, and selection keybindings are visible
   and usable; unsupported, missing, and out-of-root paths receive clear errors
   without filesystem mutation.
4. An optional macOS right-click/context-menu entry, if provided, passes a
   folder or selected local videos through the same validated workflow; no
   particular Finder extension architecture is required for the initial
   release.
5. The application launches an owned VLC instance with HTTP bound only to
   loopback and authenticated.
6. Playing one item to the next automatically records both items, including the
   autoplay transition that VLC preferences currently omit.
7. Pause, resume, seek, skip, and play-now controls work from the TUI.
8. Queue order and progress survive restarting both `vlcq` and VLC.
9. A later zero or stale observation cannot reduce stored progress.
10. Replaced files do not inherit previous progress.
11. Concurrent `vlcq` invocations cannot corrupt or independently mutate
    the same queue.
12. `vlcq progress --root ... --json` returns only validated relative records
    beneath that root.
13. HTTP credentials and unrelated media paths do not appear in normal output,
    logs, exceptions, or tests.
14. Unit tests cover folder opening without auto-enqueue, root/subfolder
    browsing and parent navigation, explicit selection, standalone-file root
    establishment, natural ordering, canonical deduplication,
    unsupported/missing/out-of-root handling, queue transitions, progress
    merging, path/URI validation, database migrations, malformed HTTP
    responses, and replacement detection.
15. A macOS integration test verifies VLC 3 startup, loopback binding, status
    polling, one autoplay transition, and clean shutdown. If an optional
    context-menu entry is implemented, its folder and selected-file handoff is
    also tested.

## 16. Deferred decisions

Resolve during design/implementation:

- Exact VLC 3 flags required to guarantee an isolated macOS instance.
- Whether quitting the TUI should stop VLC, leave it running, or prompt by
  default.
- Default completion threshold and natural-end inference tolerance.
- Whether an optional context-menu video selection should preselect videos in
  the TUI or add them directly when a controller already exists.
- Whether a later release should support a detached background controller.
- VLC 4 compatibility strategy.
