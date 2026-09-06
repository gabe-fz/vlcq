# vlcq Specification

## Purpose

Define the behavior of `vlcq`, a macOS terminal application that owns a deterministic VLC video queue, provides safe library browsing and playback controls, and persists trustworthy per-item progress.

## Requirements

### Requirement: Platform and scope
`vlcq` SHALL run on macOS with Python 3.12 or newer, provide a Textual TUI, use SQLite for persistence, and control a dedicated VLC 3 process through an asynchronous loopback HTTP client. VLC 4 support requires separate compatibility testing. The initial release SHALL NOT embed LibVLC, stream or transcode media, manage media files, provide LAN control, synchronize computers, infer episodes by fuzzy title matching, or support Windows or Linux.

#### Scenario: Run in the supported environment
- **WHEN** the user starts `vlcq` on a supported macOS installation with compatible VLC 3
- **THEN** the application provides its queue and progress workflows without requiring VLC preference history

### Requirement: Library root and explicit selection
Running `vlcq` without a subcommand SHALL reopen the last valid canonical library root or show a native folder chooser when none is saved. A directory operand SHALL open or change the root and SHALL NOT recursively enqueue or play its contents. The TUI SHALL browse the root and its subfolders, hide dotfiles and unsupported file types, and permit explicit single or multiple supported-video selection. Selected videos SHALL be naturally ordered and deduplicated by canonical path.

#### Scenario: Open a directory
- **WHEN** the user runs `vlcq play <directory>` or opens a directory in the TUI
- **THEN** that directory becomes the browsable library root and no discovered video is implicitly queued or started

#### Scenario: Select multiple videos
- **WHEN** the user selects multiple supported videos
- **THEN** `vlcq` processes each canonical file once in natural filename order so episode 9 precedes episode 10

### Requirement: CLI and context-menu operands
`vlcq add` and `vlcq play` SHALL treat explicit file operands as user selections subject to the same validation as TUI selections. Without an existing root, valid file operands SHALL establish their nearest common parent as the root; a single file SHALL use its parent. With an existing root, out-of-root operands SHALL be rejected clearly. An active controller SHALL receive valid root changes or additions through current-user-local IPC protected by authentication or filesystem permissions; otherwise the invocation SHALL create a queue and open the TUI. An optional macOS context-menu integration, if supplied, SHALL use exactly the same folder and file workflow and SHALL NOT require a particular Finder extension architecture.

#### Scenario: Establish a root from files
- **WHEN** explicit valid files are supplied and no root or controller exists
- **THEN** their canonical nearest common parent becomes the root and the validated files enter the requested add or play workflow

#### Scenario: Forward to an active controller
- **WHEN** another invocation supplies a valid root change or in-root files while a controller is active
- **THEN** it forwards the request over protected local IPC instead of independently mutating the queue

### Requirement: Resume and progress export
`vlcq resume` SHALL restore the most recent unfinished queue and offer to resume its current item at the persisted position. `vlcq progress --root <path> --json` SHALL return a versioned document keyed by validated root-relative paths, including position, duration, watched percentage, completion status, and observation time. It MUST NOT include records outside the canonical requested root, and consumers SHALL use this command rather than depend on the SQLite schema.

#### Scenario: Export a bounded root
- **WHEN** a consumer requests JSON progress for a canonical root
- **THEN** every emitted record is strictly beneath that root and uses a root-relative key

### Requirement: Main TUI presentation
The main screen SHALL show controller connection state, active root, a root-confined browser, explicit selection state, current filename, playback state, elapsed time, duration, percentage, a progress bar, and an ordered queue with current-item highlighting. It SHALL use distinct visual treatment for folders, videos, selected items, focused panes, and queued, playing, paused, stopped, skipped, completed, failed, or missing states. Empty panes SHALL explain the next useful action. A concise footer SHALL expose controls and transient status or errors. Full canonical paths and stored history MAY appear in an explicitly requested details view.

#### Scenario: Empty queue and browser
- **WHEN** either main pane has no displayable entries
- **THEN** that pane explains the next useful action rather than appearing broken or ambiguous

