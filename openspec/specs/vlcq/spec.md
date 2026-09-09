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
`vlcq resume` SHALL restore the persisted current queue entry when it remains unfinished, or otherwise the first unfinished entry, and offer to resume that target at its last trustworthy persisted playback position, with Start over and Cancel alternatives. Explicit playback of a different partially played item SHALL offer the same choice; activating the already playing or paused item SHALL not reload it. Start over SHALL preserve historical maximum progress and watched classification. A media item SHALL be considered watched when explicit completion evidence exists or its furthest recorded position reaches the configured percentage of a known positive duration; the threshold SHALL default to 90 percent and SHALL accept a validated user override from 1 through 100. Threshold-based watched classification SHALL NOT by itself stop playback, advance the queue, or rewrite the distinct queue outcome state. `vlcq progress --root <path> --json` SHALL retain its version-1 document and existing field meanings, keyed by validated root-relative paths with position, duration, watched percentage, completion status, and observation time; its completion status SHALL use the same watched classification. It MUST NOT include records outside the canonical requested root, and consumers SHALL use this command rather than depend on the SQLite schema.

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
- **WHEN** the user explicitly plays media classified as watched/completed that is not already active
- **THEN** it starts from zero without clearing maximum progress or watched history

#### Scenario: Reach the watched threshold
- **WHEN** furthest progress reaches 90 percent with the default configuration and no explicit natural-completion event has occurred
- **THEN** the item is classified as watched in the TUI and progress export without stopping playback, advancing the queue, or changing its queue outcome state

#### Scenario: Override the watched threshold
- **WHEN** the user configures a valid threshold other than 90 percent
- **THEN** history presentation, filtering, replay choice, and progress export consistently use that threshold

#### Scenario: Reject an invalid watched threshold
- **WHEN** the configured threshold is not a whole percentage from 1 through 100
- **THEN** startup fails clearly without mutating queue, history, or media state

### Requirement: Main TUI presentation
The main screen SHALL present full-width Files above Queue, with independently collapsible sections and a bounded multi-line player/status area at the bottom. Both sections SHALL start expanded and share available list space equally; collapsing one SHALL give its space to the other, and collapsing both SHALL leave both headers and bottom status reachable. Each section header SHALL remain one terminal line and expose a disclosure control, a compact action bar of frequent actions, and an ellipsis overflow-menu trigger. Files SHALL expose Open, Search/filter, and Add; Queue SHALL expose Play/pause, Next, and Clear completed; the player SHALL expose Previous, Play/pause, and Next. The Files header SHALL identify the active root and selection/filter state, and the Queue header SHALL show the entry count. The Files pane SHALL show the library as one inline tree whose folders expand and collapse without replacing the surrounding hierarchy. Expanded sections SHALL use bounded non-wrapping items with filenames, selection markers, compact metadata, and any recorded-progress bar attached to the same item. Folders, files, and syntactic filename components including the stem and extension SHALL have distinct semantic colors while preserving the literal filename; progress-state colors SHALL be confined to progress bars and status indicators rather than recoloring the filename as watched. Selected items, focused sections, and queued, playing, paused, stopped, skipped, watched/completed, failed, and missing states SHALL retain distinct non-color indicators. Pending queue position SHALL be implicit rather than repeated as a `QUEUED` badge; `STOPPED` SHALL appear only on the persisted current playback item. Queue highlight, current playback state, item outcome, and read-only playback history SHALL remain visually distinguishable. Long text SHALL truncate without wrapping or hiding section controls; full paths and stored history SHALL be available through Details. Populated panes SHALL NOT contain instructional paragraphs. Empty panes SHALL provide one short next-action hint. Bottom status SHALL show active playback and connection state without duplicating it elsewhere on the main screen.

#### Scenario: Empty queue and browser
- **WHEN** either expanded section has no displayable entries
- **THEN** it shows one short next-action hint and its action bar and overflow menu remain available

#### Scenario: Expand several tree branches
- **WHEN** the user expands folders in separate branches of the library tree
- **THEN** the root, both expanded branches, their visible descendants, and surrounding hierarchy remain in the same scrollable Files view

#### Scenario: Collapse and restore a section
- **WHEN** the user collapses Files or Queue and later expands it
- **THEN** its header remains reachable, the other expanded list uses the freed space, and tree expansion, selection, highlight, and scroll position are preserved

