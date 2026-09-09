## MODIFIED Requirements

### Requirement: Resume and progress export
`vlcq resume` SHALL restore the persisted current queue entry when it remains unfinished, or otherwise the first unfinished entry, and offer to resume that target at its last trustworthy persisted playback position, with Start over and Cancel alternatives. Explicit playback of a different partially played item SHALL offer the same choice; activating the already playing or paused item SHALL not reload it. Start over SHALL reset current-view/resume position without clearing historical coverage or legacy maximum progress. Internal watched classification SHALL require confirmed unique played coverage reaching the configured percentage of a known positive duration; the threshold SHALL default to 90 percent and SHALL accept a validated user override from 1 through 100. A furthest position or natural-end event alone SHALL NOT establish coverage-based watched classification. Classification SHALL NOT by itself stop playback, advance the queue, or rewrite queue outcome state. Existing completed queue outcomes SHALL remain eligible for finished-entry navigation and Clear completed independently of coverage. `vlcq progress --root <path> --json` SHALL retain its version-1 document and existing legacy field meanings, keyed by validated root-relative paths with position, duration, watched percentage, completion status, and observation time. It SHALL add `coverageMs`, `coveragePercent`, and `coverageWatched`; uninitialized coverage SHALL use null, and unknown duration SHALL produce null coverage percentage and classification. Initialized coverage without qualified playback SHALL have zero covered milliseconds. Legacy fields SHALL NOT be silently reinterpreted as coverage, and documentation SHALL explain their possible divergence from the UI. It MUST NOT include records outside the canonical requested root, and consumers SHALL use this command rather than depend on the SQLite schema.

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
- **WHEN** the user explicitly plays media classified as watched by coverage that is not already active
- **THEN** it starts from zero without clearing coverage or legacy maximum history

#### Scenario: Reach the watched threshold
- **WHEN** confirmed unique played coverage reaches 90 percent with the default configuration
- **THEN** coverage classification and export coverage fields identify it as watched without stopping playback, advancing the queue, or changing queue outcome state

#### Scenario: Override the watched threshold
- **WHEN** the user configures a valid threshold other than 90 percent
- **THEN** coverage classification, filtering, replay choice, and export coverage fields consistently use that threshold

#### Scenario: Reject an invalid watched threshold
- **WHEN** the configured threshold is not a whole percentage from 1 through 100
- **THEN** startup fails clearly without mutating queue, history, or media state

### Requirement: Main TUI presentation
The main screen SHALL present only full-width Files above Queue with independently collapsible sections; it SHALL NOT contain a persistent player/status pane or visible pause, previous, next, or seek controls. Both sections SHALL start expanded and share available list space equally; collapsing one SHALL give its space to the other, and collapsing both SHALL leave both headers and application actions reachable. Each section header SHALL remain one terminal line and expose a disclosure control, a compact action bar of frequent actions, and an ellipsis overflow-menu trigger. Files SHALL expose Open, Search/filter, and Add; Queue SHALL expose compact removal, reorder, and clear actions. Application Help, reconnect, and Quit SHALL be available on demand from Queue overflow even without a row selection. Contextual Play, Resume, and Start over SHALL remain available for applicable items. The Files header SHALL identify the active root and selection/filter state, and the Queue header SHALL show the entry count. The Files pane SHALL show the library as one inline tree whose folders expand and collapse without replacing the surrounding hierarchy. Expanded sections SHALL use bounded non-wrapping items. Queue rows SHALL show filename, a current-view position bar and percentage, current/total time, and a separately labeled historical coverage percentage. Files rows SHALL show filename and historical coverage percentage without a bar or current-time summary, alongside necessary tree, selection, and membership markers. Folders, files, and syntactic filename components including the stem and extension SHALL have distinct semantic colors while preserving the literal filename; progress-state colors SHALL be confined to progress bars and status indicators rather than recoloring the filename as watched. Selection, focus, queued membership, and the current playback item SHALL retain compact non-color markers. Missing/failed entries SHALL retain actionable error indicators. Rows SHALL NOT repeat queued, playing, paused, stopped, skipped, watched, or completed labels. Queue highlight SHALL remain independent from the current-item marker and historical percentage; internal queue outcomes SHALL remain available through Details rather than row badges. Long text SHALL truncate without wrapping or hiding section controls; full paths and stored history SHALL be available through Details. Populated panes SHALL NOT contain instructional paragraphs. Empty panes SHALL provide one short next-action hint. Connection and error notices SHALL appear only when relevant, with no persistent healthy/idle summary or duplicate active-file metadata outside its queue row.

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
- **WHEN** the user invokes Open, Search/filter, Add, Remove, reorder, or Clear from its declared action bar
- **THEN** the action uses the same validation and targeting semantics as its keyboard or overflow-menu equivalent

