## MODIFIED Requirements

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
