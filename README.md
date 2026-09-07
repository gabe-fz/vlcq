# vlcq

`vlcq` is a folder-first Textual interface for a deterministic, persistent VLC 3
video queue on macOS. It browses local folders without automatically enqueueing
their contents, sends VLC one selected item at a time, and records monotonic
playback history plus a separate trustworthy resume position in SQLite.

## Install

Requires Python 3.12+ and VLC 3 installed at `/Applications/VLC.app` or available
as `vlc` on `PATH`.

```sh
python3 -m venv .venv
.venv/bin/pip install -e .
.venv/bin/vlcq
```

Running `vlcq` reopens the last library, or displays the native macOS folder
chooser on first use. You can also open a specific root directly:

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

Library rows show **No recorded progress**, **In progress**, or **Completed** independently
from queue state, plus known furthest progress and queued membership. The percentage in
`vlcq progress --json` is furthest-position progress, not measured watch-time coverage.
Missing durations and last-played times are shown as unknown. Replaced files are matched
by fingerprint and never inherit the old file's history.

Selecting an incomplete item with a trustworthy resume point opens **Resume**, **Start
over**, or **Cancel**. Legacy version-1 rows use the explicitly labeled **Resume from
furthest recorded progress** fallback; their last-played time remains unknown. Start over
preserves the historical maximum and completion evidence. Automatic queue advancement
uses a trustworthy resume without opening a modal.

The library pane provides mouse buttons for **Add to end**, **Play next**, and **Play now**;
the queue pane provides highlighted-entry **Play next**, **Play now**, reorder, removal, undo,
and clear controls. The selected-item details bar provides direct **Resume** and **Start
over** actions without opening a dialog.
Rows highlight without starting playback, and video checkboxes select a batch. Search and
history filters apply only to the current folder while selections remain preserved.

The active-player bar shows elapsed, total, and remaining time, with pause, previous/next,
relative seek, reconnect, and known-duration click-to-seek controls. Reconnect never
selects or autoplays a queue item. Essential controls remain available in the compact
80x24 layout.

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
| `c` | Clear completed entries |
| `q` | Quit after choosing whether to stop or keep the owned VLC process |

The browser toolbar provides **Open**, **Up**, **Select**, **Add to end**, **Play next**,
**Play now**, search, and history filters. The queue toolbar targets the highlighted queue
entry. Add-to-end is idempotent; Play next moves existing entries without duplication and
never restarts the active entry. Add-and-play commits a batch only after its resume choice,
so Cancel cannot insert or reorder media. Clear confirms before removing queue entries and
never deletes media.

Removing active playback first requires a confirmed stop and never starts a successor.
Successful removal or clear offers one in-memory undo. Undo restores order and retained
history but does not restart playback; another queue mutation, automatic advancement,
root change, or shutdown expires it. Missing media can be restored as visibly missing,
while unsafe or replaced paths are rejected.
When either list overflows, use its vertical scrollbar or mouse wheel to reach
all rows, and drag the horizontal scrollbar at the bottom to reveal long names.
Left and Right remain app shortcuts for navigation and seeking.
Empty panes show
the next useful action, focused panes receive a distinct highlight, and every
queue row displays state indicators (`▶ PLAYING`, `Ⅱ PAUSED`, `✓ COMPLETED`,
`! MISSING`, and `× FAILED`). Hidden dotfiles and unsupported file
types are not shown.

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
visible and an authenticated HTTP interface bound to `127.0.0.1`. The generated password, Authorization header, raw VLC
responses, and media history are not logged. `vlcq` performs no non-loopback
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
unavailable. This smoke test opens VLC briefly; it does not play or modify media.