#### Scenario: Literal long filename
- **WHEN** a filename contains bracketed release tags, numbers, and an extension or exceeds the available item width
- **THEN** its literal components are semantically color-coded without changing their text, it remains bounded without wrapping, controls stay reachable, and Details exposes the complete name and path

#### Scenario: Use a frequent header action
- **WHEN** the user invokes Open, Search/filter, Add, Play/pause, Next, Clear completed, or Previous from its declared action bar
- **THEN** the action uses the same validation and targeting semantics as its keyboard or overflow-menu equivalent

#### Scenario: Pending and stopped presentation
- **WHEN** the queue contains pending entries and one stopped current entry
- **THEN** pending rows omit a redundant `QUEUED` badge and only the current entry can display `STOPPED`

### Requirement: TUI controls and non-destructive actions
The TUI SHALL support opening/changing root (`o`), tree navigation and folder expansion (arrows, Enter, and Backspace as context permits), opening or playing (`Enter`), toggling selection (`v`), add (`a`), add-and-play (`A`), play/pause (`Space`), queue-only removal (`d`/Delete), reorder (`J`/`K`), next (`n`), previous (`p`), seek (Left/`[`/`]` as context permits), retry (`r`), clear completed (`c`), help (`?`), and quit (`q`) with an explicit VLC-process choice. Right-click menus SHALL provide row-contextual actions; compact visible action bars SHALL provide frequent section and player actions; ellipsis triggers and a keyboard menu binding SHALL expose overflow actions. Library-selection actions SHALL target explicit library selections or the highlighted library video; single-item playback SHALL target the clicked or highlighted item independently of a batch. Queue-item actions SHALL target only their declared queue entry. Collectively the action bars and menus SHALL provide Add to end, Play next, Play now, Resume, Start over, transport, reconnect, removal, reordering, undo, search, filters, details, help, and quit in the appropriate context. Resume and Start over SHALL be directly available for applicable highlighted items as well as through the playback-choice dialog. Unavailable visible actions SHALL be disabled and unavailable menu actions SHALL be disabled or omitted. Clearing the full queue or completed entries SHALL require confirmation. No queue control SHALL delete or modify an underlying media file. Text-entry fields SHALL receive ordinary typing without triggering global playback or navigation shortcuts.

#### Scenario: Remove a queue item
- **WHEN** the user removes or clears an item through a queue context menu
- **THEN** only queue state changes and the media file remains untouched

#### Scenario: Collapse a folder at the root
- **WHEN** the user collapses a folder in the library tree
- **THEN** only that folder's descendants are hidden and the browser remains rooted at the configured library

#### Scenario: Navigate upward at the root
- **WHEN** the user requests parent navigation while the tree focus is already at the library root
- **THEN** the browser remains at that root without discarding expansion or selection state

#### Scenario: Type a filename search
- **WHEN** a text field has focus and the user types characters that are also application shortcuts
- **THEN** those characters edit the field without playing, queueing, seeking, or quitting

#### Scenario: Use pane-local controls
- **WHEN** the user invokes an action bar or menu from Files or Queue
- **THEN** it targets only that surface's declared selection or highlighted object and not an object from the other section

#### Scenario: Dismiss or invalidate a menu
- **WHEN** the user dismisses a context menu or its target disappears before invocation
- **THEN** dismissal performs no queue or playback mutation, and a stale action fails safely rather than acting on the entry now occupying the same row index

#### Scenario: Unsafe menu target
- **WHEN** a selected media path is missing, replaced, or resolves outside the root before a visible or overflow action executes
- **THEN** existing action-specific identity and containment checks apply and unsafe mutation or inherited playback history is prevented

### Requirement: Responsive and recoverable UI
TUI input SHALL remain responsive during VLC startup, HTTP polling, root and tree-branch discovery, recursive search/filter discovery, path validation, and database writes. Potentially slow operations SHALL run asynchronously, stale branch results SHALL NOT attach to a replaced root, and expanding one branch SHALL NOT require eager traversal of unrelated collapsed branches. Resize events SHALL preserve section collapse state, tree expansion, selection, highlight, and scrolling and SHALL keep menus, section headers, bottom status, and usable expanded list viewports accessible at 80-by-24 and larger terminal sizes. With both sections expanded at 80-by-24, each list SHALL expose at least five compact entry items when populated. Menus near terminal edges SHALL remain inside the viewport and support scrolling when needed. Recoverable failures SHALL appear in a bounded bottom notice without closing the application, growing the layout without bound, or hiding the lists; complete error text SHALL remain available on demand.

