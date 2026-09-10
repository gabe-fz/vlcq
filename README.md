# vlcq

`vlcq` is a macOS Textual wrapper around a deterministic, persistent queue for a
dedicated VLC 3 process. It browses a root-confined library without modifying media,
keeps VLC's ephemeral playlist to the current item plus at most one authorized
successor, and stores private queue and playback state in SQLite.

## Install and run

Requires Python 3.12+, FFmpeg `ffprobe`, and VLC 3 at `/Applications/VLC.app` or as `vlc` on `PATH`.
Install FFmpeg on macOS with `brew install ffmpeg`; `vlcq doctor` reports whether the
local inspector is ready.

```sh
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
.venv/bin/vlcq
```

A commandless run reopens the last library (or presents the macOS folder chooser on
first use). Other entry points are:

```sh
vlcq play ~/Videos/Show
vlcq play ~/Videos/Show/e01.mkv ~/Videos/Show/e02.mkv
vlcq add ~/Videos/Show/e03.mkv
vlcq resume
vlcq doctor
vlcq inspect-subtitles --root ~/Videos ~/Videos/Show/e01.mkv --json
vlcq progress --root ~/Videos --json
```

Opening a folder establishes the library root but does not enqueue its contents.
Explicit files must remain under the active canonical root.

## Two-pane workflow

The main screen contains only full-width **Files** above **Queue**. Both start expanded
and split available list space equally. Each can be collapsed independently without
losing tree expansion, selection, highlight, or scroll position; if both are collapsed,
their one-line headers and Queue application menu remain reachable.

The Files header provides **Open**, **Search/filter**, **Add**, and an overflow menu. The
Queue header provides removal, reorder, clear actions, and an overflow menu. Right-click
opens row-specific actions; `Shift+F10` is the keyboard menu fallback. Every supported
Files and Queue video has an always-visible indented **Subtitles** subitem. Left-click,
right-click, or focus it and press `Shift+F10` to inspect/select subtitles; the video's
general menu does not duplicate that action. Inactive inspection is bounded and does not
load, enqueue, or play media. Queue overflow always provides **Reconnect**,
**Help**, **Quit**, **Remember subtitles by show**, and **Prefer English subtitles**, even
with an empty queue or no selected row. Queue **Details** is available on demand and is
not a persistent player panel.

Files are shown in one lazy inline tree. Multiple branches can remain expanded. Search
is case-insensitive, recursive, asynchronous, and root-confined. **All**, **In progress**,
and **Not watched** use historical coverage. Explicit selections survive filtering and
collapsing; the Files header reports selected and hidden-selected counts. Enter/Play
always targets the highlighted item rather than an unrelated selected batch.

Folder rows remain one line; video rows reserve a second indented subtitle line. Filenames
remain literal while folders, stem text, bracketed spans, numbers, punctuation, and
extensions use semantic colors. Compact markers convey selection, queued membership, and
the current item without relying on color. Subtitle markers distinguish `● Active`,
`★ Planned`, and `○` unresolved/default state. Missing and
failed queue entries retain `!`/`×` indicators. Ordinary queued, playing, paused,
stopped, skipped, completed, and watched labels are intentionally not repeated in rows;
queue outcomes remain available in Details.

### Current view versus historical coverage

Files rows show the filename and `hist N%` only—never a progress bar. Queue rows show:

```text
◆ episode.mkv  [████░░░░] 40% 12:00/30:00  hist 85%
```

The Queue bar, percentage, and time are the current/latest viewing position. For the
active item they use matching live VLC state; inactive rows use the last trustworthy
resume position. New entries begin at zero. Legacy-only rows with no trustworthy resume
position show unknown rather than substituting their old maximum. **Start over** writes a
zero resume position immediately but never clears coverage or legacy history.

`hist` is the union of unique confirmed played ranges across all viewings, not the
furthest reached position and not elapsed wall time. Replaying an overlap adds no credit,
and disjoint playback never fills the gap. Percentages use floor rounding, so incomplete
99.x% evidence never displays as 100%. `—` means coverage is absent/uninitialized;
initialized tracking with known duration but no qualified ranges displays 0%. Unknown or
nonpositive duration produces an unknown percentage.

