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

### 5.1 Start a queue

```sh
vlcq play ./Odd\ Taxi/
vlcq play episode-01.mkv episode-02.mkv
```

- Directories are scanned recursively for supported video extensions.
- Discovered files are naturally sorted, so episode 9 precedes episode 10.
- Duplicate canonical files are included only once.
- The TUI opens with the resulting queue and begins playback unless disabled by
  an explicit option.

### 5.2 Add items to an existing or new queue

```sh
vlcq add ./next-season/
vlcq add movie.mkv
```

If an active `vlcq` controller exists, the command forwards additions to it. If
none exists, it creates a queue and opens the TUI. Inter-process communication
must be local to the current user and authenticated or protected by filesystem
permissions.

### 5.3 Resume

```sh
vlcq resume
```

This restores the most recent unfinished queue and offers to resume the current
item at its last persisted position.

### 5.4 Export progress

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
| `Space` | Play/pause |
| `Enter` | Play highlighted item now |
| `a` | Open an add-path prompt |
| `d` / `Delete` | Remove highlighted item from the queue only |
| `J` / `K` | Move highlighted item down/up |
| `n` | Skip to next item |
| `p` | Return to previous item |
| Left/Right | Seek backward/forward by a configurable interval |
| `r` | Resume/retry highlighted item |
| `c` | Clear completed queue entries after confirmation |
| `?` | Show help |
| `q` | Quit with an explicit choice about the VLC process |

Destructive-looking actions affect queue state only. They must never delete the
underlying media file.

### 6.3 Responsiveness

- TUI input must remain responsive while HTTP polling, VLC startup, filesystem
  scanning, and database writes occur.
- Slow operations run asynchronously.
- Resize events must preserve a usable queue viewport.
- Recoverable errors appear in the UI without closing the application.

## 7. Queue semantics

- `vlcq`, not VLC, is authoritative for queue order.
- VLC receives one active item at a time.
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

For every observed or queued item:

1. Accept only local `file:` URIs or explicit local CLI paths.
2. Reject remote hosts, credentials, query strings, fragments, malformed
   escapes, NULs, and non-file schemes.
3. Resolve the canonical path and require a regular file before playback.
4. Store a file fingerprint containing device, inode, size, and modification
   time.
5. Treat a changed fingerprint as a replacement rather than applying old
   progress silently.
6. Do not follow a queue/cache/database symlink into an unsafe location.

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

1. `vlcq play <directory>` naturally sorts videos and displays them in the TUI.
2. The application launches an owned VLC instance with HTTP bound only to
   loopback and authenticated.
3. Playing one item to the next automatically records both items, including the
   autoplay transition that VLC preferences currently omit.
4. Pause, resume, seek, skip, and play-now controls work from the TUI.
5. Queue order and progress survive restarting both `vlcq` and VLC.
6. A later zero or stale observation cannot reduce stored progress.
7. Replaced files do not inherit previous progress.
8. Concurrent `vlcq` invocations cannot corrupt or independently mutate the
   same queue.
9. `vlcq progress --root ... --json` returns only validated relative records
   beneath that root.
10. HTTP credentials and unrelated media paths do not appear in normal output,
    logs, exceptions, or tests.
11. Unit tests cover natural sorting, queue transitions, progress merging,
    path/URI validation, database migrations, malformed HTTP responses, and
    replacement detection.
12. A macOS integration test verifies VLC 3 startup, loopback binding, status
    polling, one autoplay transition, and clean shutdown.

## 16. Deferred decisions

Resolve during design/implementation:

- Exact VLC 3 flags required to guarantee an isolated macOS instance.
- Whether quitting the TUI should stop VLC, leave it running, or prompt by
  default.
- Default completion threshold and natural-end inference tolerance.
- Whether manually selected media outside the managed queue should be observed,
  ignored, or offered for import.
- Whether a later release should support a detached background controller.
- VLC 4 compatibility strategy.