#### Scenario: Slow filesystem operation
- **WHEN** expanding a folder or recursively searching the library is delayed
- **THEN** the event loop and already-visible tree remain responsive and the user receives a result or recoverable error

#### Scenario: Stale folder result
- **WHEN** an asynchronous folder load completes after the root changed or its parent branch collapsed
- **THEN** the stale result does not appear under the new tree or unexpectedly reopen the branch

#### Scenario: Resize a populated screen
- **WHEN** the user resizes between 120-by-40, 80-by-24, and a tall narrow 80-by-50 terminal
- **THEN** the stacked layout remains usable, each expanded list retains its state, frequent and overflow actions remain reachable, and no large or wrapped button rows appear

#### Scenario: Open a menu at an edge
- **WHEN** a row near the bottom or right edge is right-clicked
- **THEN** every applicable action is reachable inside the viewport without resizing the terminal

### Requirement: Read-only watch-history presentation
Library and queue items with positive recorded progress SHALL show a thick, bounded progress bar and a whole-number percentage computed from furthest position and known positive duration. The bar SHALL use distinct progress colors below and at/above the configured watched threshold, which defaults to 90 percent. Items with progress but unknown duration SHALL show an explicitly unknown percentage without fabricating bar completion. Items with no matching progress SHALL omit the bar and history badge rather than repeat “No recorded progress”; an explicitly requested Details view SHALL identify missing history as “No recorded progress.” Watched classification SHALL require explicit completion evidence or furthest progress at/above the configured threshold; percentages SHALL describe furthest progress reached, not measured viewing coverage. Items SHALL expose already-queued membership, and Details SHALL show resume position, furthest progress, known duration, configured threshold, watched classification, and last trustworthy playback time. Missing history and duration SHALL not be fabricated as zero-time evidence. Displaying or deriving watched history SHALL neither create media records, rewrite explicit completion evidence, nor change observation timestamps and SHALL match the current canonical file fingerprint. Routine history refresh SHALL validate file identities without blocking input, use bounded read-only retrieval for visible tree and queue items, and preserve unchanged item identity, highlight, tree expansion, and scrolling.

#### Scenario: Browse an unplayed file
- **WHEN** a valid video has no matching playback history
- **THEN** its item has no progress bar, percentage, history badge, or last-played date, Details explicitly identifies absent history, and browsing does not create history

#### Scenario: Display partial progress
- **WHEN** a file has furthest progress of 45 minutes against a known 60-minute duration
- **THEN** its Files and Queue items show a 75 percent progress bar in the below-threshold color

#### Scenario: Display watched progress
- **WHEN** a file reaches or exceeds the configured watched threshold
- **THEN** its Files and Queue progress bars use the watched color and its history is classified as watched

#### Scenario: Completed video is replaying
- **WHEN** a video with watched history is currently playing again
- **THEN** the UI preserves its watched progress bar while independently showing Playing queue state and live player progress

#### Scenario: Replacement at an existing path
- **WHEN** a video's fingerprint differs from the stored media fingerprint
- **THEN** neither its history display nor its resume action inherits the previous file's progress

#### Scenario: Refresh history while identity checks are delayed
- **WHEN** filesystem identity validation is delayed while a user types, expands a branch, highlights an item, or scrolls
- **THEN** the interaction remains responsive and unchanged items retain their identity, highlight, expansion, and scroll position