Coverage requires at least five seconds of coherent, advancing `playing` observations
for the same validated media/generation/playlist identity at a finite positive VLC rate.
Request timing, a five-second maximum observation gap, and one media-second of VLC 3
integer-timestamp tolerance bound accepted intervals. Seeks, rate changes, stalls,
pauses, previews shorter than five seconds, stale or slow polls, reconnects, failures,
scrubbing, and item transitions reset pending evidence. Ambiguous evidence is
intentionally undercounted. End inference and Next never fill an unseen tail or force
100%; only actually qualified ranges are stored. This measures conservative observed
playback, not human attention and not every tiny native seek.

The watched threshold defaults to 90% unique coverage and can be changed with a whole
`VLCQ_WATCHED_PERCENT=1..100`. Invalid values fail before queue/history mutation. The
same raw threshold drives replay choices and Files filters. Reaching it does not stop,
advance, or rewrite queue outcome state. Explicit completed queue outcomes remain
eligible for finished-entry navigation and Clear completed independently.

## Playback controls

Visible pause, seek, Previous, and Next controls belong to the owned VLC window. `vlcq`
keeps its keyboard transport shortcuts and contextual **Play**, **Resume**, and **Start
over** actions, but has no toolbar, persistent status/player pane, transport menu entries,
or clickable seek track. Queue progress is read-only.

VLC's native **Next** is supported because vlcq stages and validates one authorized
successor. Native Previous and arbitrary VLC playlist navigation are not supported;
unexpected media fails closed instead of inheriting history. Reconnect creates no
duplicate process/poll loop and never selects, loads, or autoplays media.

### Subtitle selection and preferences

The always-visible **Subtitles** subitem summarizes the confirmed active choice, a planned
show/policy choice, or an unresolved/default state. Its picker lists `Off`, embedded streams
discovered by local `ffprobe`, and immediate same-stem sidecars (`.srt`, `.ass`, `.ssa`,
`.vtt`, `.sub/.idx`, `.sup`). Sidecars such as
`Episode.en.whisper.srt` are associated only with the sibling video; hidden, unrelated,
missing, non-regular, and root-escaping files are ignored. Choices made before playback
are whole-show preferences when remembering is enabled and planned choices are marked
separately from VLC-confirmed active choices. Track IDs are used only for that immediate
command; remembered choices store language, source, sidecar variant, and recognized
full-dialogue, signs/songs, forced, and SDH characteristics so IDs, paths, and per-episode
titles can change. The supported VLC 3 macOS
status response does not identify its initially selected subtitle stream, so the picker
truthfully marks **VLC current/default · exact track not reported** as active until vlcq
successfully applies a generation-local track or Off choice. VLC payload variants that
do report active flags are validated after selection. If **Remember subtitles by show**
is enabled, a successful vlcq choice (including **Off**) for a confidently inferred
show wins over all automation. Otherwise, enabled **Prefer English subtitles**
tries confidently identified English full-dialogue first, then other English tracks;
within a class it prefers non-forced and non-SDH tracks. If metadata is missing or no
English track is identifiable, VLC's existing/default choice is preserved.

Show inference is deliberately conservative: season folders or explicit `S01E02`,
`1x02`, and `Episode 02` filename evidence are accepted, while generic names, unsafe
paths, and ambiguous root-level files are not. Preferences are scoped by a hash of the
canonical library root and show identity; absolute media paths are not stored in the
subtitle preference table. Offline inspection uses only the canonical media URI, a shell-free bounded `ffprobe`
subprocess, and no subtitle-content reads. Discovery and selection use only the authenticated
127.0.0.1 VLC HTTP endpoint. Delayed tracks, stale menus, rejected commands, and
reconnects fail with bounded notices while playback continues.

Relevant connection transitions, errors, and action feedback use a dismissible one-line
notice that reserves no space while hidden. Its complete last text is available from
Queue overflow. Healthy/idle state and duplicate active-file metadata are not displayed
below the lists.

## Keys