#### Scenario: Pending and stopped presentation
- **WHEN** the queue contains pending entries and one stopped current entry
- **THEN** rows omit status badges and only the persisted current entry has the compact current-item marker

#### Scenario: No player panel
- **WHEN** the main screen is idle, playing, or paused
- **THEN** only Files and Queue remain as persistent content panes, with no player toolbar, transport menu entries, live seek surface, or permanent status summary

### Requirement: TUI controls and non-destructive actions
The TUI SHALL support opening/changing root (`o`), tree navigation and folder expansion (arrows, Enter, and Backspace as context permits), opening or playing (`Enter`), toggling selection (`v`), add (`a`), add-and-play (`A`), play/pause (`Space`), queue-only removal (`d`/Delete), reorder (`J`/`K`), next (`n`), previous (`p`), seek (Left/`[`/`]` as context permits), retry (`r`), clear completed (`c`), help (`?`), and quit (`q`) with an explicit VLC-process choice. Right-click menus SHALL provide row-contextual actions; compact visible action bars SHALL provide frequent library and queue management actions; ellipsis triggers and a keyboard menu binding SHALL expose overflow actions. Library-selection actions SHALL target explicit library selections or the highlighted library video; single-item playback SHALL target the clicked or highlighted item independently of a batch. Queue-item actions SHALL target only their declared queue entry. Collectively the action bars and menus SHALL provide Add to end, Play next, Play now, Resume, Start over, reconnect, removal, reordering, undo, search, filters, Details, Help, and Quit in the appropriate context. Ordinary transport SHALL use existing keyboard shortcuts or the owned VLC window, not visible vlcq transport buttons or menu entries. Both Files and Queue SHALL offer on-demand item Details. Resume and Start over SHALL be directly available for applicable highlighted items as well as through the playback-choice dialog. Unavailable visible actions SHALL be disabled and unavailable menu actions SHALL be disabled or omitted. Clearing the full queue or completed entries SHALL require confirmation. No queue control SHALL delete or modify an underlying media file. Text-entry fields SHALL receive ordinary typing without triggering global playback or navigation shortcuts.

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
TUI input SHALL remain responsive during VLC startup, HTTP polling, root and tree-branch discovery, recursive search/filter discovery, path validation, and database writes. Potentially slow operations SHALL run asynchronously, stale branch results SHALL NOT attach to a replaced root, and expanding one branch SHALL NOT require eager traversal of unrelated collapsed branches. Resize events SHALL preserve section collapse state, tree expansion, selection, highlight, and scrolling and SHALL keep menus, section headers, on-demand application actions, and usable expanded list viewports accessible at 80-by-24 and larger terminal sizes. With both sections expanded at 80-by-24, each list SHALL expose at least five compact entry items when populated. Menus near terminal edges SHALL remain inside the viewport and support scrolling when needed. Recoverable failures SHALL appear in a dismissible one-line notice without closing the application, growing the layout without bound, or hiding the lists; complete error text SHALL remain available on demand. The notice SHALL consume no reserved space when absent and SHALL NOT become a persistent player/status pane.

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
Historical percentages in Files and Queue SHALL describe confirmed unique played coverage across viewings, not maximum position. Queue bars and current/total times SHALL instead describe the current/latest viewing's playback position: matching live state for the active item and last trustworthy resume state for inactive items. Start over SHALL reset that position immediately without lowering coverage. New unstarted queue entries SHALL show zero current position; legacy-only entries without trustworthy current position SHALL show unknown rather than substituting maximum history. Unknown duration SHALL produce unknown total time and percentage, never a fabricated full bar. Absent or legacy-only coverage SHALL use a compact unknown marker; initialized known-duration tracking without qualified playback SHALL show 0%. Historical whole percentages SHALL round down and SHALL NOT show 100% for incomplete coverage. Historical labels SHALL distinguish coverage from the current bar without watched/completed badges or a second bar. Items SHALL expose already-queued membership. Details SHALL show current/resume position, coverage, known duration, threshold/classification, last trustworthy playback time, and explicitly labeled legacy maximum/completion when present. Displaying history SHALL neither create records, rewrite evidence, nor change timestamps and SHALL match the current canonical file fingerprint. Routine refresh SHALL use bounded read-only retrieval and asynchronous identity validation, preserving unchanged row identity, highlight, expansion, and scrolling.