### Requirement: Library discovery and selection visibility
Files SHALL provide on-demand case-insensitive filename search and All, In progress, and Not watched filters across the configured library tree through its action bar and overflow menu. Search/filter discovery SHALL recursively inspect root-confined folders asynchronously, show matching files with enough ancestor context to locate them in the tree, and SHALL NOT permanently expand unrelated branches. In progress SHALL mean positive recorded progress without watched classification; Not watched SHALL include files without history and files below the configured threshold. Directories SHALL remain available for tree navigation. Filtering SHALL NOT enqueue media or silently discard selections. When selections exist, a compact persistent header summary SHALL show the selected count and hidden selection count, including selections in collapsed or filtered branches, with a clear-selection overflow action. Active search or non-default filters SHALL be visibly indicated and clearable without a permanent filter toolbar. Action targeting SHALL distinguish highlighted-item playback from explicit batch queueing, and batch menu labels SHALL disclose the selected count and hidden count when nonzero.

#### Scenario: Selection is outside the visible listing
- **WHEN** filtering or a collapsed branch hides selected videos
- **THEN** the header discloses the hidden selections even when Files is collapsed and batch actions continue to target them until cleared or consumed

#### Scenario: Search nested folders
- **WHEN** the user searches for a filename located beneath a collapsed nested folder
- **THEN** the matching file and its ancestor context appear in the same Files tree without enqueueing it or losing prior branch expansion state

#### Scenario: Filter at a custom threshold
- **WHEN** the user applies In progress or Not watched after configuring a non-default watched threshold
- **THEN** results use that same threshold consistently across the full library tree

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
Single-clicking a video or queue item SHALL highlight it without changing playback; folder expansion and collapse SHALL remain available by mouse without replacing the surrounding Files tree. A queue highlight SHALL be persisted immediately by queue-entry identity, independently of the current playback identity, and SHALL be restored across routine refresh, reorder, collapse and expansion, and application restart while that entry remains in the active queue. If the persisted entry no longer belongs to the active queue, the stale selection SHALL be cleared safely. Browser video selection SHALL use compact inline checkbox hit targets rather than standalone button rows. Right-clicking an item SHALL highlight and open its context menu without expanding a folder, toggling selection, or starting playback. One-line visible action bars SHALL provide frequent mouse actions, while ellipsis triggers SHALL expose overflow actions when the terminal intercepts right-click. Context menus and compact controls SHALL permit tree navigation, queue insertion, playback choice, pause/resume, previous/next, relative seek, removal, reorder, clearing, undo, reconnect, details, help, and quit. Every dialog SHALL have compact clickable action and cancellation controls. Menus SHALL support keyboard navigation, activation, Escape cancellation, and focus restoration. Absolute seek by clicking the live progress track SHALL be available only with a connected matching active item and known positive duration, and SHALL invalidate natural-end inference from before the seek. Per-item history bars SHALL be read-only and SHALL NOT seek or change playback when clicked. Essential actions SHALL remain reachable at an 80-by-24 terminal size without requiring keyboard shortcuts.

#### Scenario: Complete a mouse-only workflow
- **WHEN** the user expands folders, selects videos, adds them, resumes playback, reorders and removes entries, confirms a clear, undoes it, and quits using action bars, menus, and mouse controls
- **THEN** each step is reachable without typing a keyboard shortcut, including modal confirmation and cancellation

#### Scenario: Click an inactive row
- **WHEN** the user single-clicks a different video or queue item
- **THEN** only highlight and details target change, no play command is issued, and current playback continues uninterrupted

#### Scenario: Persist an inactive queue-row click
- **WHEN** the user single-clicks a queue row other than the current playback item
- **THEN** its identity is immediately saved as the queue highlight and details target independently of the current playback identity

#### Scenario: Restore a queue highlight
- **WHEN** polling refreshes the queue or the application reopens with the highlighted entry still in the active queue
- **THEN** the same entry remains highlighted regardless of index changes and independently of the current playback marker

#### Scenario: Selected queue entry disappears
- **WHEN** the selected entry is removed or the active library changes
- **THEN** its stale persisted identity is cleared without selecting, playing, or modifying an unrelated media item by reused position

#### Scenario: Right-click a folder
- **WHEN** the user right-clicks a folder item
- **THEN** a folder context menu opens without changing its expansion state or mutating the queue

#### Scenario: Terminal intercepts right-click
- **WHEN** terminal behavior prevents the application receiving a right-click
- **THEN** visible action bars and ellipsis triggers expose the same frequent and applicable overflow actions

#### Scenario: Click item history progress
- **WHEN** the user clicks a Files or Queue history progress bar
- **THEN** no seek, queue mutation, or playback command occurs