### Requirement: TUI controls and non-destructive actions
The TUI SHALL support opening/changing root (`o`), browser navigation (arrows and Backspace), opening or playing (`Enter`), toggling selection (`v`), add (`a`), add-and-play (`A`), play/pause (`Space`), queue-only removal (`d`/Delete), reorder (`J`/`K`), next (`n`), previous (`p`), seek (Left/`[`/`]` as context permits), retry (`r`), clear completed (`c`), help (`?`), and quit (`q`) with an explicit VLC-process choice. The browser and queue SHALL expose visible buttons for their primary actions. Clearing the full queue SHALL require confirmation. No queue control SHALL delete or modify an underlying media file.

#### Scenario: Remove a queue item
- **WHEN** the user removes or clears an item from the queue
- **THEN** only queue state changes and the media file remains untouched

#### Scenario: Navigate upward at the root
- **WHEN** the user requests parent navigation while already at the library root
- **THEN** the browser remains at that root

### Requirement: Responsive and recoverable UI
TUI input SHALL remain responsive during VLC startup, HTTP polling, root and subfolder browsing, path validation, and database writes. Potentially slow operations SHALL run asynchronously. Resize events SHALL preserve usable browser and queue viewports, and recoverable failures SHALL appear without closing the application.

#### Scenario: Slow filesystem operation
- **WHEN** root browsing or path validation is delayed
- **THEN** the event loop remains responsive and the user receives a result or recoverable error

### Requirement: Authoritative queue semantics
`vlcq`, not VLC, SHALL own queue order and SHALL send VLC one active item at a time. Only explicitly selected videos SHALL enter the queue. Natural completion SHALL persist final progress and start the next item. Manual next SHALL mark the current entry skipped unless completion was independently established. Manual item selection SHALL update queue position, and reordering SHALL persist immediately. Missing files SHALL remain visible as missing and be skipped with a warning. Queue state SHALL survive controller crashes, and only one controller SHALL mutate the database at a time.

#### Scenario: Natural completion
- **WHEN** `vlcq` conservatively observes the active item ending naturally
- **THEN** it records completion and starts the next queued item

#### Scenario: Manual skip
- **WHEN** the user advances before independently observed completion
- **THEN** the current entry becomes skipped rather than completed

### Requirement: Owned VLC process
`vlcq` SHALL launch VLC directly as a dedicated instance, validate required VLC 3 command-line flags, record the owned PID, and verify process identity before signaling it. It MUST NOT terminate or control an unrelated VLC process. Incompatible installations SHALL produce an actionable error.

#### Scenario: Existing unrelated VLC instance
- **WHEN** VLC is already running outside `vlcq`
- **THEN** `vlcq` starts and controls its dedicated instance without terminating the unrelated process

### Requirement: Secure VLC HTTP control
The owned VLC instance SHALL expose its HTTP interface only on `127.0.0.1`, on a selected local port, with a high-entropy per-session password. Session data SHALL live in a mode-0700 directory and mode-0600 file. Readiness probing SHALL have a bounded timeout. The client SHALL reject redirects and unexpected response content or types. Passwords, authorization headers, raw responses, and full media history MUST NOT be logged or printed.

#### Scenario: VLC HTTP response is unexpected
- **WHEN** the endpoint redirects or returns an unexpected content type or malformed body
- **THEN** `vlcq` rejects the response without exposing credentials or guessing queue advancement

### Requirement: Playback observation
While the controller runs, `vlcq` SHALL poll VLC status for the current local media URI, state, elapsed time, duration, and position. Parsing SHALL tolerate missing or changed fields and fail closed for malformed or non-local media URIs. Polling MAY be frequent while playing, slower while paused, and use bounded backoff while stopped or unavailable. Progress SHALL flush promptly on pause, stop, item change, skip, shutdown, and observed completion.

#### Scenario: Malformed observed URI
- **WHEN** VLC reports malformed, remote, or otherwise non-local media
- **THEN** `vlcq` refuses to associate that observation with local progress or automatic advancement

### Requirement: Conservative progress semantics
`vlcq` SHALL persist raw position and duration and derive display percentages. Maximum progress for the same unchanged file MUST NOT decrease due to stale or zero observations. Seeking near the end or manually stopping SHALL NOT prove completion. Completion SHALL use an explicit natural-ended state when reliable or conservative configurable inference based on continuous recent playback and the expected stopped transition. The database SHALL store maximum progress separately from `completion_observed`; uncertain completion SHALL retain progress without setting that flag.

#### Scenario: Stale observation
- **WHEN** a later observation reports a lower or zero position for the same unchanged file
- **THEN** persisted maximum progress does not decrease