#### Scenario: Browse an unplayed file
- **WHEN** a valid video has no matching playback history
- **THEN** its Files row has only a compact unknown historical marker alongside its filename/selection metadata, Details identifies absent history, and browsing does not create history

#### Scenario: Display partial progress
- **WHEN** a file has 45 minutes of confirmed unique played ranges against a known 60-minute duration
- **THEN** Files and Queue show historical 75%, Files has no progress bar, and the Queue bar independently reflects its current-view position

#### Scenario: Display watched progress
- **WHEN** a file reaches or exceeds the configured watched threshold
- **THEN** its historical percentage remains numeric without a watched badge and internal coverage classification is watched without changing the current-view bar

#### Scenario: Completed video is replaying
- **WHEN** a video with watched history is currently playing again
- **THEN** its Queue bar and time reflect the rewatch, its historical percentage remains unchanged until new ranges are played, and no Playing or Watched badge is added

#### Scenario: Replacement at an existing path
- **WHEN** a video's fingerprint differs from the stored media fingerprint
- **THEN** neither its history display nor its resume action inherits the previous file's progress

#### Scenario: Refresh history while identity checks are delayed
- **WHEN** filesystem identity validation is delayed while a user types, expands a branch, highlights an item, or scrolls
- **THEN** the interaction remains responsive and unchanged items retain their identity, highlight, expansion, and scroll position

### Requirement: Library discovery and selection visibility
Files SHALL provide on-demand case-insensitive filename search and All, In progress, and Not watched filters across the configured library tree through its action bar and overflow menu. Search/filter discovery SHALL recursively inspect root-confined folders asynchronously, show matching files with enough ancestor context to locate them in the tree, and SHALL NOT permanently expand unrelated branches. In progress SHALL mean positive confirmed coverage below the watched threshold; Not watched SHALL include files with unknown/legacy-only coverage and files below the configured coverage threshold. Directories SHALL remain available for tree navigation. Filtering SHALL NOT enqueue media or silently discard selections. When selections exist, a compact persistent header summary SHALL show the selected count and hidden selection count, including selections in collapsed or filtered branches, with a clear-selection overflow action. Active search or non-default filters SHALL be visibly indicated and clearable without a permanent filter toolbar. Action targeting SHALL distinguish highlighted-item playback from explicit batch queueing, and batch menu labels SHALL disclose the selected count and hidden count when nonzero.

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

