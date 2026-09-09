## MODIFIED Requirements

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

### Requirement: Playback observation
While the controller runs, `vlcq` SHALL poll VLC status for the current local media URI, stable VLC playlist identity, state, elapsed time, duration, and position. Parsing SHALL tolerate missing or changed fields and fail closed for malformed or non-local media URIs. Polling SHALL distinguish observations of the active item, the staged successor, and unexpected media before changing queue state or history. Polling MAY be frequent while playing, slower while paused, and use bounded backoff while stopped or unavailable. Progress SHALL flush promptly on pause, stop, item change, skip, shutdown, and observed completion.

#### Scenario: Malformed observed URI
- **WHEN** VLC reports malformed, remote, or otherwise non-local media
- **THEN** `vlcq` refuses to associate that observation with local progress or automatic advancement

#### Scenario: Direct transition without stopped observation
- **WHEN** VLC changes directly from the active item to the staged successor between polls
- **THEN** `vlcq` captures the last trustworthy active-item observation, classifies the transition conservatively, and adopts the already-playing successor without replaying it
