from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    ListItem,
    ListView,
    ProgressBar,
    Static,
)

from .controller import PlaybackController
from .database import Database
from .models import BrowserEntry
from .paths import PathError, canonical_root, list_folder, natural_key
from .queue import QueueService
from .vlc import VLCError

_QUEUE_STATE_LABELS: dict[str, str] = {
    "queued": "○ QUEUED",
    "playing": "▶ PLAYING",
    "paused": "Ⅱ PAUSED",
    "stopped": "■ STOPPED",
    "skipped": "→ SKIPPED",
    "completed": "✓ COMPLETED",
    "missing": "! MISSING",
    "failed": "× FAILED",
}


class RootPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Label("Open library folder")
        yield Input(placeholder="/path/to/folder", id="root-input")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)


class ConfirmClear(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Clear completed queue entries? y/n")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmClearAll(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Clear every queue entry? Media files will not be changed. y/n")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class QuitPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [
        Binding("s", "stop", "Stop VLC"),
        Binding("k", "keep", "Keep VLC"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Quit: [s] stop VLC, [k] keep VLC running, [Esc] cancel")

    def action_stop(self) -> None:
        self.dismiss("stop")

    def action_keep(self) -> None:
        self.dismiss("keep")

    def action_cancel(self) -> None:
        self.dismiss(None)


class VLCQApp(App[None]):
    TITLE = "vlcq"
    CSS = """
    #status { height: 3; padding: 0 1; color: $text; background: $boost; }
    #panes { height: 1fr; }
    #browser-pane { width: 1fr; border: solid $primary; background: $surface; }
    #queue-pane { width: 1fr; border: solid $secondary; background: $surface-darken-1; }
    #browser-pane.focused { border: heavy $warning; background: $primary-background; }
    #queue-pane.focused { border: heavy $warning; background: $secondary-background; }
    .pane-title { height: 1; text-style: bold; content-align: center middle; }
    .toolbar { height: 3; align-horizontal: center; }
    .toolbar Button { min-width: 8; width: 1fr; margin: 0 1; }
    #browser, #queue { height: 1fr; }
    .folder-entry { color: $primary-lighten-2; text-style: bold; }
    .video-entry { color: $success-lighten-1; }
    .selected-video { color: $warning; text-style: bold; }
    .queue-playing, .queue-paused { color: $warning; text-style: bold; }
    .queue-completed { color: $success; }
    .queue-missing, .queue-failed { color: $error; text-style: bold; }
    .queue-stopped, .queue-skipped { color: $text-muted; }
    .queue-queued { color: $text; }
    .pane-help { height: 2; padding: 0 1; color: $text-muted; }
    .empty-state { height: auto; padding: 1; color: $text-muted; }
    RootPrompt, ConfirmClear, ConfirmClearAll, QuitPrompt { align: center middle; }
    RootPrompt > Label, RootPrompt > Input, ConfirmClear > Label,
    ConfirmClearAll > Label, QuitPrompt > Label {
        width: 70%; padding: 1; background: $surface; border: solid $accent;
    }
    """
    BINDINGS: ClassVar = [
        Binding("o", "open_root", "Open folder"),
        Binding("backspace", "parent", "Parent"),
        Binding("enter", "activate", "Open/play", priority=True),
        Binding("v", "select", "Select"),
        Binding("a", "add_selected", "Add"),
        Binding("A", "add_and_play", "Add & play"),
        Binding("space", "pause", "Play/pause"),
        Binding("d,delete", "remove", "Remove"),
        Binding("J", "move_down", "Move down"),
        Binding("K", "move_up", "Move up"),
        Binding("n", "next", "Next"),
        Binding("p", "previous", "Previous"),
        Binding("left", "left", "Up/back"),
        Binding("right", "right", "Open/forward"),
        Binding("[", "seek(-10)", "Back 10s", show=False),
        Binding("]", "seek(10)", "Forward 10s", show=False),
        Binding("r", "retry", "Retry"),
        Binding("c", "clear_completed", "Clear completed"),
        Binding("?", "help", "Help"),
        Binding("q", "quit_app", "Quit"),
    ]

    def __init__(
        self,
        *,
        root: Path,
        database: Database,
        no_vlc: bool = False,
        autoplay: bool = False,
    ) -> None:
        super().__init__()
        self.database = database
        self.queue = QueueService(database)
        self.root = self.queue.open(root)
        self.browser_path = self.root
        self.browser_entries: list[BrowserEntry] = []
        self.selected_paths: set[Path] = set()
        self.browser_reverse = False
        self.controller = PlaybackController(self.queue)
        self.no_vlc = no_vlc
        self.autoplay = autoplay
        self._notice = "Ready"
        self._playback_summary = ""

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        with Horizontal(id="panes"):
            with Vertical(id="browser-pane"):
                yield Label("LIBRARY", classes="pane-title")
                with Horizontal(classes="toolbar"):
                    yield Button("Open", id="browser-open", variant="primary")
                    yield Button("Up", id="browser-up")
                    yield Button("Select", id="browser-select")
                    yield Button("Add & play", id="browser-add-play", variant="warning")
                    yield Button("Sort", id="browser-sort")
                yield Static(
                    "Highlight a video: Add queues it; Play starts it. Press v for multi-select.",
                    id="browser-help",
                    classes="pane-help",
                )
                yield Static(
                    "No folders or playable videos here.", id="browser-empty", classes="empty-state"
                )
                yield ListView(id="browser")
            with Vertical(id="queue-pane"):
                yield Label("QUEUE", classes="pane-title")
                with Horizontal(classes="toolbar"):
                    yield Button("Add", id="queue-add", variant="success")
                    yield Button("Play", id="queue-play", variant="warning")
                    yield Button("Sort", id="queue-sort")
                    yield Button("Clear", id="queue-clear", variant="error")
                yield Static(
                    "Queue is empty — highlight a playable video and press Add or A.",
                    id="queue-empty",
                    classes="empty-state",
                )
                yield ListView(id="queue")
        yield ProgressBar(total=100, id="progress")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_browser()
        # Start in the folder-first workflow: the first browser item is ready
        # for keyboard actions without requiring an extra focus/tab step.
        self.query_one("#browser", ListView).focus()
        self.refresh_queue()
        self.update_status("Ready")
        if not self.no_vlc:
            try:
                await self.controller.start()
                self.update_status("VLC connected")
            except (VLCError, OSError):
                self.update_status("VLC unavailable — press r to retry")
        if self.autoplay and self.queue.entries():
            if self.no_vlc:
                self.queue.play_now(0)
            elif self.controller.client is not None:
                await self.controller.play_index(0)
            self.refresh_queue()
        self.set_interval(1, self.refresh_playback)

    async def on_unmount(self) -> None:
        if not self.no_vlc and self.controller.client is not None:
            await self.controller.stop()

    def on_descendant_focus(self, event: events.DescendantFocus) -> None:
        try:
            browser_pane = self.query_one("#browser-pane")
            queue_pane = self.query_one("#queue-pane")
        except NoMatches:
            # Modal screens replace the main widget tree while they are open.
            return
        ancestors = event.widget.ancestors_with_self
        browser_pane.set_class(browser_pane in ancestors, "focused")
        queue_pane.set_class(queue_pane in ancestors, "focused")

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        del event
        self._refresh_controls()

    def _render_status(self) -> None:
        try:
            relative = self.browser_path.relative_to(self.root)
        except ValueError:
            relative = Path(".")
        message = self._notice
        if self._playback_summary:
            message = f"{message} · {self._playback_summary}"
        for widget in self.query("#status"):
            if isinstance(widget, Static):
                widget.update(f"Root: {self.root.name}  Folder: {relative}  {message}")

    def update_status(self, message: str) -> None:
        self._notice = message
        self._render_status()

    def refresh_playback(self) -> None:
        current = self.queue.current()
        status = self.controller.status
        if self.no_vlc:
            state = current.state if current else "offline"
            status_name = state
            position_ms = 0
            duration_ms = 0
        else:
            status_name = status.state
            position_ms = status.position_ms
            duration_ms = status.duration_ms
        percent = min(100.0, position_ms * 100 / duration_ms) if duration_ms else 0.0
        for widget in self.query("#progress"):
            if isinstance(widget, ProgressBar):
                widget.update(progress=percent)
        name = current.path.name if current else "none"
        elapsed = position_ms // 1000
        duration = duration_ms // 1000
        self._playback_summary = f"{status_name}  {name}  {elapsed}s/{duration}s"
        self._render_status()
        # Controller observations update SQLite; redraw rows even when no user input occurs.
        try:
            self.refresh_queue()
        except NoMatches:
            # A timer can tick while Textual is tearing down the application.
            pass

    async def refresh_browser(self) -> None:
        browser = self.query_one("#browser", ListView)
        highlighted = self._browser_entry()
        highlighted_path = highlighted.path if highlighted is not None else None
        await browser.clear()
        try:
            entries = await asyncio.to_thread(list_folder, self.browser_path, self.root)
        except (OSError, PathError) as exc:
            self.browser_entries = []
            self.query_one("#browser-empty", Static).update(f"Unable to read this folder: {exc}")
            self.query_one("#browser-empty", Static).display = True
            self._refresh_controls()
            self.update_status(f"Browse failed: {exc}")
            return
        if self.browser_reverse:
            folders = sorted(
                (entry for entry in entries if entry.is_dir),
                key=lambda entry: natural_key(entry.name),
                reverse=True,
            )
            files = sorted(
                (entry for entry in entries if not entry.is_dir),
                key=lambda entry: natural_key(entry.name),
                reverse=True,
            )
            entries = [*folders, *files]
        self.browser_entries = entries
        empty = self.query_one("#browser-empty", Static)
        empty.display = not bool(entries)
        if not entries:
            empty.update("No folders or playable videos here.")
        for entry in self.browser_entries:
            selected = entry.path in self.selected_paths
            marker = "[x]" if selected else "[ ]"
            icon = "▸" if entry.is_dir else marker
            classes = "folder-entry" if entry.is_dir else "video-entry"
            if selected:
                classes += " selected-video"
            await browser.append(ListItem(Label(f"{icon} {entry.name}"), classes=classes))
        if highlighted_path is not None:
            restored_index = next(
                (
                    index
                    for index, entry in enumerate(self.browser_entries)
                    if entry.path == highlighted_path
                ),
                None,
            )
            browser.index = (
                restored_index
                if restored_index is not None
                else (0 if self.browser_entries else None)
            )
        elif self.browser_entries and browser.index is None:
            browser.index = 0
        self._refresh_controls()

    def refresh_queue(
        self, selected_path: Path | None = None, *, selection_captured: bool = False
    ) -> None:
        try:
            view = self.query_one("#queue", ListView)
        except NoMatches:
            return
        if not selection_captured:
            old_entries = self.queue.entries()
            selected_index = view.index
            selected_path = (
                old_entries[selected_index].path
                if selected_index is not None and 0 <= selected_index < len(old_entries)
                else None
            )
        view.clear()
        entries = self.queue.entries()
        current = self.queue.current()
        empty = self.query_one("#queue-empty", Static)
        empty.display = not bool(entries)
        if entries:
            for entry in entries:
                is_current = current is not None and current.id == entry.id
                state_label = _QUEUE_STATE_LABELS.get(entry.state, f"? {entry.state.upper()}")
                marker = "◆" if is_current else " "
                state_class = {
                    "queued": "queue-queued",
                    "playing": "queue-playing",
                    "paused": "queue-paused",
                    "stopped": "queue-stopped",
                    "skipped": "queue-skipped",
                    "completed": "queue-completed",
                    "missing": "queue-missing",
                    "failed": "queue-failed",
                }.get(entry.state, "queue-failed")
                view.append(
                    ListItem(
                        Label(f"{marker} {entry.path.name} — {state_label}"),
                        classes=state_class,
                    )
                )
        if selected_path is not None:
            restored_index = next(
                (index for index, entry in enumerate(entries) if entry.path == selected_path),
                None,
            )
            if restored_index is not None:

                def restore_queue_highlight() -> None:
                    if view.is_attached and restored_index < len(view.children):
                        view.index = restored_index

                # ListView.append mounts on the next refresh; restore in the
                # following event turn so the new child collection is present.
                self.call_after_refresh(lambda: self.call_later(restore_queue_highlight))
            elif entries:
                view.index = 0
        elif entries and view.index is None:
            view.index = 0
        self._refresh_controls()

    def _browser_entry(self) -> BrowserEntry | None:
        try:
            view = self.query_one("#browser", ListView)
        except NoMatches:
            return None
        if view.index is None or not 0 <= view.index < len(self.browser_entries):
            return None
        return self.browser_entries[view.index]

    def _refresh_controls(self) -> None:
        """Keep visible actions honest as browser and queue state changes."""
        try:
            entry = self._browser_entry()
            self.query_one("#browser-up", Button).disabled = self.browser_path == self.root
            self.query_one("#browser-select", Button).disabled = not (
                entry is not None and entry.supported
            )
            can_add = bool(self.selected_paths) or (entry is not None and entry.supported)
            self.query_one("#queue-add", Button).disabled = not can_add
            self.query_one("#browser-add-play", Button).disabled = not can_add
            queue_entries = self.queue.entries()
            queue_view = self.query_one("#queue", ListView)
            self.query_one("#queue-play", Button).disabled = not (
                bool(queue_entries) and queue_view.index is not None
            )
            self.query_one("#queue-clear", Button).disabled = not bool(queue_entries)
        except NoMatches:
            # Refresh calls can happen before Textual has composed the app.
            return

    def _paths_for_add(self) -> list[Path]:
        """Return an explicit selection, or the highlighted playable item."""
        if self.selected_paths:
            return sorted(self.selected_paths, key=lambda path: natural_key(str(path)))
        entry = self._browser_entry()
        if entry is not None and entry.supported:
            return [entry.path]
        return []

    def _queue_path_index(self, path: Path) -> int | None:
        return next(
            (index for index, entry in enumerate(self.queue.entries()) if entry.path == path),
            None,
        )

    def _queue_selected_path(self) -> Path | None:
        index = self._queue_index()
        if index is None:
            return None
        entries = self.queue.entries()
        return entries[index].path

    async def action_activate(self) -> None:
        if isinstance(self.screen, RootPrompt):
            # The app-level priority binding runs before Input.Submitted. Handle
            # the root value here so the modal remains keyboard-operable.
            try:
                value = self.screen.query_one("#root-input", Input).value
            except NoMatches:
                return
            self.screen.dismiss(value)
            return
        try:
            queue_view = self.query_one("#queue", ListView)
        except NoMatches:
            # There is no main-screen action to activate while another modal is
            # open (for example, a confirmation dialog).
            return
        if self._queue_has_focus() and queue_view.index is not None:
            index = self._queue_index()
            if index is None:
                self.update_status("Nothing is highlighted in the queue")
                return
            try:
                if self.no_vlc:
                    self.queue.play_now(index)
                else:
                    await self.controller.play_index(index)
            except (IndexError, VLCError, OSError) as exc:
                self.update_status(f"Play failed: {exc}")
            else:
                self.update_status("Playing highlighted queue item")
            self.refresh_queue()
            return
        entry = self._browser_entry()
        if entry is None:
            self.update_status("Nothing is highlighted — open a folder or highlight a video")
            return
        if entry.is_dir:
            self.browser_path = entry.path
            await self.refresh_browser()
            self.update_status("Browsing")
            return
        try:
            self.queue.add([entry.path])
            index = self._queue_path_index(entry.path)
            if index is None:
                raise RuntimeError("video was not added to the queue")
            if self.no_vlc:
                self.queue.play_now(index)
            else:
                await self.controller.play_index(index)
        except (IndexError, OSError, PathError, RuntimeError, VLCError) as exc:
            self.update_status(f"Play failed: {exc}")
        else:
            # Enter is a one-item play action.  If that item was selected for
            # a batch earlier, consume only that selection while preserving
            # any other explicit selections for a later Add action.
            self.selected_paths.discard(entry.path)
            await self.refresh_browser()
            self.update_status(f"Playing {entry.path.name}")
        self.refresh_queue()

    async def action_parent(self) -> None:
        if not self._browser_has_focus():
            self.update_status("Parent navigation is available in the browser pane")
            return
        if self.browser_path != self.root:
            self.browser_path = self.browser_path.parent
            await self.refresh_browser()
        self.update_status("At root" if self.browser_path == self.root else "Browsing")

    async def action_select(self) -> None:
        if not self._browser_has_focus():
            self.update_status("Select is available in the browser pane")
            return
        entry = self._browser_entry()
        if entry is None:
            self.update_status("Nothing is highlighted to select")
        elif entry.supported:
            if entry.path in self.selected_paths:
                self.selected_paths.remove(entry.path)
                self.update_status(f"Deselected {entry.path.name}")
            else:
                self.selected_paths.add(entry.path)
                self.update_status(f"Selected {entry.path.name} ({len(self.selected_paths)} total)")
            await self.refresh_browser()
        else:
            self.update_status("Folders cannot be selected; highlight a playable video")

    async def action_add_selected(self) -> None:
        paths = self._paths_for_add()
        if not paths:
            self.update_status("Nothing to add — highlight a playable video or press v to select")
            return
        try:
            self.queue.add(paths)
        except (OSError, PathError, RuntimeError, ValueError) as exc:
            self.update_status(f"Add failed: {exc}")
            return
        self.selected_paths.clear()
        await self.refresh_browser()
        self.refresh_queue()
        self.update_status(f"Added {len(paths)} video{'s' if len(paths) != 1 else ''} to queue")

    async def action_add_and_play(self) -> None:
        paths = self._paths_for_add()
        if not paths:
            self.update_status("Nothing to play — highlight a playable video or press v to select")
            return
        target = paths[0]
        try:
            self.queue.add(paths)
            index = self._queue_path_index(target)
            if index is None:
                raise RuntimeError("video was not added to the queue")
            if self.no_vlc:
                self.queue.play_now(index)
            else:
                await self.controller.play_index(index)
        except (IndexError, OSError, PathError, RuntimeError, VLCError) as exc:
            self.update_status(f"Add and play failed: {exc}")
            self.refresh_queue()
            return
        self.selected_paths.clear()
        await self.refresh_browser()
        self.refresh_queue()
        self.update_status(f"Playing {target.name}")

    def action_open_root(self) -> None:
        def opened(value: str | None) -> None:
            if not value:
                return
            try:
                self.root = self.queue.open(canonical_root(value))
                self.browser_path = self.root
                self.selected_paths.clear()
                self.call_later(self.refresh_browser)
            except PathError as exc:
                self.update_status(str(exc))

        self.push_screen(RootPrompt(), opened)

    async def action_pause(self) -> None:
        current = self.queue.current()
        if current is None:
            self.update_status("Nothing is playing")
            return
        if not self.no_vlc and self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return
        try:
            if self.no_vlc:
                state = "paused" if current.state == "playing" else "playing"
                self.queue.database.set_state(current.id, state)
            else:
                await self.controller.toggle_pause()
                state = self.controller.status.state
        except (OSError, VLCError) as exc:
            self.update_status(f"Pause/resume failed: {exc}")
        else:
            self.update_status("Paused" if state == "paused" else "Playing")
        self.refresh_queue()

    async def action_next(self) -> None:
        if not self.no_vlc and self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return
        try:
            if self.no_vlc:
                entry = self.queue.next()
            else:
                await self.controller.next()
                entry = self.queue.current()
        except (IndexError, OSError, VLCError) as exc:
            self.update_status(f"Next failed: {exc}")
        else:
            self.update_status(f"Playing {entry.path.name}" if entry else "No next queue item")
        self.refresh_queue()

    async def action_previous(self) -> None:
        if not self.no_vlc and self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return
        try:
            if self.no_vlc:
                entry = self.queue.previous()
            else:
                await self.controller.previous()
                entry = self.queue.current()
        except (IndexError, OSError, VLCError) as exc:
            self.update_status(f"Previous failed: {exc}")
        else:
            self.update_status(f"Playing {entry.path.name}" if entry else "Queue is empty")
        self.refresh_queue()

    def _browser_has_focus(self) -> bool:
        try:
            browser_pane = self.query_one("#browser-pane")
        except NoMatches:
            return False
        return self.focused is not None and browser_pane in self.focused.ancestors_with_self

    def _queue_has_focus(self) -> bool:
        try:
            queue_pane = self.query_one("#queue-pane")
        except NoMatches:
            return False
        return self.focused is not None and queue_pane in self.focused.ancestors_with_self

    async def action_left(self) -> None:
        if self._browser_has_focus():
            await self.action_parent()
        else:
            await self.action_seek(-10)

    async def action_right(self) -> None:
        entry = self._browser_entry()
        if self._browser_has_focus() and entry is not None and entry.is_dir:
            await self.action_activate()
        else:
            await self.action_seek(10)

    async def action_seek(self, seconds: int) -> None:
        if self.no_vlc:
            return
        if self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return
        try:
            await self.controller.seek(seconds)
        except (OSError, VLCError) as exc:
            self.update_status(f"Seek failed: {exc}")

    def _queue_index(self) -> int | None:
        try:
            view = self.query_one("#queue", ListView)
        except NoMatches:
            return None
        if view.index is None or not 0 <= view.index < len(self.queue.entries()):
            return None
        return view.index

    def action_remove(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Remove is available in the queue pane")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        try:
            name = self.queue.entries()[index].path.name
            self.queue.remove(index)
        except (IndexError, OSError, RuntimeError) as exc:
            self.update_status(f"Remove failed: {exc}")
        else:
            self.update_status(f"Removed {name} from queue (media was not changed)")
        self.refresh_queue()

    def action_move_down(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Move is available in the queue pane")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        selected_path = self._queue_selected_path()
        try:
            self.queue.move(index, 1)
        except (IndexError, OSError, RuntimeError) as exc:
            self.update_status(f"Move failed: {exc}")
        else:
            self.update_status("Moved queue item down")
        self.refresh_queue(selected_path, selection_captured=True)

    def action_move_up(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Move is available in the queue pane")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        selected_path = self._queue_selected_path()
        try:
            self.queue.move(index, -1)
        except (IndexError, OSError, RuntimeError) as exc:
            self.update_status(f"Move failed: {exc}")
        else:
            self.update_status("Moved queue item up")
        self.refresh_queue(selected_path, selection_captured=True)

    async def action_retry(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Retry is available in the queue pane")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        try:
            # Reconnect before changing queue state.  A failed startup should
            # leave the selected row untouched and make the retry actionable
            # rather than merely reporting that VLC is disconnected.
            if not self.no_vlc and self.controller.client is None:
                await self.controller.start()
            # Validate the file before asking a real VLC client to play it.  The
            # queue service also preserves a useful ``missing`` state when the
            # file disappeared, instead of silently advancing to another item.
            entry = self.queue.retry(index)
            if not self.no_vlc:
                await self.controller.play_index(index)
        except FileNotFoundError:
            self.update_status("Video is missing — restore the file, then press r")
        except (IndexError, OSError, RuntimeError, VLCError) as exc:
            self.update_status(f"Retry failed: {exc}")
        else:
            self.update_status(f"Retrying {entry.path.name}")
        self.refresh_queue()

    def action_clear_all(self) -> None:
        if not self.queue.entries():
            self.update_status("Queue is already empty")
            return

        def confirmed(value: bool | None) -> None:
            if not value:
                self.update_status("Clear cancelled")
                return
            try:
                self.queue.clear_all()
            except (OSError, RuntimeError) as exc:
                self.update_status(f"Clear failed: {exc}")
                return
            self.refresh_queue()
            self.update_status("Queue cleared (media files were not changed)")

        self.push_screen(ConfirmClearAll(), confirmed)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "browser-open":
            self.action_open_root()
        elif button_id == "browser-up":
            await self.action_parent()
        elif button_id == "browser-select":
            await self.action_select()
        elif button_id == "browser-add-play":
            await self.action_add_and_play()
        elif button_id == "browser-sort":
            self.browser_reverse = not self.browser_reverse
            await self.refresh_browser()
        elif button_id == "queue-add":
            await self.action_add_selected()
        elif button_id == "queue-play":
            index = self._queue_index()
            if index is None:
                self.update_status("Nothing is highlighted in the queue")
                return
            try:
                if self.no_vlc:
                    self.queue.play_now(index)
                else:
                    await self.controller.play_index(index)
            except (IndexError, OSError, VLCError) as exc:
                self.update_status(f"Play failed: {exc}")
            else:
                self.update_status("Playing highlighted queue item")
            self.refresh_queue()
        elif button_id == "queue-sort":
            selected_path = self._queue_selected_path()
            self.queue.sort_natural()
            self.refresh_queue(selected_path, selection_captured=True)
            self.update_status("Queue sorted naturally")
        elif button_id == "queue-clear":
            self.action_clear_all()

    def action_clear_completed(self) -> None:
        if not any(entry.state == "completed" for entry in self.queue.entries()):
            self.update_status("No completed queue entries to clear")
            return

        def confirmed(value: bool | None) -> None:
            if not value:
                self.update_status("Clear cancelled")
                return
            try:
                self.queue.clear_completed()
            except (OSError, RuntimeError) as exc:
                self.update_status(f"Clear failed: {exc}")
                return
            self.refresh_queue()
            self.update_status("Completed queue entries cleared")

        self.push_screen(ConfirmClear(), confirmed)

    def action_help(self) -> None:
        self.notify(
            "o open · Enter open/play · Backspace parent · v select · a add · A add/play · "
            "Space pause · n/p next/previous · arrows seek · d remove · J/K reorder",
            title="vlcq keys",
            timeout=8,
        )

    async def _finish_quit(self, choice: str) -> None:
        await self.controller.stop(stop_vlc=choice == "stop")
        self.exit()

    def action_quit_app(self) -> None:
        if self.no_vlc:
            self.exit()
            return

        def chosen(value: str | None) -> None:
            if value:
                self.run_worker(self._finish_quit(value), exclusive=True)

        self.push_screen(QuitPrompt(), chosen)