### Requirement: Complete mouse-operated playback workflows
Single-clicking a video or queue item SHALL highlight it without changing playback; folder expansion and collapse SHALL remain available by mouse without replacing the surrounding Files tree. A queue highlight SHALL be persisted immediately by queue-entry identity, independently of the current playback identity, and SHALL be restored across routine refresh, reorder, collapse and expansion, and application restart while that entry remains in the active queue. If the persisted entry no longer belongs to the active queue, the stale selection SHALL be cleared safely. Browser video selection SHALL use compact inline checkbox hit targets rather than standalone button rows. Right-clicking an item SHALL highlight and open its context menu without expanding a folder, toggling selection, or starting playback. One-line visible action bars SHALL provide frequent mouse actions, while ellipsis triggers SHALL expose overflow actions when the terminal intercepts right-click. Context menus and compact controls SHALL permit tree navigation, queue insertion, playback choice, removal, reorder, clearing, undo, reconnect, Details, Help, and Quit. Visible pause/resume transport, previous/next, and seek controls SHALL be delegated to the owned VLC window; vlcq SHALL retain its keyboard transport shortcuts. Every dialog SHALL have compact clickable action and cancellation controls. Menus SHALL support keyboard navigation, activation, Escape cancellation, and focus restoration. The vlcq TUI SHALL have no clickable seek track. Queue current-view bars and historical values SHALL be read-only and SHALL NOT seek or change playback when clicked. Library and queue management, playback activation choices, and application actions SHALL remain mouse-accessible at 80-by-24; ordinary transport SHALL remain available through VLC controls or vlcq keyboard shortcuts. Native VLC Next SHALL retain its supported authoritative-queue behavior; this delegation SHALL NOT imply support for native Previous or arbitrary playlist navigation.

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
- **WHEN** the user clicks a Queue current-view bar or historical value
- **THEN** no seek, queue mutation, or playback command occurs

#### Scenario: Seek with unknown duration
- **WHEN** the active video's duration is unknown or VLC is disconnected
- **THEN** no clickable absolute-seek track is present, row progress remains read-only, and unavailable keyboard transport reports a recoverable notice

#### Scenario: Keyboard transport without a player pane
- **WHEN** the user invokes an existing play/pause, previous/next, or seek shortcut outside text input
- **THEN** the existing validated controller action runs without requiring player widgets and unavailable playback reports a recoverable notice

### Requirement: Readable stable playback UI and reconnect
Live playback progress SHALL be confined to the active Queue row, with no persistent bottom player or status surface. Highlighted-item Details SHALL open only on request and SHALL remain distinct from current playback identity. Routine polling SHALL update row progress while preserving tree and queue item identity, highlight, expansion, scroll position, menu target, and actionable controls. Relevant connection transitions and errors SHALL use a dismissible one-line notice with full error text available on demand; ordinary healthy/idle playback SHALL reserve no notice space. Help, reconnect, and Quit SHALL remain reachable through Queue overflow when the queue is empty or both sections are collapsed. Reconnect SHALL NOT restart media without an explicit playback action. Overlapping reconnect requests SHALL not create duplicate owned VLC processes or polling loops.

#### Scenario: Browse while playback updates
- **WHEN** the user highlights a different video, expands a folder, or scrolls while status polling runs
- **THEN** the active Queue row continues updating without adding a second filename summary and tree expansion, browser highlight, details target, and scroll position remain stable

#### Scenario: Use the thick live progress track
- **WHEN** matching active media has a known positive duration
- **THEN** its Queue bar, percentage, and current/total time update without changing row height or introducing a player pane

#### Scenario: Reconnect with an empty queue
- **WHEN** VLC is unavailable and the user invokes Reconnect with no selected queue item
- **THEN** connection recovery proceeds independently and does not enqueue or autoplay media

#### Scenario: Long playback failure
- **WHEN** playback fails with an error longer than the terminal width
- **THEN** the notice remains one line, complete error information is available on demand, and Files and Queue retain usable viewports

### Requirement: Conservative progress semantics
`vlcq` SHALL persist current/resume position independently from legacy maximum position and historical coverage. Confirmed coverage SHALL be the union of played ranges for the unchanged media identity; replaying an overlap SHALL NOT increase coverage for that overlap. Coverage SHALL require bounded, identity-matching observations of sustained playing with position advances consistent with elapsed time and validated playback rate. A run SHALL require at least five seconds of coherent advancing playback before earning coverage; an observation gap over five seconds SHALL break continuity. Integer-second reporting repeats SHALL earn no credit and SHALL be tolerated only within a bounded quantization window, not treated as proof of continued playback. Seeks, rapid previews/scrubbing, pauses, manual Next, unobserved gaps, stale responses, rate discontinuities, and wrong-media observations SHALL NOT credit jumped or unobserved ranges. Uncertain observations SHALL break continuity without erasing committed coverage. End-of-file inference or queue advancement SHALL NOT fill missing ranges or force 100%. Seeking near the end or manually stopping SHALL NOT prove completion. Queue completion SHALL use reliable natural-end evidence or conservative inference based on qualified continuous recent playback and the expected transition, separately from coverage classification. Legacy maximum for unchanged media MUST NOT decrease due to stale/zero observations, and legacy completion evidence SHALL remain separate from measured coverage.

