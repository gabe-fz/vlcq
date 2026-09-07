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
`vlcq resume` SHALL restore the persisted current queue entry when it remains unfinished, or otherwise the first unfinished entry, and offer to resume that target at its last trustworthy persisted playback position, with Start over and Cancel alternatives. Explicit playback of a different partially played item SHALL offer the same choice; activating the already playing or paused item SHALL not reload it. Start over SHALL preserve historical maximum progress and completion. `vlcq progress --root <path> --json` SHALL retain its version-1 document and existing field meanings, keyed by validated root-relative paths with position, duration, watched percentage, completion status, and observation time. It MUST NOT include records outside the canonical requested root, and consumers SHALL use this command rather than depend on the SQLite schema.

#### Scenario: Export a bounded root
- **WHEN** a consumer requests JSON progress for a canonical root
- **THEN** every emitted record is strictly beneath that root and uses a root-relative key

#### Scenario: Resume after rewinding
- **WHEN** the user reached 40 minutes, deliberately rewound to 20 minutes, and later requests resume
- **THEN** Resume uses the last trustworthy playback position near 20 minutes while exported maximum progress remains at least 40 minutes

#### Scenario: Resume the persisted current entry
- **WHEN** a saved unfinished queue has a persisted current entry that is not its first entry
- **THEN** `vlcq resume`, including offline mode, targets that current entry rather than queue index zero

#### Scenario: Resume without a current entry
- **WHEN** a saved queue has no persisted current entry
- **THEN** `vlcq resume` targets its first unfinished entry

#### Scenario: Cancel explicit playback
- **WHEN** the user cancels a Resume / Start over choice
- **THEN** current playback and queue contents and order remain unchanged

#### Scenario: Replay completed media
- **WHEN** the user explicitly plays completed media that is not already active
- **THEN** it starts from zero without clearing completion history

### Requirement: Main TUI presentation
The main screen SHALL present full-width Files above Queue, with independently collapsible sections and compact status at the bottom. Both sections SHALL start expanded and share available list space equally; collapsing one SHALL give its space to the other, and collapsing both SHALL leave both headers and bottom status reachable. Each header SHALL remain one terminal line and expose a disclosure control and a compact actions-menu trigger. The Files header SHALL identify the active root and current root-relative folder; the Queue header SHALL show the entry count. Expanded sections SHALL use single-line rows with filenames, selection markers, and compact metadata attached to the same row. Folders, videos, selected items, focused sections, and queued, playing, paused, stopped, skipped, completed, failed, and missing states SHALL have distinct semantic color and non-color indicators. Filenames SHALL be rendered literally, including markup-like characters. Long text SHALL truncate without wrapping or hiding section controls; full paths and stored history SHALL be available through Details. Populated panes SHALL NOT contain instructional paragraphs or persistent action toolbars. Empty panes SHALL provide one short next-action hint. Bottom status SHALL show active playback and connection state without duplicating it elsewhere on the main screen.

#### Scenario: Empty queue and browser
- **WHEN** either expanded section has no displayable entries
- **THEN** it shows one short next-action hint and its actions menu remains available

#### Scenario: Collapse and restore a section
- **WHEN** the user collapses Files or Queue and later expands it
- **THEN** its header remains reachable, the other expanded list uses the freed space, and folder, selection, highlight, and scroll position are preserved

#### Scenario: Literal long filename
- **WHEN** a filename contains bracketed release tags or exceeds the available row width
- **THEN** it remains literal semantically colored text on one line, controls stay reachable, and Details exposes the complete name and path

### Requirement: TUI controls and non-destructive actions
The TUI SHALL support opening/changing root (`o`), browser navigation (arrows and Backspace), opening or playing (`Enter`), toggling selection (`v`), add (`a`), add-and-play (`A`), play/pause (`Space`), queue-only removal (`d`/Delete), reorder (`J`/`K`), next (`n`), previous (`p`), seek (Left/`[`/`]` as context permits), retry (`r`), clear completed (`c`), help (`?`), and quit (`q`) with an explicit VLC-process choice. Right-click menus SHALL be the primary surface for contextual actions, with a compact visible actions trigger per section, a player/application menu at the bottom, and a keyboard menu binding. Library-selection actions SHALL target explicit library selections or the highlighted library video; single-item playback SHALL target the clicked or highlighted item independently of a batch. Queue-item actions SHALL target only their declared queue entry. Menus SHALL provide Add to end, Play next, Play now, Resume, Start over, transport, reconnect, removal, reordering, undo, search, filters, details, help, and quit in the appropriate context. Resume and Start over SHALL be directly available for applicable highlighted items as well as through the playback-choice dialog. Unavailable menu actions SHALL be disabled or omitted. Clearing the full queue or completed entries SHALL require confirmation. No queue control SHALL delete or modify an underlying media file. Text-entry fields SHALL receive ordinary typing without triggering global playback or navigation shortcuts.