#### Scenario: Seek with unknown duration
- **WHEN** the active video's duration is unknown or VLC is disconnected
- **THEN** clicking the live progress track sends no absolute-seek command and the unavailable action is explained

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
The bottom player area SHALL use a bounded, slightly taller layout showing the active filename, connection state, elapsed/known total time, one thick live progress track, whole-number percentage, and a one-line compact action bar with Previous, Play/pause, Next, and an ellipsis overflow trigger. Remaining time SHALL be available in the player overflow menu or Details rather than duplicating the live summary. Unknown duration, percentage, and remaining time SHALL be explicitly unknown. An idle player SHALL show a short idle/connection state instead of a filename placeholder and multiple unknown counters. A separate bottom notice SHALL occupy at most one line and SHALL not repeat the playback summary or selection details. The player area SHALL remain visible with either or both sections collapsed and SHALL remain bounded at 80-by-24. Highlighted-item Details SHALL remain distinct from the active player and open only on request. Routine polling SHALL update live controls and progress while preserving tree and queue item identity, highlight, expansion, scroll position, menu target, and actionable controls. Reconnect SHALL be available without a queue selection and SHALL not restart media without an explicit playback action. Overlapping reconnect requests SHALL not create duplicate owned VLC processes or polling loops.

#### Scenario: Browse while playback updates
- **WHEN** the user highlights a different video, expands a folder, or scrolls while status polling runs
- **THEN** the bottom player still names the active file exactly once on the main screen outside its queue item, and tree expansion, browser highlight, details target, and scroll position remain stable

#### Scenario: Use the thick live progress track
- **WHEN** matching active media has a known positive duration
- **THEN** the bottom area shows a thick bounded track and percentage that update without changing the height of the layout

#### Scenario: Reconnect with an empty queue
- **WHEN** VLC is unavailable and the user invokes Reconnect with no selected queue item
- **THEN** connection recovery proceeds independently and does not enqueue or autoplay media

#### Scenario: Long playback failure
- **WHEN** playback fails with an error longer than the terminal width
- **THEN** the notice remains one line, complete error information is available on demand, and Files and Queue retain usable viewports

### Requirement: Authoritative queue semantics
`vlcq`, not VLC, SHALL own queue order and SHALL stage in VLC no more than the active item and the next eligible queue item needed for native Next interoperability. Only explicitly selected videos SHALL enter either queue. The persisted queue highlight SHALL be independent of the persisted current playback item. At most one entry SHALL have a transient playback state of playing, paused, or stopped, and that entry MUST be the persisted current playback item. Changing the current item SHALL return every prior non-terminal transient entry to pending; completed, skipped, failed, and missing outcomes and all playback history SHALL remain unchanged. Opening an existing queue SHALL transactionally repair stale transient states and invalid current or selected identities without deleting queue entries, changing order, modifying history, or accessing media destructively. Natural completion SHALL persist final progress and start or adopt the next item. Manual next through either `vlcq` or the owned VLC interface SHALL mark the current entry skipped unless completion was independently established. An observed VLC transition SHALL advance the authoritative queue only when the new local media identity matches the staged successor; unexpected media SHALL NOT be adopted or associated with queue history. Activating a highlighted item for playback SHALL update the current playback identity without conflating it with highlight persistence, and reordering SHALL persist immediately and update the staged successor without restarting the active item. Missing files SHALL remain visible as missing and be skipped with a warning. Queue state SHALL survive controller crashes, and only one controller SHALL mutate the database at a time.

#### Scenario: Natural completion
- **WHEN** `vlcq` conservatively observes the active item ending naturally or transitioning to the staged successor after independent near-end evidence
- **THEN** it records completion and makes the next queued item current without restarting media that VLC already started

#### Scenario: Manual skip
- **WHEN** the user advances through `vlcq` or the owned VLC interface before independently observed completion
- **THEN** the current entry becomes skipped and the staged successor becomes the persisted current item

#### Scenario: Native Next has no successor
- **WHEN** the active item has no eligible successor in the authoritative queue
- **THEN** VLC contains no fabricated successor and `vlcq` does not restart or invent a queue item

#### Scenario: Unexpected VLC media transition
- **WHEN** VLC reports media other than the active item or its staged successor
- **THEN** `vlcq` pauses automatic advancement, does not adopt that media, and does not write its observations to queue history

