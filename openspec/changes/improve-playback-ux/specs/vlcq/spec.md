## MODIFIED Requirements

### Requirement: Resume and progress export
`vlcq resume` SHALL restore the most recent unfinished queue and offer to resume its current item at its last trustworthy persisted playback position, with Start over and Cancel alternatives. Explicit playback of a different partially played item SHALL offer the same choice; activating the already playing or paused item SHALL not reload it. Start over SHALL preserve historical maximum progress and completion. `vlcq progress --root <path> --json` SHALL retain its version-1 document and existing field meanings, keyed by validated root-relative paths with position, duration, watched percentage, completion status, and observation time. It MUST NOT include records outside the canonical requested root, and consumers SHALL use this command rather than depend on the SQLite schema.

#### Scenario: Export a bounded root
- **WHEN** a consumer requests JSON progress for a canonical root
- **THEN** every emitted record is strictly beneath that root and uses a root-relative key

#### Scenario: Resume after rewinding
- **WHEN** the user reached 40 minutes, deliberately rewound to 20 minutes, and later requests resume
- **THEN** Resume uses the last trustworthy playback position near 20 minutes while exported maximum progress remains at least 40 minutes

#### Scenario: Cancel explicit playback
- **WHEN** the user cancels a Resume / Start over choice
- **THEN** current playback and queue contents and order remain unchanged

#### Scenario: Replay completed media
- **WHEN** the user explicitly plays completed media that is not already active
- **THEN** it starts from zero without clearing completion history

### Requirement: TUI controls and non-destructive actions
The TUI SHALL support opening/changing root (`o`), browser navigation (arrows and Backspace), opening or playing (`Enter`), toggling selection (`v`), add (`a`), add-and-play (`A`), play/pause (`Space`), queue-only removal (`d`/Delete), reorder (`J`/`K`), next (`n`), previous (`p`), seek (Left/`[`/`]` as context permits), retry (`r`), clear completed (`c`), help (`?`), and quit (`q`) with an explicit VLC-process choice. Library-selection actions SHALL appear in the library pane; queue-item actions SHALL appear in the queue pane. Visible controls SHALL provide Add to end, Play next, Play now, Resume, Start over, transport, reconnect, removal, reordering, and undo when applicable. Clearing the full queue or completed entries SHALL require confirmation. No queue control SHALL delete or modify an underlying media file. Text-entry fields SHALL receive ordinary typing without triggering global playback or navigation shortcuts.

#### Scenario: Remove a queue item
- **WHEN** the user removes or clears an item from the queue
- **THEN** only queue state changes and the media file remains untouched

#### Scenario: Navigate upward at the root
- **WHEN** the user requests parent navigation while already at the library root
- **THEN** the browser remains at that root

#### Scenario: Type a filename search
- **WHEN** a text field has focus and the user types characters that are also application shortcuts
- **THEN** those characters edit the field without playing, queueing, seeking, or quitting

## ADDED Requirements

### Requirement: Read-only watch-history presentation
Library and queue rows SHALL distinguish No recorded progress, In progress, and Completed independently of queue state. Completion SHALL require recorded completion evidence; percentages SHALL describe furthest progress reached, not measured viewing coverage. Rows SHALL expose known progress and already-queued membership, and selected-item details SHALL show resume position, furthest progress, known duration, and last trustworthy playback time. Missing history and duration SHALL be shown as unknown rather than fabricated zero-time evidence. Displaying history SHALL neither create media records nor change observation timestamps and SHALL match the current canonical file fingerprint.

#### Scenario: Browse an unplayed file
- **WHEN** a valid video has no matching playback history
- **THEN** its row shows No recorded progress and no last-played date, and browsing does not create history

#### Scenario: Completed video is replaying
- **WHEN** a video with recorded completion is currently playing again
- **THEN** the UI preserves Completed history while independently showing Playing queue state and live position

#### Scenario: Replacement at an existing path
- **WHEN** a video's fingerprint differs from the stored media fingerprint
- **THEN** neither its history display nor its resume action inherits the previous file's progress

### Requirement: Library discovery and selection visibility
The library SHALL provide case-insensitive filename search and All, In progress, and Not completed filters within the current folder. In progress SHALL mean positive recorded progress without completion; Not completed SHALL include files without history. Directories SHALL remain available for navigation. Filtering SHALL NOT enqueue media or silently discard selections. A persistent summary SHALL show selected count and selections hidden by filtering or located in other folders, with a clear-selection action. Action targeting SHALL distinguish highlighted-item playback from explicit batch queueing.

#### Scenario: Selection is outside the visible listing
- **WHEN** filtering or folder navigation hides selected videos
- **THEN** the summary discloses the hidden selections and batch actions continue to target them until cleared or consumed

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
Single-clicking a row SHALL highlight it without changing playback. Browser video selection SHALL use clickable checkbox controls. Visible mouse controls SHALL permit folder navigation, queue insertion, playback choice, pause/resume, previous/next, relative seek, removal, reorder, clearing, undo, reconnect, help, and quit. Every dialog SHALL have clickable action and cancellation controls. Absolute seek by clicking the progress track SHALL be available only with a connected matching active item and known positive duration, and SHALL invalidate natural-end inference from before the seek. Essential actions SHALL remain reachable at an 80-by-24 terminal size without requiring keyboard shortcuts.

#### Scenario: Complete a mouse-only workflow
- **WHEN** the user opens a folder, selects videos, adds them, resumes playback, reorders and removes entries, confirms a clear, undoes it, and quits using the mouse
- **THEN** each step is reachable without typing a keyboard shortcut, including modal confirmation and cancellation

#### Scenario: Click an inactive row
- **WHEN** the user single-clicks a different library or queue row
- **THEN** only highlight and details change and current playback continues uninterrupted

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

### Requirement: Readable stable playback UI and reconnect
The player area SHALL show the active filename, connection state, elapsed and known total and remaining time using minutes/seconds or hours/minutes/seconds. Unknown duration and remaining time SHALL be explicitly unknown. Highlighted-item details SHALL remain distinct from the active player. Routine polling SHALL preserve row identity, highlight, scroll position, and actionable controls. Reconnect SHALL be available without a queue selection and SHALL not restart media without an explicit playback action. Overlapping reconnect requests SHALL not create duplicate owned VLC processes or polling loops.

#### Scenario: Browse while playback updates
- **WHEN** the user highlights a different video or scrolls while status polling runs
- **THEN** the active player still names the playing file and the browser highlight, details target, and scroll position remain stable

#### Scenario: Reconnect with an empty queue
- **WHEN** VLC is unavailable and the user clicks Reconnect with no selected queue item
- **THEN** connection recovery proceeds independently and does not enqueue or autoplay media