#### Scenario: Remove a queue item
- **WHEN** the user removes or clears an item through a queue context menu
- **THEN** only queue state changes and the media file remains untouched

#### Scenario: Navigate upward at the root
- **WHEN** the user requests parent navigation while already at the library root
- **THEN** the browser remains at that root

#### Scenario: Type a filename search
- **WHEN** a text field has focus and the user types characters that are also application shortcuts
- **THEN** those characters edit the field without playing, queueing, seeking, or quitting

#### Scenario: Use pane-local controls
- **WHEN** the user invokes a menu from Files or Queue
- **THEN** it targets only that menu's declared selection or highlighted object and not an object from the other section

#### Scenario: Dismiss or invalidate a menu
- **WHEN** the user dismisses a context menu or its target disappears before invocation
- **THEN** dismissal performs no queue or playback mutation, and a stale action fails safely rather than acting on the entry now occupying the same row index

#### Scenario: Unsafe menu target
- **WHEN** a selected media path is missing, replaced, or resolves outside the root before a menu action executes
- **THEN** existing action-specific identity and containment checks apply and unsafe mutation or inherited playback history is prevented

### Requirement: Responsive and recoverable UI
TUI input SHALL remain responsive during VLC startup, HTTP polling, root and subfolder browsing, path validation, and database writes. Potentially slow operations SHALL run asynchronously. Resize events SHALL preserve collapse state, selection, highlight, and scrolling and SHALL keep menus, section headers, bottom status, and usable expanded list viewports accessible at 80-by-24 and larger terminal sizes. With both sections expanded at 80-by-24, each list SHALL expose at least five entry rows when populated. Menus near terminal edges SHALL remain inside the viewport and support scrolling when needed. Recoverable failures SHALL appear in a bounded bottom notice without closing the application, growing the layout, or hiding the lists; complete error text SHALL remain available on demand.

#### Scenario: Slow filesystem operation
- **WHEN** root browsing or path validation is delayed
- **THEN** the event loop remains responsive and the user receives a result or recoverable error

#### Scenario: Resize a populated screen
- **WHEN** the user resizes between 120-by-40, 80-by-24, and a tall narrow 80-by-50 terminal
- **THEN** the stacked layout remains usable, each expanded list retains its state, and no large or wrapped button rows appear

#### Scenario: Open a menu at an edge
- **WHEN** a row near the bottom or right edge is right-clicked
- **THEN** every applicable action is reachable inside the viewport without resizing the terminal

### Requirement: Read-only watch-history presentation
Library and queue rows SHALL show compact In progress or Completed evidence independently of queue state, with known furthest progress attached to the same single-line row as the filename. Rows with no matching progress SHALL omit a history badge rather than repeat “No recorded progress”; an explicitly requested Details view SHALL identify missing history as “No recorded progress.” Completion SHALL require recorded completion evidence; percentages SHALL describe furthest progress reached, not measured viewing coverage. Rows SHALL expose already-queued membership, and Details SHALL show resume position, furthest progress, known duration, and last trustworthy playback time. Missing history and duration SHALL not be fabricated as zero-time evidence. Displaying history SHALL neither create media records nor change observation timestamps and SHALL match the current canonical file fingerprint. Routine history refresh SHALL validate file identities without blocking input, use bounded read-only retrieval for the visible folder and queue, and preserve unchanged row identity, highlight, and scrolling.

#### Scenario: Browse an unplayed file
- **WHEN** a valid video has no matching playback history
- **THEN** its row has no history badge or last-played date, Details explicitly identifies absent history, and browsing does not create history

#### Scenario: Completed video is replaying
- **WHEN** a video with recorded completion is currently playing again
- **THEN** the UI preserves Completed history while independently showing Playing queue state and live position

#### Scenario: Replacement at an existing path
- **WHEN** a video's fingerprint differs from the stored media fingerprint
- **THEN** neither its history display nor its resume action inherits the previous file's progress