#### Scenario: Seek near the end
- **WHEN** the user seeks near the duration and stops playback
- **THEN** progress is retained but the item is not marked complete solely because of the seek or stop

### Requirement: Media identity and path safety
For roots and selected, observed, or queued media, `vlcq` SHALL accept only local directories and regular local files. It SHALL reject remote hosts, credentials, query strings, fragments, malformed escapes, NULs, non-file schemes, unsupported extensions, missing paths, and out-of-root files. Paths SHALL be canonicalized, containment SHALL account for symlink traversal rather than string prefixes, and state storage SHALL NOT follow unsafe symlinks. Browsing and validation MUST NOT modify, move, copy, or delete media. Identity SHALL include device, inode, size, and modification time; a changed fingerprint SHALL be treated as replacement content rather than silently inheriting progress. Same-filesystem renames MAY be recognized by device and inode.

#### Scenario: Symlink escapes the root
- **WHEN** a selected path lexically appears in the root but canonically resolves outside it
- **THEN** `vlcq` rejects it and does not add, play, or modify it

#### Scenario: File fingerprint changes
- **WHEN** a path's device, inode, size, or modification-time fingerprint indicates replacement
- **THEN** old progress is not silently applied to the replacement

### Requirement: Durable private persistence
By default `vlcq` SHALL store data at `~/Library/Application Support/vlcq/vlcq.sqlite3`, with its application directory mode 0700 and state files mode 0600. SQLite SHALL use WAL, transactions, serialized writes, bounded busy timeouts, and a versioned schema with explicit migrations. Corruption or migration failure SHALL preserve the original and report recovery guidance rather than replacing it. Persistence SHALL model media identity/progress, durable ordered queues and states, and—if retained—bounded compact observations.

#### Scenario: Migration failure
- **WHEN** a schema migration cannot complete safely
- **THEN** `vlcq` preserves the original database and stops with recovery instructions

#### Scenario: Controller crash
- **WHEN** the controller terminates unexpectedly
- **THEN** committed queue order and progress remain consistent and resumable

### Requirement: Privacy and network isolation
Normal logs SHALL contain operation categories and counts rather than absolute media paths, credentials, raw HTTP payloads, or playback history. Explicit debug output MAY reveal the current media path but MUST still redact secrets and HTTP authorization data. Any logs SHALL be bounded and private. `vlcq` SHALL perform no network access except loopback communication with its owned VLC instance and SHALL emit no telemetry.

#### Scenario: Ordinary error logging
- **WHEN** a playback or validation operation fails
- **THEN** normal output identifies the error category without disclosing credentials or unrelated media paths

### Requirement: Safe failure handling
If VLC is unavailable, `vlcq` SHALL retain the queue and offer retry or relaunch. On HTTP authentication failure it SHALL stop polling and show guidance rather than retry credentials repeatedly. On VLC crash it SHALL flush the latest valid observation and offer restart at saved progress. Missing or replaced media SHALL be marked and skipped without mutation. If the database is unavailable, unmanaged autoplay SHALL NOT begin. Unsupported HTTP responses SHALL pause automatic advancement rather than guess.

#### Scenario: Database unavailable
- **WHEN** durable queue state cannot be accessed
- **THEN** `vlcq` reports the failure and does not begin unmanaged autoplay

### Requirement: Verification coverage
Automated tests SHALL cover root opening without auto-enqueue, subfolder and parent navigation, explicit selection, standalone-file root establishment, natural ordering, canonical deduplication, invalid and out-of-root operands, queue transitions, monotonic progress, path and URI safety, database migrations, malformed HTTP responses, and replacement detection. A macOS integration test SHALL verify compatible VLC 3 startup, loopback binding, status polling, an autoplay transition, and clean shutdown. If context-menu integration is supplied, its folder and file handoff SHALL also be tested.

#### Scenario: Run acceptance tests
- **WHEN** the project test suite runs in its applicable unit and macOS integration environments
- **THEN** it exercises the safety, queue, progress, UI workflow, and real VLC boundaries required by this specification

### Requirement: Deferred compatibility decisions
Implementation changes that settle VLC isolation flags, quit behavior, completion thresholds, context-menu preselection behavior, detached-controller support, or VLC 4 compatibility SHALL be proposed and documented through OpenSpec before those decisions become normative.

#### Scenario: Make a deferred decision
- **WHEN** a future change chooses behavior for a currently deferred area
- **THEN** its OpenSpec change updates this specification with the resulting requirement and scenarios
