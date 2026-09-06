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

from .controller import PlaybackController, ResumeChoiceRequired, ResumeOffer
from .database import Database
from .models import BrowserEntry, HistoryProjection, QueueEntry
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


class QueueListItem(ListItem):
    """A queue row carrying its database identity independently of its label."""

    entry_id: int


class RootPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Label("Open library folder")
        yield Input(placeholder="/path/to/folder", id="root-input")
        with Horizontal(classes="dialog-actions"):
            yield Button("Open", id="root-open", variant="primary")
            yield Button("Cancel", id="root-cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss(event.value)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "root-open":
            self.dismiss(self.query_one("#root-input", Input).value)
        elif event.button.id == "root-cancel":
            self.dismiss(None)


class ResumePrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [
        Binding("escape", "cancel", "Cancel"),
        Binding("r", "resume", "Resume"),
        Binding("s", "start_over", "Start over"),
    ]

    def __init__(self, offer: ResumeOffer) -> None:
        super().__init__()
        self.offer = offer

    def compose(self) -> ComposeResult:
        label = "Resume from furthest recorded progress" if self.offer.legacy_fallback else "Resume"
        yield Label(f"{label}? Start over preserves history.")
        with Horizontal(classes="dialog-actions"):
            yield Button(label, id="resume-choice", variant="primary")
            yield Button("Start over", id="start-over-choice")
            yield Button("Cancel", id="resume-cancel")

    def action_resume(self) -> None:
        self.dismiss("resume")

    def action_start_over(self) -> None:
        self.dismiss("start_over")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choices = {
            "resume-choice": "resume",
            "start-over-choice": "start_over",
            "resume-cancel": None,
        }
        if event.button.id in choices:
            self.dismiss(choices[event.button.id])


class ConfirmClear(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Clear completed queue entries? Media files will not be changed.")
        with Horizontal(classes="dialog-actions"):
            yield Button("Clear", id="clear-confirm", variant="error")
            yield Button("Cancel", id="clear-cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clear-confirm":
            self.dismiss(True)
        elif event.button.id == "clear-cancel":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class ConfirmClearAll(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Clear every queue entry? Media files will not be changed.")
        with Horizontal(classes="dialog-actions"):
            yield Button("Clear all", id="clear-all-confirm", variant="error")
            yield Button("Cancel", id="clear-all-cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clear-all-confirm":
            self.dismiss(True)
        elif event.button.id == "clear-all-cancel":
            self.dismiss(False)

    def action_cancel(self) -> None:
        self.dismiss(False)


class QuitPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [
        Binding("s", "stop", "Stop VLC"),
        Binding("k", "keep", "Keep VLC"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Label("Quit: stop VLC, keep VLC running, or cancel")
        with Horizontal(classes="dialog-actions"):
            yield Button("Stop VLC", id="quit-stop", variant="warning")
            yield Button("Keep VLC", id="quit-keep")
            yield Button("Cancel", id="quit-cancel")

    def action_stop(self) -> None:
        self.dismiss("stop")

    def action_keep(self) -> None:
        self.dismiss("keep")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        choices = {"quit-stop": "stop", "quit-keep": "keep", "quit-cancel": None}
        if event.button.id in choices:
            self.dismiss(choices[event.button.id])


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
    .compact .toolbar { width: 100%; }
    .compact .toolbar Button { min-width: 6; width: auto; margin: 0; }
    .compact #browser-search { min-width: 8; width: 1fr; margin: 0; }
    .dialog-actions { height: auto; align-horizontal: center; }
    .dialog-actions Button { margin: 0 1; min-width: 12; }
    #browser-search { width: 1fr; margin: 0 1; }
    #selection-summary, #selected-details, #player { height: auto; padding: 0 1; color: $text-muted; }
    #player { color: $text; background: $boost; }
    .history-none { color: $text-muted; }
    .history-progress { color: $warning; }
    .history-completed { color: $success; }
    .queued-badge { color: $accent; }
    #browser, #queue {
        height: 1fr;
        overflow-x: auto;
        overflow-y: auto;
    }
    #browser > ListItem, #queue > ListItem {
        width: auto;
        min-width: 100%;
    }
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
    RootPrompt, ResumePrompt, ConfirmClear, ConfirmClearAll, QuitPrompt { align: center middle; }
    RootPrompt > Label, RootPrompt > Input, ResumePrompt > Label,
    ConfirmClear > Label, ConfirmClearAll > Label, QuitPrompt > Label {
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
        Binding("u", "undo", "Undo"),
        Binding("space", "pause", "Play/pause"),
        Binding("d,delete", "remove", "Remove"),
        Binding("J", "move_down", "Move down"),
        Binding("K", "move_up", "Move up"),
        Binding("n", "next", "Next"),
        Binding("p", "previous", "Previous"),
        Binding("left", "left", "Up/back", priority=True),
        Binding("right", "right", "Open/forward", priority=True),
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
        self.history: dict[Path, HistoryProjection] = {}
        self.search_query = ""
        self.history_filter = "all"
        self.browser_reverse = False
        self.controller = PlaybackController(self.queue)
        self.no_vlc = no_vlc
        self.autoplay = autoplay
        self.resume_command = False
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
                with Horizontal(classes="toolbar"):
                    yield Button("Add to end", id="browser-add", variant="success")
                    yield Button("Play next", id="browser-next")
                    yield Button("Play now", id="browser-add-play", variant="warning")
                    yield Button("Sort", id="browser-sort")
                with Horizontal(classes="toolbar"):
                    yield Input(placeholder="Search filenames", id="browser-search")
                    yield Button("All", id="filter-all")
                    yield Button("In progress", id="filter-progress")
                    yield Button("Not completed", id="filter-not-completed")
                    yield Button("Clear selection", id="browser-clear-selection")
                yield Static(
                    "Click a row to highlight; click its checkbox to select. Add actions never modify media.",
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
                    yield Button("Add to end", id="queue-add", variant="success")
                    yield Button("Play next", id="queue-next")
                    yield Button("Play now", id="queue-play", variant="warning")
                with Horizontal(classes="toolbar"):
                    yield Button("Up", id="queue-up")
                    yield Button("Down", id="queue-down")
                    yield Button("Remove", id="queue-remove", variant="error")
                    yield Button("Undo", id="queue-undo")
                with Horizontal(classes="toolbar"):
                    yield Button("Sort", id="queue-sort")
                    yield Button("Clear", id="queue-clear", variant="error")
                yield Static(
                    "Queue is empty — highlight a playable video and press Add or A.",
                    id="queue-empty",
                    classes="empty-state",
                )
                yield ListView(id="queue")
        yield Static("No selection", id="selection-summary")
        yield Static("No item highlighted", id="selected-details")
        with Horizontal(id="player-controls", classes="toolbar"):
            yield Button("Previous", id="player-previous")
            yield Button("-10s", id="player-back")
            yield Button("Pause", id="player-pause", variant="primary")
            yield Button("+10s", id="player-forward")
            yield Button("Next", id="player-next")
            yield Button("Reconnect", id="player-reconnect")
            yield Button("Help", id="app-help")
            yield Button("Quit", id="app-quit", variant="error")
        yield Static("Player disconnected", id="player")
        yield ProgressBar(total=100, id="progress")
        yield Footer()

    async def on_mount(self) -> None:
        if self.size.width <= 90:
            self.add_class("compact")
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
        if (self.autoplay or self.resume_command) and self.queue.entries():
            if self.no_vlc:
                self.queue.play_now(0)
            elif self.controller.client is not None:
                try:
                    if self.resume_command:
                        await self._play_queue_index(0)
                    else:
                        await self.controller.play_with_policy(0, automatic=True)
                except (IndexError, OSError, VLCError):
                    self.update_status("Automatic resume failed")
            self.refresh_queue()
        self.set_interval(1, self.refresh_playback)

    async def on_unmount(self) -> None:
        # Poll failures retire the client reference, but the VLC process may
        # still be alive and must be stopped when the app exits.
        if not self.no_vlc:
            await self.controller.stop()

    def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if not isinstance(widget, ProgressBar):
            if widget is not None and any(
                isinstance(ancestor, ListItem) for ancestor in widget.ancestors_with_self
            ):
                entry = self._browser_entry()
                if entry is not None and entry.is_dir:
                    self.run_worker(self.action_activate(), exclusive=True)
            return
        if self.no_vlc or self.controller.client is None:
            self.update_status("Seek unavailable while VLC is disconnected")
            return
        current = self.queue.current()
        duration = self.controller.status.duration_ms
        if current is None or duration <= 0:
            self.update_status("Seek unavailable because duration is unknown")
            return
        region = widget.region
        screen_x = getattr(event, "screen_x", region.x)
        ratio = max(0.0, min(1.0, (screen_x - region.x) / max(1, region.width - 1)))
        target = int(duration * ratio)
        self.run_worker(self._seek_track(target, duration), exclusive=True)

    async def _seek_track(self, target_ms: int, duration_ms: int) -> None:
        try:
            await self.controller.seek_absolute(target_ms, duration_ms)
        except (OSError, VLCError) as exc:
            self.update_status(f"Seek failed: {exc}")
        else:
            self.update_status(f"Seeked to {self._format_time(target_ms)}")

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
        self._refresh_details()
        self._refresh_controls()

    async def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "browser-search":
            await self.refresh_browser()

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

    @staticmethod
    def _format_time(value_ms: int | None) -> str:
        if value_ms is None or value_ms < 0:
            return "?"
        total = value_ms // 1000
        seconds = total % 60
        minutes = (total // 60) % 60
        hours = total // 3600
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    def _history_for(self, paths: list[Path]) -> dict[Path, HistoryProjection]:
        try:
            return self.database.history_for_paths(paths, root=self.root)
        except (OSError, PathError, RuntimeError):
            return {}

    def _history_label(self, path: Path) -> str:
        history = self.history.get(path)
        if history is None:
            return "No recorded progress"
        if history.completion_observed:
            category = "Completed"
        elif history.position_ms > 0:
            category = "In progress"
        else:
            return "No recorded progress"
        progress = self._format_time(history.position_ms)
        duration = self._format_time(history.duration_ms) if history.duration_ms > 0 else "?"
        return f"{category} {progress}/{duration}"

    def _refresh_selection_summary(self) -> None:
        try:
            summary = self.query_one("#selection-summary", Static)
        except NoMatches:
            return
        visible = {entry.path for entry in self.browser_entries if entry.supported}
        hidden = self.selected_paths - visible
        other_folder = sum(1 for path in hidden if path.parent != self.browser_path)
        if not self.selected_paths:
            summary.update("No selected videos")
        else:
            summary.update(
                f"Selected: {len(self.selected_paths)} · hidden by filter/folder: {len(hidden)} "
                f"({other_folder} in other folders)"
            )

    def _refresh_details(self) -> None:
        try:
            details = self.query_one("#selected-details", Static)
        except NoMatches:
            return
        entry = self._browser_entry()
        if entry is None or entry.is_dir:
            details.update("No video highlighted")
            return
        history = self.history.get(entry.path)
        queued = next((item for item in self.queue.entries() if item.path == entry.path), None)
        queue_label = "queued" if queued is not None else "not queued"
        if history is None:
            details.update(f"{entry.name} · {queue_label} · No recorded progress")
            return
        last_played = history.last_played_at or "unknown last-played time"
        resume = (
            self._format_time(history.resume_position_ms)
            if history.resume_position_ms is not None
            else (
                f"{self._format_time(history.fallback_resume_position_ms)} "
                "(furthest recorded fallback)"
                if history.fallback_resume_position_ms is not None
                else "unknown"
            )
        )
        details.update(
            f"{entry.name} · {queue_label} · resume {resume} · furthest "
            f"{self._format_time(history.position_ms)} · duration "
            f"{self._format_time(history.duration_ms) if history.duration_ms else '?'} · "
            f"last played {last_played}"
        )

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
        remaining = max(0, duration_ms - position_ms) if duration_ms > 0 else None
        self._playback_summary = (
            f"{status_name}  {name}  elapsed {self._format_time(position_ms)} / "
            f"total {self._format_time(duration_ms) if duration_ms else '?'} / "
            f"remaining {self._format_time(remaining)}"
        )
        try:
            self.query_one("#player", Static).update(
                f"{status_name.upper()} · {name} · elapsed {self._format_time(position_ms)} · "
                f"total {self._format_time(duration_ms) if duration_ms else '?'} · "
                f"remaining {self._format_time(remaining)} · "
                f"{'connected' if self.controller.client is not None or self.no_vlc else 'disconnected'}"
            )
        except NoMatches:
            pass
        self._render_status()
        self._refresh_browser_history()
        # Controller observations update SQLite; redraw rows even when no user input occurs.
        try:
            self.refresh_queue()
        except NoMatches:
            # A timer can tick while Textual is tearing down the application.
            pass

    def _refresh_browser_history(self) -> None:
        """Refresh history labels in place without rebuilding browser rows."""
        try:
            view = self.query_one("#browser", ListView)
        except NoMatches:
            return
        paths = [entry.path for entry in self.browser_entries if entry.supported]
        refreshed = self._history_for(paths)
        for path in paths:
            self.history.pop(path, None)
        self.history.update(refreshed)
        for entry, row in zip(self.browser_entries, view.children, strict=False):
            if not entry.supported:
                continue
            try:
                row.query_one(Static).update(self._history_label(entry.path))
            except NoMatches:
                continue
        self._refresh_details()

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
        self.history = self._history_for([entry.path for entry in entries if entry.supported])
        try:
            self.search_query = self.query_one("#browser-search", Input).value.casefold()
        except NoMatches:
            self.search_query = ""
        if self.search_query or self.history_filter != "all":
            filtered: list[BrowserEntry] = []
            for entry in entries:
                if entry.is_dir:
                    filtered.append(entry)
                    continue
                if self.search_query and self.search_query not in entry.name.casefold():
                    continue
                history = self.history.get(entry.path)
                if self.history_filter == "progress" and not (
                    history is not None and history.position_ms > 0 and not history.completion_observed
                ):
                    continue
                if self.history_filter == "not-completed" and history is not None and history.completion_observed:
                    continue
                filtered.append(entry)
            entries = filtered
        self.browser_entries = entries
        empty = self.query_one("#browser-empty", Static)
        empty.display = not bool(entries)
        if not entries:
            empty.update("No folders or playable videos match the current view.")
        queued_paths = {entry.path for entry in self.queue.entries()}
        for index, entry in enumerate(self.browser_entries):
            selected = entry.path in self.selected_paths
            marker = "[x]" if selected else "[ ]"
            icon = "▸" if entry.is_dir else marker
            classes = "folder-entry" if entry.is_dir else "video-entry"
            if selected:
                classes += " selected-video"
            label = Label(f"{icon} {entry.name}")
            if entry.is_dir:
                await browser.append(ListItem(label, classes=classes))
            else:
                queued = " · QUEUED" if entry.path in queued_paths else ""
                history_class = (
                    "history-completed"
                    if self.history.get(entry.path, None) is not None
                    and self.history[entry.path].completion_observed
                    else "history-progress"
                    if self.history.get(entry.path, None) is not None
                    and self.history[entry.path].position_ms > 0
                    else "history-none"
                )
                await browser.append(
                    ListItem(
                        label,
                        Button("☑" if selected else "☐", id=f"browser-check-{index}"),
                        Static(f"{self._history_label(entry.path)}{queued}", classes=history_class),
                        classes=classes,
                    )
                )
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
        self._refresh_selection_summary()
        self._refresh_details()
        self._refresh_controls()

    @staticmethod
    def _queue_state_class(state: str) -> str:
        return {
            "queued": "queue-queued",
            "playing": "queue-playing",
            "paused": "queue-paused",
            "stopped": "queue-stopped",
            "skipped": "queue-skipped",
            "completed": "queue-completed",
            "missing": "queue-missing",
            "failed": "queue-failed",
        }.get(state, "queue-failed")

    def _update_queue_row(
        self, row: QueueListItem, entry: QueueEntry, current_id: int | None
    ) -> None:
        is_current = current_id == entry.id
        state_label = _QUEUE_STATE_LABELS.get(entry.state, f"? {entry.state.upper()}")
        marker = "◆" if is_current else " "
        row.query_one(Label).update(f"{marker} {entry.path.name} — {state_label}")
        try:
            row.query_one(".queue-history", Static).update(self._history_label(entry.path))
        except NoMatches:
            pass
        row.set_classes(self._queue_state_class(entry.state))

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
        entries = self.queue.entries()
        queue_paths = [entry.path for entry in entries]
        refreshed_history = self._history_for(queue_paths)
        for path in queue_paths:
            self.history.pop(path, None)
        self.history.update(refreshed_history)
        current = self.queue.current()
        current_id = current.id if current is not None else None
        empty = self.query_one("#queue-empty", Static)
        empty.display = not bool(entries)

        # Polling changes row state frequently, but do not replace widgets when
        # queue identity and order are unchanged. Replacing only for a
        # structural change keeps highlight and scroll state stable while the
        # status label and CSS class update in place.
        rows = list(view.children)
        reuse_rows = len(rows) == len(entries) and all(
            isinstance(row, QueueListItem) and row.entry_id == entry.id
            for row, entry in zip(rows, entries)
        )
        if reuse_rows:
            for row, entry in zip(rows, entries, strict=True):
                assert isinstance(row, QueueListItem)
                self._update_queue_row(row, entry, current_id)
        else:
            view.clear()
            for entry in entries:
                state_label = _QUEUE_STATE_LABELS.get(entry.state, f"? {entry.state.upper()}")
                marker = "◆" if current_id == entry.id else " "
                row = QueueListItem(
                    Label(f"{marker} {entry.path.name} — {state_label}"),
                    Static(self._history_label(entry.path), classes="queue-history"),
                    classes=self._queue_state_class(entry.state),
                )
                row.entry_id = entry.id
                view.append(row)
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
            for button_id in ("#queue-add", "#browser-add", "#browser-next", "#browser-add-play"):
                self.query_one(button_id, Button).disabled = not can_add
            self.query_one("#browser-clear-selection", Button).disabled = not bool(self.selected_paths)
            queue_entries = self.queue.entries()
            queue_view = self.query_one("#queue", ListView)
            self.query_one("#queue-play", Button).disabled = not (
                bool(queue_entries) and queue_view.index is not None
            )
            self.query_one("#queue-clear", Button).disabled = not bool(queue_entries)
            self.query_one("#queue-next", Button).disabled = self._queue_selected_path() is None
            self.query_one("#queue-remove", Button).disabled = not (
                bool(queue_entries) and queue_view.index is not None
            )
            self.query_one("#queue-up", Button).disabled = not (
                bool(queue_entries) and queue_view.index is not None
            )
            self.query_one("#queue-down", Button).disabled = not (
                bool(queue_entries) and queue_view.index is not None
            )
            self.query_one("#queue-undo", Button).disabled = not self.queue.undo_available
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

    def _require_vlc_for_play(self) -> bool:
        if not self.no_vlc and self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return False
        return True

    def _resume_offer_for_path(self, path: Path) -> ResumeOffer:
        return ResumeOffer(self.database.history_for(path, root=self.root))

    def _offer_requires_choice(self, offer: ResumeOffer) -> bool:
        return not offer.completed and (offer.usable_resume or offer.legacy_fallback)

    async def _play_queue_index(self, index: int, choice: str | None = None) -> bool:
        if self.no_vlc:
            self.queue.play_now(index)
        else:
            if not self._require_vlc_for_play():
                return False
            try:
                await self.controller.play_with_policy(index, choice=choice)
            except ResumeChoiceRequired as exc:
                def chosen(value: str | None) -> None:
                    if value is not None:
                        self.run_worker(
                            self._play_queue_index(index, choice=value), exclusive=True
                        )

                self.push_screen(ResumePrompt(exc.offer), chosen)
                return False
        return True

    async def _activate_browser_video(self, entry: BrowserEntry, choice: str | None = None) -> None:
        if not self._require_vlc_for_play():
            return
        offer = self._resume_offer_for_path(entry.path)
        if choice is None and self._offer_requires_choice(offer):
            def chosen(value: str | None) -> None:
                if value is not None:
                    self.run_worker(
                        self._activate_browser_video(entry, choice=value), exclusive=True
                    )

            self.push_screen(ResumePrompt(offer), chosen)
            return
        try:
            self.queue.add([entry.path])
            index = self._queue_path_index(entry.path)
            if index is None:
                raise RuntimeError("video was not added to the queue")
            played = await self._play_queue_index(index, choice=choice)
        except (IndexError, OSError, PathError, RuntimeError, VLCError) as exc:
            self.update_status(f"Play failed: {exc}")
            self.refresh_queue()
            return
        if not played:
            self.refresh_queue()
            return
        self.selected_paths.discard(entry.path)
        await self.refresh_browser()
        self.update_status(f"Playing {entry.path.name}")
        self.refresh_queue()

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
                played = await self._play_queue_index(index)
            except (IndexError, VLCError, OSError) as exc:
                self.update_status(f"Play failed: {exc}")
            else:
                if played:
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
        await self._activate_browser_video(entry)

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

    async def action_play_next_selected(self) -> None:
        paths = self._paths_for_add()
        if not paths:
            self.update_status("Nothing to place next — select or highlight a video")
            return
        try:
            self.queue.play_next(paths)
        except (OSError, PathError, RuntimeError, ValueError) as exc:
            self.update_status(f"Play next failed: {exc}")
            return
        self.selected_paths.clear()
        await self.refresh_browser()
        self.refresh_queue()
        self.update_status(f"Placed {len(paths)} video{'s' if len(paths) != 1 else ''} next")

    async def action_clear_selection(self) -> None:
        self.selected_paths.clear()
        await self.refresh_browser()
        self.update_status("Selection cleared")

    async def action_undo(self) -> None:
        try:
            restored = self.queue.undo()
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status(f"Undo refused: {exc}")
        else:
            self.update_status("Removal undone" if restored else "Undo expired")
        self.refresh_queue()

    async def _finish_add_and_play(self, paths: list[Path], choice: str | None = None) -> None:
        target = paths[0]
        try:
            self.queue.add(paths)
            index = self._queue_path_index(target)
            if index is None:
                raise RuntimeError("video was not added to the queue")
            played = await self._play_queue_index(index, choice=choice)
        except (IndexError, OSError, PathError, RuntimeError, VLCError) as exc:
            self.update_status(f"Add and play failed: {exc}")
            self.refresh_queue()
            return
        if not played:
            self.refresh_queue()
            return
        self.selected_paths.clear()
        await self.refresh_browser()
        self.refresh_queue()
        self.update_status(f"Playing {target.name}")

    async def action_add_and_play(self) -> None:
        paths = self._paths_for_add()
        if not paths:
            self.update_status("Nothing to play — highlight a playable video or press v to select")
            return
        if not self._require_vlc_for_play():
            return
        offer = self._resume_offer_for_path(paths[0])
        if self._offer_requires_choice(offer):
            def chosen(value: str | None) -> None:
                if value is not None:
                    self.run_worker(
                        self._finish_add_and_play(paths, choice=value), exclusive=True
                    )

            self.push_screen(ResumePrompt(offer), chosen)
            return
        await self._finish_add_and_play(paths)

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

    async def action_remove(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Remove is available in the queue pane")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        try:
            entry = self.queue.entries()[index]
            if (
                not self.no_vlc
                and entry.state in {"playing", "paused"}
                and not await self.controller.stop_playback()
            ):
                self.update_status("Remove blocked: VLC stop could not be confirmed")
                return
            self.queue.remove(index, stop_confirmed=self.no_vlc or entry.state in {"playing", "paused"})
        except (IndexError, OSError, RuntimeError, ValueError) as exc:
            self.update_status(f"Remove failed: {exc}")
        else:
            self.update_status(f"Removed {entry.path.name}; media was not changed · Undo available")
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

    async def _perform_clear_all(self) -> None:
        try:
            current = self.queue.current()
            if (
                current is not None
                and current.state in {"playing", "paused"}
                and not self.no_vlc
                and not await self.controller.stop_playback()
            ):
                self.update_status("Clear blocked: VLC stop could not be confirmed")
                return
            self.queue.clear_all(stop_confirmed=True)
        except (OSError, RuntimeError, ValueError) as exc:
            self.update_status(f"Clear failed: {exc}")
            return
        self.refresh_queue()
        self.update_status("Queue cleared; media files were not changed · Undo available")

    def action_clear_all(self) -> None:
        if not self.queue.entries():
            self.update_status("Queue is already empty")
            return

        def confirmed(value: bool | None) -> None:
            if not value:
                self.update_status("Clear cancelled")
                return
            self.run_worker(self._perform_clear_all(), exclusive=True)

        self.push_screen(ConfirmClearAll(), confirmed)

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id is None:
            return
        if button_id.startswith("browser-check-"):
            try:
                row_index = int(button_id.rsplit("-", 1)[1])
                view = self.query_one("#browser", ListView)
                view.index = row_index
                await self.action_select()
            except (ValueError, IndexError):
                self.update_status("Selection target is no longer visible")
        elif button_id == "browser-open":
            self.action_open_root()
        elif button_id == "browser-up":
            await self.action_parent()
        elif button_id == "browser-select":
            await self.action_select()
        elif button_id == "browser-add":
            await self.action_add_selected()
        elif button_id == "browser-next":
            await self.action_play_next_selected()
        elif button_id == "browser-add-play":
            await self.action_add_and_play()
        elif button_id == "browser-clear-selection":
            await self.action_clear_selection()
        elif button_id in {"filter-all", "filter-progress", "filter-not-completed"}:
            self.history_filter = {
                "filter-all": "all",
                "filter-progress": "progress",
                "filter-not-completed": "not-completed",
            }[button_id]
            await self.refresh_browser()
        elif button_id == "browser-sort":
            self.browser_reverse = not self.browser_reverse
            await self.refresh_browser()
        elif button_id == "queue-add":
            await self.action_add_selected()
        elif button_id == "queue-next":
            path = self._queue_selected_path()
            if path is None:
                self.update_status("Nothing is highlighted in the queue")
            else:
                try:
                    self.queue.play_next([path])
                except (OSError, PathError, RuntimeError, ValueError) as exc:
                    self.update_status(f"Play next failed: {exc}")
                else:
                    self.refresh_queue(path, selection_captured=True)
                    self.update_status("Highlighted queue item placed next")
        elif button_id == "queue-play":
            index = self._queue_index()
            if index is None:
                self.update_status("Nothing is highlighted in the queue")
                return
            try:
                played = await self._play_queue_index(index)
            except (IndexError, OSError, VLCError) as exc:
                self.update_status(f"Play failed: {exc}")
            else:
                if played:
                    self.update_status("Playing highlighted queue item")
            self.refresh_queue()
        elif button_id == "queue-up":
            self.action_move_up()
        elif button_id == "queue-down":
            self.action_move_down()
        elif button_id == "queue-remove":
            await self.action_remove()
        elif button_id == "queue-undo":
            await self.action_undo()
        elif button_id == "queue-sort":
            selected_path = self._queue_selected_path()
            self.queue.sort_natural()
            self.refresh_queue(selected_path, selection_captured=True)
            self.update_status("Queue sorted naturally")
        elif button_id == "queue-clear":
            self.action_clear_all()
        elif button_id == "player-pause":
            await self.action_pause()
        elif button_id == "player-back":
            await self.action_seek(-10)
        elif button_id == "player-forward":
            await self.action_seek(10)
        elif button_id == "player-next":
            await self.action_next()
        elif button_id == "player-previous":
            await self.action_previous()
        elif button_id == "player-reconnect":
            if self.no_vlc:
                self.update_status("VLC controls are disabled in offline mode")
            else:
                try:
                    await self.controller.reconnect()
                except (OSError, VLCError) as exc:
                    self.update_status(f"Reconnect failed: {exc}")
                else:
                    self.update_status("VLC reconnected; playback was not restarted")
        elif button_id == "app-help":
            self.action_help()
        elif button_id == "app-quit":
            self.action_quit_app()

    def action_clear_completed(self) -> None:
        if not any(entry.state == "completed" for entry in self.queue.entries()):
            self.update_status("No completed queue entries to clear")
            return

        def confirmed(value: bool | None) -> None:
            if not value:
                self.update_status("Clear cancelled")
                return
            async def clear() -> None:
                try:
                    current = self.queue.current()
                    if (
                        current is not None
                        and current.state in {"playing", "paused"}
                        and not self.no_vlc
                        and not await self.controller.stop_playback()
                    ):
                        self.update_status("Clear blocked: VLC stop could not be confirmed")
                        return
                    self.queue.clear_completed(stop_confirmed=True)
                except (OSError, RuntimeError, ValueError) as exc:
                    self.update_status(f"Clear failed: {exc}")
                    return
                self.refresh_queue()
                self.update_status("Completed queue entries cleared · Undo available")

            self.run_worker(clear(), exclusive=True)

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
