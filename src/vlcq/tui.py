from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
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
    .queue-playing { color: $warning; text-style: bold; }
    .queue-completed { color: $success; }
    .queue-missing, .queue-failed { color: $error; }
    RootPrompt, ConfirmClear, ConfirmClearAll, QuitPrompt { align: center middle; }
    RootPrompt > Label, RootPrompt > Input, ConfirmClear > Label,
    ConfirmClearAll > Label, QuitPrompt > Label {
        width: 70%; padding: 1; background: $surface; border: solid $accent;
    }
    """
    BINDINGS: ClassVar = [
        Binding("o", "open_root", "Open folder"),
        Binding("backspace", "parent", "Parent", priority=True),
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
                    yield Button("Sort", id="browser-sort")
                yield ListView(id="browser")
            with Vertical(id="queue-pane"):
                yield Label("QUEUE", classes="pane-title")
                with Horizontal(classes="toolbar"):
                    yield Button("Add", id="queue-add", variant="success")
                    yield Button("Play", id="queue-play", variant="warning")
                    yield Button("Sort", id="queue-sort")
                    yield Button("Clear", id="queue-clear", variant="error")
                yield ListView(id="queue")
        yield ProgressBar(total=100, id="progress")
        yield Footer()

    async def on_mount(self) -> None:
        await self.refresh_browser()
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
        browser_pane = self.query_one("#browser-pane")
        queue_pane = self.query_one("#queue-pane")
        ancestors = event.widget.ancestors_with_self
        browser_pane.set_class(browser_pane in ancestors, "focused")
        queue_pane.set_class(queue_pane in ancestors, "focused")

    def update_status(self, message: str) -> None:
        for widget in self.query("#status"):
            if isinstance(widget, Static):
                widget.update(
                    f"Root: {self.root.name}  "
                    f"Folder: {self.browser_path.relative_to(self.root) or '.'}  {message}"
                )

    def refresh_playback(self) -> None:
        status = self.controller.status
        percent = (
            min(100.0, status.position_ms * 100 / status.duration_ms) if status.duration_ms else 0.0
        )
        for widget in self.query("#progress"):
            if isinstance(widget, ProgressBar):
                widget.update(progress=percent)
        current = self.queue.current()
        name = current.path.name if current else "none"
        elapsed = status.position_ms // 1000
        duration = status.duration_ms // 1000
        self.update_status(f"{status.state}  {name}  {elapsed}s/{duration}s")

    async def refresh_browser(self) -> None:
        browser = self.query_one("#browser", ListView)
        await browser.clear()
        entries = await asyncio.to_thread(list_folder, self.browser_path, self.root)
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
        for entry in self.browser_entries:
            selected = entry.path in self.selected_paths
            marker = "[x]" if selected else "[ ]"
            icon = "▸" if entry.is_dir else marker
            classes = "folder-entry" if entry.is_dir else "video-entry"
            if selected:
                classes += " selected-video"
            await browser.append(ListItem(Label(f"{icon} {entry.name}"), classes=classes))

    def refresh_queue(self) -> None:
        view = self.query_one("#queue", ListView)
        view.clear()
        current = self.queue.current()
        for entry in self.queue.entries():
            marker = "▶" if current and current.id == entry.id else " "
            view.append(
                ListItem(
                    Label(f"{marker} {entry.path.name} [{entry.state}]"),
                    classes=f"queue-{entry.state}",
                )
            )

    def _browser_entry(self) -> BrowserEntry | None:
        view = self.query_one("#browser", ListView)
        if view.index is None or view.index >= len(self.browser_entries):
            return None
        return self.browser_entries[view.index]

    async def action_activate(self) -> None:
        queue_view = self.query_one("#queue", ListView)
        if self.focused is queue_view and queue_view.index is not None:
            if self.no_vlc:
                self.queue.play_now(queue_view.index)
            else:
                await self.controller.play_index(queue_view.index)
            self.refresh_queue()
            return
        entry = self._browser_entry()
        if not entry:
            return
        if entry.is_dir:
            self.browser_path = entry.path
            await self.refresh_browser()
            self.update_status("Browsing")
        else:
            if entry.path not in self.selected_paths:
                self.selected_paths.add(entry.path)
            self.queue.add([entry.path])
            self.refresh_queue()
            index = next(
                i for i, item in enumerate(self.queue.entries()) if item.path == entry.path
            )
            if self.no_vlc:
                self.queue.play_now(index)
            else:
                await self.controller.play_index(index)
            self.refresh_queue()

    async def action_parent(self) -> None:
        if self.browser_path != self.root:
            self.browser_path = self.browser_path.parent
            await self.refresh_browser()
        self.update_status("At root" if self.browser_path == self.root else "Browsing")

    async def action_select(self) -> None:
        entry = self._browser_entry()
        if entry and entry.supported:
            if entry.path in self.selected_paths:
                self.selected_paths.remove(entry.path)
            else:
                self.selected_paths.add(entry.path)
            await self.refresh_browser()

    async def action_add_selected(self) -> None:
        if self.selected_paths:
            self.queue.add(list(self.selected_paths))
            self.selected_paths.clear()
            await self.refresh_browser()
            self.refresh_queue()
            self.update_status("Added to queue")

    async def action_add_and_play(self) -> None:
        await self.action_add_selected()
        if self.queue.entries():
            if self.no_vlc:
                self.queue.play_now(0)
            else:
                await self.controller.play_index(0)
            self.refresh_queue()

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
        if not self.no_vlc:
            await self.controller.toggle_pause()

    async def action_next(self) -> None:
        if self.no_vlc:
            self.queue.next()
        else:
            await self.controller.next()
        self.refresh_queue()

    async def action_previous(self) -> None:
        if self.no_vlc:
            self.queue.previous()
        else:
            await self.controller.previous()
        self.refresh_queue()

    def _browser_has_focus(self) -> bool:
        browser_pane = self.query_one("#browser-pane")
        return self.focused is not None and browser_pane in self.focused.ancestors_with_self

    async def action_left(self) -> None:
        if self._browser_has_focus():
            await self.action_parent()
        elif not self.no_vlc:
            await self.controller.seek(-10)

    async def action_right(self) -> None:
        entry = self._browser_entry()
        if self._browser_has_focus() and entry is not None and entry.is_dir:
            await self.action_activate()
        elif not self.no_vlc:
            await self.controller.seek(10)

    async def action_seek(self, seconds: int) -> None:
        if not self.no_vlc:
            await self.controller.seek(seconds)

    def _queue_index(self) -> int | None:
        return self.query_one("#queue", ListView).index

    def action_remove(self) -> None:
        index = self._queue_index()
        if index is not None:
            self.queue.remove(index)
            self.refresh_queue()

    def action_move_down(self) -> None:
        index = self._queue_index()
        if index is not None:
            self.queue.move(index, 1)
            self.refresh_queue()

    def action_move_up(self) -> None:
        index = self._queue_index()
        if index is not None:
            self.queue.move(index, -1)
            self.refresh_queue()

    def action_retry(self) -> None:
        index = self._queue_index()
        if index is not None:
            try:
                self.queue.retry(index)
            except FileNotFoundError:
                self.update_status("Video is missing")
            self.refresh_queue()

    def action_clear_all(self) -> None:
        def confirmed(value: bool | None) -> None:
            if value:
                self.queue.clear_all()
                self.refresh_queue()

        self.push_screen(ConfirmClearAll(), confirmed)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id == "browser-open":
            self.action_open_root()
        elif button_id == "browser-up":
            await self.action_parent()
        elif button_id == "browser-select":
            await self.action_select()
        elif button_id == "browser-sort":
            self.browser_reverse = not self.browser_reverse
            await self.refresh_browser()
        elif button_id == "queue-add":
            await self.action_add_selected()
        elif button_id == "queue-play":
            index = self._queue_index()
            if index is not None:
                if self.no_vlc:
                    self.queue.play_now(index)
                else:
                    await self.controller.play_index(index)
                self.refresh_queue()
        elif button_id == "queue-sort":
            self.queue.sort_natural()
            self.refresh_queue()
        elif button_id == "queue-clear":
            self.action_clear_all()

    def action_clear_completed(self) -> None:
        def confirmed(value: bool | None) -> None:
            if value:
                self.queue.clear_completed()
                self.refresh_queue()

        self.push_screen(ConfirmClear(), confirmed)

    def action_help(self) -> None:
        self.notify(
            "o open · Enter open/play · Backspace parent · v select · a/A add/play · "
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