#### Scenario: Refresh history while identity checks are delayed
- **WHEN** filesystem identity validation is delayed while a user types, highlights a row, or scrolls
- **THEN** the interaction remains responsive and unchanged rows retain their identity, highlight, and scroll position

### Requirement: Library discovery and selection visibility
Files SHALL provide on-demand case-insensitive filename search and All, In progress, and Not completed filters within the current folder through its menu. In progress SHALL mean positive recorded progress without completion; Not completed SHALL include files without history. Directories SHALL remain available for navigation. Filtering SHALL NOT enqueue media or silently discard selections. When selections exist, a compact persistent header summary SHALL show the selected count and hidden selection count, including selections in other folders, with a clear-selection menu action. Active search or non-default filters SHALL be visibly indicated and clearable without a permanent filter toolbar. Action targeting SHALL distinguish highlighted-item playback from explicit batch queueing, and batch menu labels SHALL disclose the selected count and hidden count when nonzero.

#### Scenario: Selection is outside the visible listing
- **WHEN** filtering or folder navigation hides selected videos
- **THEN** the header discloses the hidden selections even when Files is collapsed and batch actions continue to target them until cleared or consumed

#### Scenario: Enter while a batch is selected
- **WHEN** a video is highlighted and the user activates Enter or its single-item Play action
- **THEN** playback targets that video rather than silently playing the batch, while unrelated selections remain intact

### Requirement: Separate trustworthy resume state
The application SHALL persist a last trustworthy playback position separately from maximum progress and a playback timestamp separately from media-record creation time. Manual rewind and Start over SHALL be able to lower the resume position without lowering history. Stale responses, wrong-media observations, unavailable status, and an unverified stop-time zero SHALL NOT overwrite a valid resume position. Before switching, stopping, removing active media, or quitting, the controller SHALL attempt a bounded final observation and retain its last valid observation if that fails.

#### Scenario: Old poll arrives after switching
- **WHEN** a response from the previous playback operation arrives after a new operation begins
- **THEN** it does not replace the new item's resume state or trigger automatic advancement

#### Scenario: Upgrade legacy history
- **WHEN** existing records contain maximum progress but no separate resume position or reliable playback timestamp
- **THEN** maximum progress and completion remain unchanged, any resume fallback is identified as furthest recorded progress, and unknown last-played time is not invented

### Requirement: Explicit deterministic queue insertion
Add to end SHALL append validated selections without interruption. Play next SHALL place naturally ordered, canonical-path-deduplicated selections immediately after the active item, moving existing queued matches rather than duplicating them and never moving or restarting the active item. Without an active item, Play next SHALL place selections at the front without autoplay. Unselected entries SHALL retain relative order. Play now SHALL queue the target if needed and switch through the resume decision. Batch add-and-play SHALL add the selected batch and target its first naturally ordered item. New actions SHALL reject unsafe paths before mutation.

#### Scenario: Play-next selection includes existing entries
- **WHEN** selected videos already occur elsewhere in the queue
- **THEN** they move together after the active entry in natural order without duplicates, interruptions, or changes to other entries' relative order

#### Scenario: Play next with no active item
- **WHEN** the user requests Play next while nothing is active
- **THEN** the selected videos move to the queue front and remain stopped

#### Scenario: Automatic advance reaches partially played media
- **WHEN** natural completion advances to an incomplete item with a trustworthy resume position
- **THEN** playback resumes it without an interactive prompt; items without usable resume state and completed items start from zero

#### Scenario: Invalid batch operand
- **WHEN** any selected operand is missing, unsupported, or canonically outside the root
- **THEN** the batch action reports the failure without partially inserting or reordering entries or modifying media

### Requirement: Complete mouse-operated playback workflows
Single-clicking a video or queue row SHALL highlight it without changing playback; folder activation SHALL remain available by mouse. Browser video selection SHALL use compact inline checkbox hit targets rather than standalone button rows. Right-clicking a row SHALL highlight and open its context menu without opening a folder, toggling selection, or starting playback. Small visible menu triggers SHALL provide a mouse fallback when the terminal intercepts right-click. Context menus and compact controls SHALL permit folder navigation, queue insertion, playback choice, pause/resume, previous/next, relative seek, removal, reorder, clearing, undo, reconnect, details, help, and quit. Every dialog SHALL have compact clickable action and cancellation controls. Menus SHALL support keyboard navigation, activation, Escape cancellation, and focus restoration. Absolute seek by clicking the progress track SHALL be available only with a connected matching active item and known positive duration, and SHALL invalidate natural-end inference from before the seek. Essential actions SHALL remain reachable at an 80-by-24 terminal size without requiring keyboard shortcuts.

