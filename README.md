# vlcq

`vlcq` is a folder-first Textual interface for a deterministic, persistent VLC 3
video queue on macOS. It browses local folders without automatically enqueueing
their contents, sends VLC one selected item at a time, and records monotonic
playback progress in SQLite.

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

The browser toolbar provides **Open**, **Up**, **Select**, **Add & play**, and
**Sort**. The queue toolbar provides **Add**, **Play**, **Sort**, and **Clear**;
Add and add-and-play use the highlighted playable browser item when there is no
`v` selection. An explicit multi-selection takes precedence and is naturally
ordered. Clear confirms before removing queue entries and never deletes media.
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