| Key | Action |
| --- | --- |
| `o` | Open/change library root |
| Up/Down | Move highlight |
| Right / Enter | Expand folder or play highlighted video |
| Left / Backspace | Collapse/go to parent; Left seeks backward outside Files |
| `v` | Toggle explicit video selection |
| `a` / `A` | Add / add and play highlighted or explicitly selected videos |
| `Space` | Play/pause |
| `n` / `p` | Next/previous |
| `[` / `]` | Seek backward/forward 10 seconds |
| `d` / Delete | Remove queue entry only |
| `J` / `K` | Move queue entry down/up |
| `r` | Retry/reconnect as applicable |
| `c` | Confirm and clear watched/completed entries |
| `Shift+F10` | Open contextual actions |
| `?` | Help |
| `q` | Quit after choosing whether to stop or keep owned VLC |

Text inputs own ordinary typing, so shortcut letters do not trigger application actions.
Clear operations require confirmation and never delete media. One in-memory undo follows
a successful removal/clear until another queue mutation, advancement, root change, or
shutdown.

## Progress export compatibility

`vlcq progress --root PATH --json` remains a version-1, canonical root-relative document.
Existing fields retain their historical meanings:

- `positionMs`: legacy furthest position
- `durationMs`: stored known duration
- `watchedPercent`: legacy furthest-position percentage
- `completionObserved`: legacy completion/threshold classification
- `observedAt`: legacy observation timestamp

The command adds separately named `coverageMs`, `coveragePercent`, and
`coverageWatched`. Uninitialized coverage is `null`; initialized coverage without a
qualified range is zero milliseconds. Unknown duration makes coverage percentage and
classification `null`. Legacy classification can therefore differ from the coverage-based
UI and policy. Consumers should use this command rather than SQLite internals.
Replaced, missing, symlink-escaped, and out-of-root identities are not exported.

## Finder handoff

The Finder/Automator boundary is:

```sh
vlcq finder-handoff "$@"
```

A folder intentionally changes root. Explicit files stay under an existing active root;
mixed folder/file and unsafe selections are rejected.

## Storage, privacy, migration, and rollback

State defaults to `~/Library/Application Support/vlcq/vlcq.sqlite3`; its directory is
mode 0700 and database/WAL files are mode 0600. Coverage ranges are compact merged
half-open millisecond intervals tied to the existing canonical file fingerprint. No raw
poll event log, attention tracking, telemetry, remote request, VLC credential, raw status
payload, or unrelated media path is recorded.

Schema version 4 adds subtitle toggles and a bounded semantic show-preference table;
the English preference defaults on and show remembering defaults off. Version 1/2/3
queue order, current/selected identities, resume position, legacy maximum, completion,
fingerprints, timestamps, and coverage ranges are retained. Migration deliberately
infers no coverage from old maximum/completion evidence. A migration failure rolls back
and reports recovery guidance without replacing the database.

Before upgrading an important database, close vlcq and take a private SQLite backup that
includes WAL state:

```sh
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
source = Path.home() / "Library/Application Support/vlcq/vlcq.sqlite3"
backup = source.with_name("vlcq.sqlite3.before-v4")
with sqlite3.connect(source) as src, sqlite3.connect(backup) as dst:
    src.backup(dst)
PY
```

Older binaries reject schema 4. Rollback means closing vlcq and restoring the backup—not
decrementing `PRAGMA user_version` or deleting tables. Progress after the backup will be
lost.

VLC is launched with its visible macOS interface plus an authenticated loopback-only HTTP
interface and with repeat/loop/random disabled. `vlcq` performs no non-loopback network
requests and never deletes, moves, copies, or edits media.

## Development and verification

```sh
.venv/bin/pytest
.venv/bin/ruff check .
.venv/bin/mypy src
openspec validate browse-subtitles-before-playback --strict
VLCQ_REAL_VLC=1 .venv/bin/pytest tests/test_integration_real_vlc.py
```

The normal suite uses mock VLC and Textual's headless Pilot. Real-VLC tests generate only
temporary media and state, verify VLC 3 timing/rate assumptions, subtitle discovery/attachment,
and native transitions,
and cleanly stop the owned process. An environment-dependent skip is not release evidence.