#### Scenario: Complete a mouse-only workflow
- **WHEN** the user opens a folder, selects videos, adds them, resumes playback, reorders and removes entries, confirms a clear, undoes it, and quits using menus and mouse controls
- **THEN** each step is reachable without typing a keyboard shortcut, including modal confirmation and cancellation

#### Scenario: Click an inactive row
- **WHEN** the user single-clicks a different video or queue row
- **THEN** only highlight and details target change, no play command is issued, and current playback continues uninterrupted

#### Scenario: Right-click a folder
- **WHEN** the user right-clicks a folder row
- **THEN** a folder context menu opens without entering that folder or mutating the queue

#### Scenario: Terminal intercepts right-click
- **WHEN** terminal behavior prevents the application receiving a right-click
- **THEN** the section actions trigger and keyboard menu binding expose the same applicable actions for the highlighted item

#### Scenario: Seek with unknown duration
- **WHEN** the active video's duration is unknown or VLC is disconnected
- **THEN** clicking the progress track sends no absolute-seek command and the unavailable action is explained

### Requirement: Safe active-item removal and bounded undo
Removing the active entry, or clearing a set containing it, SHALL capture valid progress and confirm that owned VLC playback has stopped before removing the entry. It SHALL NOT automatically play the next entry. If stopping cannot be established, removal SHALL fail visibly while retaining the active entry and observation association. Removal and clear SHALL offer one session-local undo of the latest successful removal operation; undo SHALL restore queue order and retained history but never restart playback. A subsequent queue mutation or root change SHALL invalidate that undo with visible feedback. Undo SHALL revalidate root confinement and fingerprints and reject unsafe or replaced media; missing media SHALL remain visibly missing.

#### Scenario: Remove currently playing media
- **WHEN** the user removes the active entry and the owned VLC process confirms playback stopped
- **THEN** its progress is retained, the entry is removed, and no successor starts automatically

#### Scenario: Stop cannot be confirmed
- **WHEN** the user clears a queue containing active media and VLC control fails without proof playback stopped
- **THEN** no entries are removed and the application shows a recoverable error rather than abandoning observation ownership

#### Scenario: Undo a clear
- **WHEN** the user undoes the most recent clear before another queue mutation
- **THEN** entries return in their previous order without resetting history or restarting previously active playback

#### Scenario: Undo after replacement
- **WHEN** a removed video's path now resolves outside the root or its fingerprint changed
- **THEN** undo rejects the restoration without applying old history or partially restoring the snapshot

#### Scenario: Undo expires after mutation
- **WHEN** an undo is available and a subsequent queue mutation or root change occurs
- **THEN** the undo is invalidated and the application immediately reports that expiration

### Requirement: Readable stable playback UI and reconnect
The bottom player area SHALL show the active filename, connection state, and elapsed/known total time using minutes/seconds or hours/minutes/seconds on one bounded line, with a one-line progress track and percentage. Remaining time SHALL be available in the player menu or Details rather than duplicating the live summary. Unknown duration, percentage, and remaining time SHALL be explicitly unknown. An idle player SHALL show a short idle/connection state instead of a filename placeholder and multiple unknown counters. A separate bottom notice SHALL occupy at most one line and SHALL not repeat the playback summary or selection details. The player SHALL remain visible with either or both sections collapsed. Highlighted-item Details SHALL remain distinct from the active player and open only on request. Routine polling SHALL preserve row identity, highlight, scroll position, menu target, and actionable controls. Reconnect SHALL be available without a queue selection and SHALL not restart media without an explicit playback action. Overlapping reconnect requests SHALL not create duplicate owned VLC processes or polling loops.

#### Scenario: Browse while playback updates
- **WHEN** the user highlights a different video or scrolls while status polling runs
- **THEN** the bottom player still names the active file exactly once on the main screen outside its queue row, and the browser highlight, details target, and scroll position remain stable

#### Scenario: Reconnect with an empty queue
- **WHEN** VLC is unavailable and the user invokes Reconnect with no selected queue item
- **THEN** connection recovery proceeds independently and does not enqueue or autoplay media

#### Scenario: Long playback failure
- **WHEN** playback fails with an error longer than the terminal width
- **THEN** the notice remains one line, complete error information is available on demand, and Files and Queue retain their viewports

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
