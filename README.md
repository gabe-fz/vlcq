# vlcq

`vlcq` is a folder-first Textual interface for a deterministic, persistent VLC 3
video queue on macOS. It browses a lazily expandable library tree without automatically
enqueueing its contents, and keeps VLC's ephemeral playlist bounded to the active item
plus at most one `vlcq`-authorized successor. `vlcq` remains authoritative for queue
order and records monotonic playback history plus a separate trustworthy resume position
in SQLite.

## Install

Requires Python 3.12+ and VLC 3 installed at `/Applications/VLC.app` or available
as `vlc` on `PATH`.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/vlcq
```

Running `vlcq` reopens the last library, or displays the native macOS folder
chooser on first use. You can also open a specific root directly. The Files pane keeps
that root visible while folders expand/collapse in place; normal discovery is lazy and
search/filter discovery is recursive and asynchronous:

```sh
vlcq play ~/Videos/Show
```

Opening a folder only establishes the library root. Browse into subfolders,
select videos, then add them. Explicit file arguments establish their nearest
common parent as the root:

```sh
vlcq play ~/Videos/Show/e01.mkv ~/Videos/Show/e02.mkv
vlcq resume
vlcq progress --root ~/Videos --json
```

## TUI controls and playback choices

The main screen is a stacked **Files** section above a **Queue** section. Both start
expanded and share the available list space; click `▾`/`▸` to collapse or restore either
section. Each section’s compact root/count metadata appears immediately after its name,
followed by color-coded controls. Metadata is width-limited so it cannot push controls to
the right edge. Wider terminals progressively expose additional
pane-specific actions (file navigation/sort/selection, queue management, and VLC/system
controls) instead of leaving useful space empty. Unavailable contextual actions such as
**Undo**, **Clear selection**, or **Clear watched** stay out of the bar. The `…` control appears
only while useful actions remain hidden and never duplicates buttons already on screen.
Right-clicking a row
opens actions for that specific item,
and `Shift+F10` is the keyboard fallback. Menus remain inside the terminal and scroll when
needed.

Rows are one line: filenames remain literal, including bracketed release tags, while
folders, stems, bracketed spans, numeric runs, punctuation, and extensions use distinct
syntax colors. Checkbox targets select library videos. Files and Queue items with positive
history show a thick read-only bar and whole percentage; unknown durations show `?%`.
A queue-row highlight is a durable, identity-based Details selection and never starts
playback; the diamond marks the independent current playback item. Pending queue position
is implicit rather than a `QUEUED` badge. Only the current row may show **Playing**,
**Paused**, or **Stopped**; **Skipped**, **Completed**, **Missing**, and **Failed** remain
visible as outcomes. Unplayed rows omit a history badge. Click a folder to expand it.
Full paths, resume/furthest positions, durations, watched threshold, fingerprints, and
missing history are available through **Details**.

The Files header exposes **Open**, **Search/filter**, **Add**, and file actions; Queue exposes
**Remove**, **Move up/down**, **Clear**, and queue actions; the status pane exclusively owns
**Previous**, **Play/pause**, **Next**, and VLC/application actions. File item context menus
retain history **Details** where the player status cannot represent them; Queue omits Details
because active metadata is already in the status pane. Item resume/start-over actions remain
contextual, while section menus stay pane-specific. Search and
**All / In progress / Not watched** filters discover
the whole root without enqueueing results. The Files header keeps active search/filter and
selected/hidden counts visible. Selections remain explicit across filtering, collapsing,
and resizing. **Play now** always targets the highlighted item, while **Add to end**,
**Play next**, and **Add & play** use the selected batch when one exists.

Selecting an incomplete item with a trustworthy resume point opens **Resume**, **Start
over**, or **Cancel**. Legacy rows use **Resume from furthest recorded progress**; their
last-played time remains unknown. Start over preserves maximum history and completion.
An item is **Watched** when explicit completion exists or furthest progress reaches the
configured threshold (90% by default). Threshold classification never advances playback
or changes queue outcome state. Set `VLCQ_WATCHED_PERCENT` to a whole number from 1 through
100; invalid values fail startup before database mutation. Automatic advancement uses a
trustworthy resume without opening a modal.

The bottom status pane is a color-coded metadata surface: it shows the active filename and
source folder, player/VLC state, elapsed, remaining, and total time, a thick live progress
track/percentage, transport controls, and the complete wrapping last notice. These are
always visible metadata rather than overflow buttons. The player overflow is reserved for
relative seek, reconnect, Help, and Quit. Reconnect never selects or autoplays a queue item.
Absolute seek is available only for connected matching media with a known positive duration;
item history bars are inert.

When `vlcq` owns VLC 3, VLC's native **Next** button is supported: the controller
reconciles the observed staged successor into the authoritative queue without replaying
it. The VLC process starts with repeat-current, repeat-all, and random playback explicitly
disabled, regardless of saved VLC preferences. The window is never a full queue mirror;
there is no native Previous support in this change, and media opened directly or through
untracked VLC playlist navigation is rejected rather than adopted. Use `vlcq`'s **Previous**
and **Next** controls (or `p` and `n`) for authoritative queue navigation.

## TUI keys

| Key | Action |
| --- | --- |
| `o` | Open/change library root |
| Up/Down | Browse |
| Right | Enter highlighted folder; seek forward when the queue has focus |
| `Enter` | Enter folder or play video now |
| Left / `Backspace` | Parent folder; Left seeks backward outside the browser |
| `v` | Toggle video selection |
| `a` / `A` | Add / add and play the highlighted video, or use the explicit `v` selection |
| `Space` | Pause/resume |
| `n` / `p` | Next/previous |
| Left / `[` / `]` | Seek backward / forward 10 seconds |
| `d` | Remove queue entry (never the media file) |
| `J` / `K` | Move queue entry down/up |
| `r` | Retry selected queue entry |
| `c` | Clear watched/completed entries |
| `Shift+F10` | Open contextual actions for the focused section |
| `?` | Help |
| `q` | Quit after choosing whether to stop or keep the owned VLC process |

Add-to-end is idempotent; Play next moves existing entries without duplication and never
restarts the active entry. Add-and-play commits a batch only after its resume choice, so
Cancel cannot insert or reorder media. Clear confirms before removing queue entries and
never deletes media. Removing active playback first requires a confirmed stop and never
starts a successor. Successful removal or clear offers one in-memory undo; another queue
mutation, automatic advancement, root change, or shutdown expires it. Missing media can
be restored as visibly missing, while unsafe or replaced paths are rejected.

Empty sections show one short next-action hint, focused sections receive a distinct
highlight, and the Files header reports the root without repeating the currently expanded
folder. Queue rows display current-state or outcome indicators (`▶ PLAYING`,
`Ⅱ PAUSED`, `■ STOPPED`, `→ SKIPPED`, `✓ COMPLETED`, `! MISSING`, and `× FAILED`). Hidden
dotfiles and unsupported file types are not shown.

## Watched threshold

Watched status is derived from explicit completion or furthest recorded progress. The
threshold defaults to 90 percent and can be set before startup with
`VLCQ_WATCHED_PERCENT=1..100`. Only decimal whole percentages are accepted; an invalid
value exits clearly without opening or changing the database. The setting changes display,
filters, replay choices, Clear watched/completed, and `progress --json` classification, but
never rewrites stored completion evidence or queue outcomes.

## Finder / right-click handoff

The portable integration boundary is:

```sh
vlcq finder-handoff "$@"
```

Create a Finder Quick Action in Automator that receives files or folders, add a
**Run Shell Script** action with input passed *as arguments*, and invoke the
command above using its absolute installed path. A folder opens as the root;
selected files use their canonical nearest common parent. Mixed folder/file
arguments and unsafe/out-of-root selections are rejected.

## Storage, locking, and privacy

State defaults to `~/Library/Application Support/vlcq/vlcq.sqlite3`. Its
directory and files are private to the user; SQLite uses WAL and a busy timeout.
An advisory lock prevents a second controller from independently mutating the
queue. A second process exits clearly rather than risking corruption.

VLC is launched as a dedicated process with its normal macOS video interface
visible and an authenticated HTTP interface bound to `127.0.0.1`. Its launch command
explicitly passes `--no-repeat`, `--no-loop`, and `--no-random`; users can still change
VLC controls while it runs, but `vlcq` fails closed if VLC does not remain within the
bounded window. The generated password, Authorization header, raw VLC responses, and media
history are not logged. `vlcq` performs no non-loopback
network requests and never deletes, moves, copies, or modifies media files.

## Private backup and rollback

Before testing a new binary against an important queue, make a private SQLite backup with
the SQLite backup API (including WAL state), for example:

```sh
.venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
source = Path.home() / "Library/Application Support/vlcq/vlcq.sqlite3"
backup = source.with_name("vlcq.sqlite3.before-change")
with sqlite3.connect(source) as src, sqlite3.connect(backup) as dst:
    src.backup(dst)
PY
```

Schema v2 is preservation-first. Do not decrement `PRAGMA user_version`, delete the new
resume columns, or downgrade in place. To roll back to a version-1 binary, close vlcq and
restore the private backup; observations recorded after that backup are intentionally lost.

## Development and tests

```sh
.venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
.venv/bin/python -m ruff check .
.venv/bin/python -m mypy src
```

The normal suite uses fake/mock VLC interfaces and Textual's headless Pilot.
A real installed VLC 3 startup/authentication/shutdown smoke test is opt-in:

```sh
VLCQ_REAL_VLC=1 .venv/bin/python -m pytest tests/test_integration_real_vlc.py
```

It is skipped when not explicitly enabled or when the macOS VLC application is
unavailable. The opt-in test uses generated temporary media and exercises native Next,
natural transitions, bounded playlist contents, queue outcomes, and clean shutdown; it
never uses user media or credentials.