#### Scenario: Successor changes while playing
- **WHEN** a queue mutation changes the item immediately after the active item
- **THEN** `vlcq` updates VLC's staged successor without restarting or seeking the active item

#### Scenario: Switch away from a stopped item
- **WHEN** a stopped current item exists and playback is activated on another queue entry
- **THEN** the prior item becomes pending and only the new current item may acquire a transient playback state

#### Scenario: Repair multiple stopped entries
- **WHEN** an existing queue contains multiple stopped entries but only one persisted current identity
- **THEN** opening the queue retains stopped only for that current entry, converts other stale transient states to pending, and preserves order, outcomes, and playback history

#### Scenario: Repair an invalid current identity
- **WHEN** persisted current or selected identities do not belong to the active queue
- **THEN** the invalid identities are cleared and stale transient entries become pending without autoplay or media-file modification

### Requirement: Owned VLC process
`vlcq` SHALL launch VLC directly as a dedicated instance, validate required VLC 3 command-line flags, record the owned PID, and verify process identity before signaling it. The dedicated instance SHALL start with repeat-current, repeat-all, and random playback explicitly disabled regardless of saved VLC preferences. It MUST NOT terminate or control an unrelated VLC process. Incompatible installations SHALL produce an actionable error.

#### Scenario: Existing unrelated VLC instance
- **WHEN** VLC is already running outside `vlcq`
- **THEN** `vlcq` starts and controls its dedicated instance without terminating the unrelated process

#### Scenario: Saved repeat preference
- **WHEN** the user's VLC preferences previously enabled repeat-current, repeat-all, or random playback
- **THEN** the dedicated instance starts with those modes disabled so native Next follows the staged queue order

### Requirement: Secure VLC HTTP control
The owned VLC instance SHALL expose its HTTP interface only on `127.0.0.1`, on a selected local port, with a high-entropy per-session password. Session data SHALL live in a mode-0700 directory and mode-0600 file. Readiness probing SHALL have a bounded timeout. The client SHALL reject redirects and unexpected response content or types. Passwords, authorization headers, raw responses, and full media history MUST NOT be logged or printed.

#### Scenario: VLC HTTP response is unexpected
- **WHEN** the endpoint redirects or returns an unexpected content type or malformed body
- **THEN** `vlcq` rejects the response without exposing credentials or guessing queue advancement

### Requirement: Playback observation
While the controller runs, `vlcq` SHALL poll VLC status for the current local media URI, stable VLC playlist identity, state, elapsed time, duration, and position. Parsing SHALL tolerate missing or changed fields and fail closed for malformed or non-local media URIs. Polling SHALL distinguish observations of the active item, the staged successor, and unexpected media before changing queue state or history. Polling MAY be frequent while playing, slower while paused, and use bounded backoff while stopped or unavailable. Progress SHALL flush promptly on pause, stop, item change, skip, shutdown, and observed completion.

#### Scenario: Malformed observed URI
- **WHEN** VLC reports malformed, remote, or otherwise non-local media
- **THEN** `vlcq` refuses to associate that observation with local progress or automatic advancement

#### Scenario: Direct transition without stopped observation
- **WHEN** VLC changes directly from the active item to the staged successor between polls
- **THEN** `vlcq` captures the last trustworthy active-item observation, classifies the transition conservatively, and adopts the already-playing successor without replaying it

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
By default `vlcq` SHALL store data at `~/Library/Application Support/vlcq/vlcq.sqlite3`, with its application directory mode 0700 and state files mode 0600. SQLite SHALL use WAL, transactions, serialized writes, bounded busy timeouts, and a versioned schema with explicit migrations. Corruption or migration failure SHALL preserve the original and report recovery guidance rather than replacing it. Persistence SHALL model media identity/progress, durable ordered queues and states, distinct current-playback and selected-entry identities, and—if retained—bounded compact observations.

#### Scenario: Migration failure
- **WHEN** a schema migration cannot complete safely
- **THEN** `vlcq` preserves the original database and stops with recovery instructions

#### Scenario: Controller crash
- **WHEN** the controller terminates unexpectedly
- **THEN** committed queue order, selected identity, current identity, and progress remain consistent and resumable

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
