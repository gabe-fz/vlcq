## Why

Queue rows repeat playback/outcome labels and display lifetime maximum position even during a fresh rewatch. Seeking currently increases that maximum, so it cannot answer how much of a video actually played.

## What Changes

- Queue rows show filename, current-view position bar and percentage, current/total time, and historical watched-coverage percentage. Files show only filename and historical percentage, alongside necessary selection/tree/membership markers.
- Remove the status/player pane and visible transport buttons, seek track, and transport menu entries. Keep Files + Queue with compact library/queue actions, existing keyboard transport shortcuts, contextual Play/Resume/Start over, and on-demand Help/Details/reconnect.
- Use the owned VLC window for visible playback controls. Preserve native Next support without promising native Previous or arbitrary VLC playlist navigation.
- Remove repeated watched, completed, skipped, playing, paused, and stopped row labels; retain one compact current-item marker and actionable missing/failed indicators. Show connection/error notices only when needed, never an always-present idle/status panel.
- Measure historical coverage as the union of confirmed played time ranges across viewings. Seeks, rapid scrubbing, Next, and end inference never fill unwatched gaps; repeated playback of the same range adds no duplicate credit.
- Keep current/resume position independent from coverage; Start over resets current position without clearing coverage.
- **BREAKING behavioral change:** internal watched classification and related replay/filter policies use coverage rather than furthest position or an end event. Existing queue outcomes remain internal navigation state, not proof of coverage.
- Preserve legacy maximum, completion, resume, and queue data without converting them to coverage. Keep version-1 export fields backward compatible and add explicitly named coverage fields; document that legacy export classification is not the new UI classification.

## Capabilities

### New Capabilities

None; extend the existing capability.

### Modified Capabilities

- `vlcq`: simplify row presentation, distinguish current position from historical coverage, require continuous-playback evidence, preserve legacy storage/export compatibility, and update coverage-based watched policy.

## Impact

- Affects `controller.py`, `database.py`, `models.py`, `progress.py`, `queue.py`, `tui.py`, CLI export, associated tests, and README. VLC status parsing may require timing/rate evidence.
- Add an explicit transactional SQLite migration for compact merged coverage ranges; retain existing fields rather than destructively simplifying the schema. No new service or dependency is intended.
- Existing tests/specs expecting a bottom player, visible transport controls, historical queue bars, state badges, seek-earned history classification, and furthest-based watched policy need revision. The simplified two-pane contract supersedes conflicting historical player/header presentation requirements without removing queue-management operations.
- Security/privacy: coverage remains private local SQLite data, tied to validated media identity. No attention tracking, telemetry, remote calls, raw status logging, or new credential exposure.
- Media safety: browsing remains read-only; migration failures preserve the database; no media files are modified or deleted.
- Non-goals: prove human attention, reconstruct old viewing coverage, delete legacy state, remove keyboard transport or queue functions, implement a replacement player, expand native VLC playlist navigation, add cloud sync, or track an unbounded per-poll/session event log.
