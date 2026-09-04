from __future__ import annotations

import asyncio
from pathlib import Path
from typing import ClassVar

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Footer, Header, Input, Label, ListItem, ListView, ProgressBar, Static

from .controller import PlaybackController
from .database import Database
from .models import BrowserEntry
from .paths import PathError, canonical_root, list_folder
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
    #status { height: 3; padding: 0 1; }
    #panes { height: 1fr; }
    #browser-pane, #queue-pane { width: 1fr; border: solid $accent; }
    #browser, #queue { height: 1fr; }
    RootPrompt { align: center middle; }
    RootPrompt > Label, RootPrompt > Input { width: 70%; padding: 1; background: $surface; }
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
        Binding("left", "seek(-10)", "Back 10s"),
        Binding("right", "seek(10)", "Forward 10s"),
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
        self.controller = PlaybackController(self.queue)
        self.no_vlc = no_vlc
        self.autoplay = autoplay

    def compose(self) -> ComposeResult:
        yield Header()
        yield Static(id="status")
        with Horizontal(id="panes"):
            with Vertical(id="browser-pane"):
                yield Label("Library")
                yield ListView(id="browser")
            with Vertical(id="queue-pane"):
                yield Label("Queue")
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

    def update_status(self, message: str) -> None:
        self.query_one("#status", Static).update(
            f"Root: {self.root.name}  Folder: {self.browser_path.relative_to(self.root) or '.'}  {message}"
        )

    def refresh_playback(self) -> None:
        status = self.controller.status
        percent = (
            min(100.0, status.position_ms * 100 / status.duration_ms) if status.duration_ms else 0.0
        )
        self.query_one("#progress", ProgressBar).update(progress=percent)
        current = self.queue.current()
        name = current.path.name if current else "none"
        elapsed = status.position_ms // 1000
        duration = status.duration_ms // 1000
        self.update_status(f"{status.state}  {name}  {elapsed}s/{duration}s")

    async def refresh_browser(self) -> None:
        browser = self.query_one("#browser", ListView)
        await browser.clear()
        self.browser_entries = await asyncio.to_thread(list_folder, self.browser_path, self.root)
        for entry in self.browser_entries:
            marker = "[x]" if entry.path in self.selected_paths else "[ ]"
            icon = "/" if entry.is_dir else marker if entry.supported else "[unsupported]"
            await browser.append(ListItem(Label(f"{icon} {entry.name}")))

    def refresh_queue(self) -> None:
        view = self.query_one("#queue", ListView)
        view.clear()
        current = self.queue.current()
        for entry in self.queue.entries():
            marker = ">" if current and current.id == entry.id else " "
            view.append(ListItem(Label(f"{marker} {entry.path.name} [{entry.state}]")))

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