#### Scenario: Stale observation
- **WHEN** a later observation reports a lower or zero position for the same unchanged file
- **THEN** persisted maximum progress does not decrease

#### Scenario: Seek near the end
- **WHEN** the user seeks near the duration and stops playback
- **THEN** resume/legacy position may reflect the destination but historical coverage excludes the jump and neither coverage-based watched status nor queue completion is awarded solely because of the seek or stop

#### Scenario: Play disjoint ranges and replay an overlap
- **WHEN** qualified playback covers minutes 0 through 10 and 20 through 30 of a 60-minute file and later replays minutes 5 through 10
- **THEN** coverage remains 20 minutes, the unplayed gap remains uncredited, and the displayed historical percentage is 33%

#### Scenario: Brief preview
- **WHEN** the user plays a file for less than five seconds before switching or seeking
- **THEN** that unqualified preview contributes no historical coverage

#### Scenario: Rapid navigation and scrubbing
- **WHEN** the user rapidly previews files, presses Next, or seeks repeatedly without establishing sustained coherent playback
- **THEN** historical coverage does not increase and no end position or queue outcome supplies missing credit

#### Scenario: Seek then genuinely play the tail
- **WHEN** the user seeks to 95% and subsequently establishes qualified playback through the final 5%
- **THEN** only confirmed played portions of that tail count, never the skipped first 95%, even if VLC ends naturally

#### Scenario: Polling is interrupted
- **WHEN** polling fails, a response is stale, playback rate changes, or an observation gap prevents trustworthy continuity
- **THEN** no interval bridges the discontinuity and later playback must establish fresh continuous evidence

#### Scenario: Coverage duration is unknown or corrected
- **WHEN** coverage exists but duration is unknown or a longer trustworthy duration becomes known
- **THEN** unknown duration yields an unknown percentage, corrected duration recomputes the percentage without deleting ranges, and incomplete coverage is never rounded up to 100%

## ADDED Requirements

### Requirement: Preserve legacy data without fabricating coverage
A coverage-storage upgrade SHALL preserve legacy maximum, completion, resume, timestamps, media identities, queue order, and current/selected identities. It SHALL NOT infer played ranges from legacy maximum or completion. New coverage SHALL remain tied to the validated canonical file fingerprint and SHALL NOT inherit from replacement or out-of-root content. Coverage tracking SHALL remain private and local, without raw per-poll event archives or human-attention monitoring. Migration failure SHALL preserve the original database transactionally and report recovery guidance.

#### Scenario: Upgrade a legacy complete file
- **WHEN** an existing file has legacy 100% or explicit completion but no confirmed played ranges
- **THEN** historical coverage is unknown, Details/export preserve and label legacy evidence, and subsequent qualified playback starts accumulating new coverage without assuming the rest was watched

#### Scenario: Failed migration
- **WHEN** a coverage migration fails partway through
- **THEN** existing schema/data remain intact, startup reports recovery guidance, and neither the database nor media files are replaced

#### Scenario: Replaced or unsafe media
- **WHEN** a path changes fingerprint or resolves outside the active canonical root
- **THEN** no old coverage is attached to the replacement or unsafe item and validation does not modify media

#### Scenario: Export legacy and coverage values
- **WHEN** a file with legacy maximum 95% has only 5% confirmed played coverage
- **THEN** version-1 legacy fields retain their meanings, separately named coverage fields report measured coverage, and all emitted keys remain validated root-relative paths
