## MODIFIED Requirements

### Requirement: Main TUI presentation
The main screen SHALL present full-width Files above Queue, with independently collapsible sections and compact status at the bottom. Both sections SHALL start expanded and share available list space equally; collapsing one SHALL give its space to the other, and collapsing both SHALL leave both headers and bottom status reachable. Each header SHALL remain one terminal line and expose a disclosure control and a compact actions-menu trigger. The Files header SHALL identify the active root and current root-relative folder; the Queue header SHALL show the entry count. Expanded sections SHALL use single-line rows with filenames, selection markers, and compact metadata attached to the same row. Folders, videos, selected items, focused sections, the current playback item, and skipped, completed, failed, and missing outcomes SHALL have distinct semantic color and non-color indicators. Pending queue position SHALL be implicit rather than repeated as a `QUEUED` badge; `STOPPED` SHALL appear only on the persisted current playback item. Queue selection, current playback state, item outcome, and read-only playback history SHALL remain visually distinguishable. Filenames SHALL be rendered literally, including markup-like characters. Long text SHALL truncate without wrapping or hiding section controls; full paths and stored history SHALL be available through Details. Populated panes SHALL NOT contain instructional paragraphs or persistent action toolbars. Empty panes SHALL provide one short next-action hint. Bottom status SHALL show active playback and connection state without duplicating detailed history elsewhere on the main screen.

#### Scenario: Empty queue and browser
- **WHEN** either expanded section has no displayable entries
- **THEN** it shows one short next-action hint and its actions menu remains available

#### Scenario: Collapse and restore a section
- **WHEN** the user collapses Files or Queue and later expands it
- **THEN** its header remains reachable, the other expanded list uses the freed space, and folder, selection, highlight, and scroll position are preserved

#### Scenario: Literal long filename
- **WHEN** a filename contains bracketed release tags or exceeds the available row width
- **THEN** it remains literal semantically colored text on one line, controls stay reachable, and Details exposes the complete name and path

#### Scenario: Pending and stopped presentation
- **WHEN** the queue contains pending entries and one stopped current entry
- **THEN** pending rows omit a redundant `QUEUED` badge and only the current entry can display `STOPPED`

### Requirement: Complete mouse-operated playback workflows
Single-clicking a video or queue row SHALL highlight it without changing playback; folder activation SHALL remain available by mouse. A queue highlight SHALL be persisted immediately by queue-entry identity, independently of the current playback identity, and SHALL be restored across routine refresh, reorder, collapse and expansion, and application restart while that entry remains in the active queue. If the persisted entry no longer belongs to the active queue, the stale selection SHALL be cleared safely. Browser video selection SHALL use compact inline checkbox hit targets rather than standalone button rows. Right-clicking a row SHALL highlight and open its context menu without opening a folder, toggling selection, or starting playback. Small visible menu triggers SHALL provide a mouse fallback when the terminal intercepts right-click. Context menus and compact controls SHALL permit folder navigation, queue insertion, playback choice, pause/resume, previous/next, relative seek, removal, reorder, clearing, undo, reconnect, details, help, and quit. Every dialog SHALL have compact clickable action and cancellation controls. Menus SHALL support keyboard navigation, activation, Escape cancellation, and focus restoration. Absolute seek by clicking the progress track SHALL be available only with a connected matching active item and known positive duration, and SHALL invalidate natural-end inference from before the seek. Essential actions SHALL remain reachable at an 80-by-24 terminal size without requiring keyboard shortcuts.

#### Scenario: Complete a mouse-only workflow
- **WHEN** the user opens a folder, selects videos, adds them, resumes playback, reorders and removes entries, confirms a clear, undoes it, and quits using menus and mouse controls
- **THEN** each step is reachable without typing a keyboard shortcut, including modal confirmation and cancellation

#### Scenario: Click an inactive row
- **WHEN** the user single-clicks a different video or queue row
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
- **WHEN** the user right-clicks a folder row
- **THEN** a folder context menu opens without entering that folder or mutating the queue

#### Scenario: Terminal intercepts right-click
- **WHEN** terminal behavior prevents the application receiving a right-click
- **THEN** the section actions trigger and keyboard menu binding expose the same applicable actions for the highlighted item

#### Scenario: Seek with unknown duration
- **WHEN** the active video's duration is unknown or VLC is disconnected
- **THEN** clicking the progress track sends no absolute-seek command and the unavailable action is explained

### Requirement: Authoritative queue semantics
`vlcq`, not VLC, SHALL own queue order and SHALL send VLC one active item at a time. Only explicitly selected videos SHALL enter the queue. The persisted queue highlight SHALL be independent of the persisted current playback item. At most one entry SHALL have a transient playback state of playing, paused, or stopped, and that entry MUST be the persisted current playback item. Changing the current item SHALL return every prior non-terminal transient entry to pending; completed, skipped, failed, and missing outcomes and all playback history SHALL remain unchanged. Opening an existing queue SHALL transactionally repair stale transient states and invalid current or selected identities without deleting queue entries, changing order, modifying history, or accessing media destructively. Natural completion SHALL persist final progress and start the next item. Manual next SHALL mark the current entry skipped unless completion was independently established. Activating a highlighted item for playback SHALL update the current playback identity without conflating it with highlight persistence, and reordering SHALL persist immediately. Missing files SHALL remain visible as missing and be skipped with a warning. Queue state SHALL survive controller crashes, and only one controller SHALL mutate the database at a time.

#### Scenario: Natural completion
- **WHEN** `vlcq` conservatively observes the active item ending naturally
- **THEN** it records completion and starts the next queued item

#### Scenario: Manual skip
- **WHEN** the user advances before independently observed completion
- **THEN** the current entry becomes skipped rather than completed

#### Scenario: Switch away from a stopped item
- **WHEN** a stopped current item exists and playback is activated on another queue entry
- **THEN** the prior item becomes pending and only the new current item may acquire a transient playback state

#### Scenario: Repair multiple stopped entries
- **WHEN** an existing queue contains multiple stopped entries but only one persisted current identity
- **THEN** opening the queue retains stopped only for that current entry, converts other stale transient states to pending, and preserves order, outcomes, and playback history

#### Scenario: Repair an invalid current identity
- **WHEN** persisted current or selected identities do not belong to the active queue
- **THEN** the invalid identities are cleared and stale transient entries become pending without autoplay or media-file modification

### Requirement: Durable private persistence
By default `vlcq` SHALL store data at `~/Library/Application Support/vlcq/vlcq.sqlite3`, with its application directory mode 0700 and state files mode 0600. SQLite SHALL use WAL, transactions, serialized writes, bounded busy timeouts, and a versioned schema with explicit migrations. Corruption or migration failure SHALL preserve the original and report recovery guidance rather than replacing it. Persistence SHALL model media identity/progress, durable ordered queues and states, distinct current-playback and selected-entry identities, and—if retained—bounded compact observations.

#### Scenario: Migration failure
- **WHEN** a schema migration cannot complete safely
- **THEN** `vlcq` preserves the original database and stops with recovery instructions

#### Scenario: Controller crash
- **WHEN** the controller terminates unexpectedly
- **THEN** committed queue order, selected identity, current identity, and progress remain consistent and resumable
