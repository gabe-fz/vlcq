from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar, Literal

from rich.text import Text
from textual import events
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.css.query import NoMatches
from textual.screen import ModalScreen
from textual.widget import Widget
from textual.widgets import Button, Input, Label, ListItem, ListView, ProgressBar, Static

from .config import resolve_watched_percent
from .controller import PlaybackController, ResumeChoiceRequired, ResumeOffer
from .database import Database
from .models import BrowserEntry, HistoryProjection, QueueEntry
from .paths import VIDEO_EXTENSIONS, PathError, canonical_root, is_beneath, list_folder, natural_key
from .progress import clamped_percentage
from .queue import QueueService
from .vlc import VLCError

_QUEUE_STATE_LABELS: dict[str, str] = {
    "playing": "▶ PLAYING",
    "paused": "Ⅱ PAUSED",
    "stopped": "■ STOPPED",
    "skipped": "→ SKIPPED",
    "completed": "✓ COMPLETED",
    "missing": "! MISSING",
    "failed": "× FAILED",
}
_TRANSIENT_QUEUE_STATES = frozenset({"playing", "paused", "stopped"})


def _file_identity(
    raw_path: Path, root: Path
) -> tuple[Path, tuple[int, int, int, int]] | None:
    """Resolve and stat one browser path away from the Textual event loop."""
    try:
        path = raw_path.expanduser().resolve(strict=False)
        if not is_beneath(path, root) or not path.is_file():
            return None
        stat = path.stat()
    except (OSError, RuntimeError):
        return None
    return path, (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)


@dataclass(frozen=True)
class FileTarget:
    path: Path
    root_generation: int
    selected_paths: tuple[Path, ...] = ()


@dataclass(frozen=True)
class QueueTarget:
    entry_id: int
    root_generation: int


@dataclass(frozen=True)
class SectionTarget:
    section: Literal["files", "queue"]
    root_generation: int


@dataclass(frozen=True)
class PlayerTarget:
    path: Path | None
    root_generation: int


type ContextTarget = FileTarget | QueueTarget | SectionTarget | PlayerTarget


@dataclass(frozen=True)
class ContextAction:
    key: str
    label: str
    target: ContextTarget
    enabled: bool = True


class CompactButton(Button):
    """A one-cell-friendly button without a delay between fast mouse clicks."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.active_effect_duration = 0


def render_filename(name: str, *, folder: bool = False, depth: int = 0, expanded: bool = False) -> Text:
    """Render literal filename syntax as semantic Rich spans.

    No filename is parsed for episode/show meaning: the returned text is the
    original name with only presentation styles attached to its spans.
    """
    text = Text(no_wrap=True, overflow="ellipsis")
    prefix = "  " * max(0, depth)
    if folder:
        text.append(prefix + ("▾ " if expanded else "▸ "), style="bold cyan")
        text.append(name, style="bold cyan")
        return text

    suffix = Path(name).suffix
    stem = name[: -len(suffix)] if suffix else name
    text.append(prefix)
    index = 0
    bracket = False
    while index < len(stem):
        character = stem[index]
        if character == "[":
            bracket = True
            text.append(character, style="bold magenta")
        elif character == "]" and bracket:
            bracket = False
            text.append(character, style="bold magenta")
        elif bracket:
            text.append(character, style="magenta")
        elif character.isdigit():
            end = index + 1
            while end < len(stem) and stem[end].isdigit():
                end += 1
            text.append(stem[index:end], style="bold yellow")
            index = end - 1
        elif not character.isalnum() and character != "_":
            text.append(character, style="bright_black")
        else:
            text.append(character, style="white")
        index += 1
    if suffix:
        text.append(suffix, style="bold green")
    return text


class ItemProgress(Static):
    """Fixed-width, read-only history progress attached to one item row."""

    BAR_WIDTH = 8

    def __init__(
        self,
        percentage: int | None,
        watched: bool,
        *,
        visible: bool = False,
        id: str | None = None,
    ) -> None:
        super().__init__(id=id, classes="history-bar")
        self.percentage = percentage
        self.watched = watched
        self.display = visible or percentage is not None or watched
        self._render_progress()

    def set_progress(
        self, percentage: int | None, watched: bool, *, visible: bool = False
    ) -> None:
        self.percentage = percentage
        self.watched = watched
        self.display = visible or percentage is not None or watched
        self._render_progress()

    def _render_progress(self) -> None:
        if not self.display:
            self.update("")
            return
        if self.percentage is None:
            bar = "?" * self.BAR_WIDTH
            value = "?%"
            style = "green" if self.watched else "yellow"
        else:
            filled = round(self.BAR_WIDTH * self.percentage / 100)
            bar = "█" * filled + "░" * (self.BAR_WIDTH - filled)
            value = f"{self.percentage}%"
            style = "green" if self.watched else "yellow"
        self.update(Text(f" {bar} {value:>4}", style=style, no_wrap=True))


class BrowserListItem(ListItem):
    path: Path
    is_dir: bool
    depth: int

    def __init__(
        self,
        entry: BrowserEntry,
        renderable: Text,
        selected: bool = False,
        *,
        depth: int = 0,
        history_percentage: int | None = None,
        history_watched: bool = False,
        history_visible: bool = False,
    ) -> None:
        self.path = entry.path
        self.is_dir = entry.is_dir
        self.depth = depth
        classes = "folder-entry" if entry.is_dir else "video-entry"
        if selected:
            classes += " selected-video"
        if entry.is_dir:
            super().__init__(Label(renderable, classes="row-label", markup=False), classes=classes)
        else:
            marker = "☑" if selected else "☐"
            super().__init__(
                Horizontal(
                    CompactButton(
                        marker,
                        classes="browser-check",
                        tooltip="Deselect video" if selected else "Select video",
                    ),
                    Label(renderable, classes="row-label", markup=False),
                    ItemProgress(
                        history_percentage, history_watched, visible=history_visible
                    ),
                    classes="browser-row",
                ),
                classes=classes,
            )


class QueueListItem(ListItem):
    """A queue row carrying its database identity independently of its label."""

    entry_id: int

    def __init__(
        self,
        entry: QueueEntry,
        current_id: int | None,
        selected_id: int | None,
        renderable: Text,
        *,
        history_percentage: int | None = None,
        history_watched: bool = False,
        history_visible: bool = False,
    ) -> None:
        self.entry_id = entry.id
        super().__init__(
            Horizontal(
                Label(renderable, classes="row-label", markup=False),
                ItemProgress(
                    history_percentage, history_watched, visible=history_visible
                ),
                classes="queue-row",
            )
        )
        display_state = (
            entry.state
            if current_id == entry.id or entry.state not in _TRANSIENT_QUEUE_STATES
            else "queued"
        )
        classes = VLCQApp._queue_state_class(display_state)
        if selected_id == entry.id:
            classes += " queue-selected"
        self.set_classes(classes)


class RootPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def compose(self) -> ComposeResult:
        yield Static("Open library folder", classes="dialog-title")
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


class SearchFilterPrompt(ModalScreen[tuple[str, str] | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Cancel")]

    def __init__(self, query: str, history_filter: str) -> None:
        super().__init__()
        self.initial_query = query
        self.history_filter = history_filter

    def compose(self) -> ComposeResult:
        yield Static("Search and filter Files", classes="dialog-title")
        yield Input(value=self.initial_query, placeholder="filename contains…", id="search-input")
        with Horizontal(classes="dialog-actions search-filters"):
            yield Button("All", id="search-filter-all")
            yield Button("In progress", id="search-filter-progress")
            yield Button("Not watched", id="search-filter-not-completed")
        with Horizontal(classes="dialog-actions"):
            yield Button("Apply", id="search-apply", variant="primary")
            yield Button("Clear", id="search-clear")
            yield Button("Cancel", id="search-cancel")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.dismiss((event.value, self.history_filter))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        button_id = event.button.id
        if button_id in {"search-filter-all", "search-filter-progress", "search-filter-not-completed"}:
            self.history_filter = {
                "search-filter-all": "all",
                "search-filter-progress": "progress",
                "search-filter-not-completed": "not-completed",
            }[button_id]
        elif button_id == "search-apply":
            self.dismiss((self.query_one("#search-input", Input).value, self.history_filter))
        elif button_id == "search-clear":
            self.dismiss(("", "all"))
        elif button_id == "search-cancel":
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
        yield Static(f"{label}? Start over preserves history.", classes="dialog-title")
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
        choices: dict[str, str | None] = {
            "resume-choice": "resume",
            "start-over-choice": "start_over",
            "resume-cancel": None,
        }
        if event.button.id in choices:
            self.dismiss(choices[event.button.id])


class DetailsPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Close")]

    def __init__(self, text: str, can_resume: bool, can_start_over: bool) -> None:
        super().__init__()
        self.details_text = text
        self.can_resume = can_resume
        self.can_start_over = can_start_over

    def compose(self) -> ComposeResult:
        yield Static(self.details_text, id="details-content", classes="details-content", markup=False)
        with Horizontal(classes="dialog-actions"):
            yield Button("Resume", id="details-resume", variant="primary", disabled=not self.can_resume)
            yield Button("Start over", id="details-start-over", disabled=not self.can_start_over)
            yield Button("Close", id="details-close")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "details-resume" and self.can_resume:
            self.dismiss("resume")
        elif event.button.id == "details-start-over" and self.can_start_over:
            self.dismiss("start_over")
        elif event.button.id == "details-close":
            self.dismiss(None)


class ActionMenu(ModalScreen[ContextAction | None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Close")]

    def __init__(self, actions: list[ContextAction], source: Widget | None, x: int, y: int) -> None:
        super().__init__()
        self.actions = actions
        self.source = source
        self.menu_x = x
        self.menu_y = y

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="context-menu"):
            for index, action in enumerate(self.actions):
                yield Button(
                    action.label,
                    id=f"context-action-{index}",
                    disabled=not action.enabled,
                    classes="context-action",
                )

    def on_mount(self) -> None:
        menu = self.query_one("#context-menu")
        menu_width = min(40, max(24, self.app.size.width - 2))
        menu_height = min(max(4, len(self.actions) + 2), max(5, self.app.size.height - 2))
        menu.styles.width = menu_width
        menu.styles.height = menu_height
        menu.styles.offset = (
            max(0, min(self.menu_x, self.app.size.width - menu_width)),
            max(0, min(self.menu_y, self.app.size.height - menu_height)),
        )
        first = next((button for button, action in zip(menu.query(Button), self.actions) if action.enabled), None)
        if first is not None:
            first.focus()

    def on_click(self, event: events.Click) -> None:
        if (
            self.source is not None
            and event.screen_x is not None
            and event.screen_y is not None
            and self.source.region.contains(event.screen_x, event.screen_y)
        ):
            return
        menu = self.query_one("#context-menu")
        if event.widget is None or menu not in event.widget.ancestors_with_self:
            self.dismiss(None)

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.disabled or event.button.id is None:
            return
        try:
            index = int(event.button.id.rsplit("-", 1)[1])
            self.dismiss(self.actions[index])
        except (IndexError, ValueError):
            self.dismiss(None)


class ConfirmClear(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Clear watched/completed queue entries? Media files will not be changed.", classes="dialog-title")
        with Horizontal(classes="dialog-actions"):
            yield Button("Clear", id="clear-confirm", variant="error")
            yield Button("Cancel", id="clear-cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clear-confirm":
            self.dismiss(True)
        elif event.button.id == "clear-cancel":
            self.dismiss(False)


class ConfirmClearAll(ModalScreen[bool]):
    BINDINGS: ClassVar = [
        Binding("y", "confirm", "Yes"),
        Binding("n,escape", "cancel", "No"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Clear every queue entry? Media files will not be changed.", classes="dialog-title")
        with Horizontal(classes="dialog-actions"):
            yield Button("Clear all", id="clear-all-confirm", variant="error")
            yield Button("Cancel", id="clear-all-cancel")

    def action_confirm(self) -> None:
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "clear-all-confirm":
            self.dismiss(True)
        elif event.button.id == "clear-all-cancel":
            self.dismiss(False)


class QuitPrompt(ModalScreen[str | None]):
    BINDINGS: ClassVar = [
        Binding("s", "stop", "Stop VLC"),
        Binding("k", "keep", "Keep VLC"),
        Binding("escape", "cancel", "Cancel"),
    ]

    def compose(self) -> ComposeResult:
        yield Static("Quit: stop VLC, keep VLC running, or cancel", classes="dialog-title")
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
        choices: dict[str, str | None] = {
            "quit-stop": "stop",
            "quit-keep": "keep",
            "quit-cancel": None,
        }
        if event.button.id in choices:
            self.dismiss(choices[event.button.id])


class NoticePrompt(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Close")]

    def __init__(self, notice: str) -> None:
        super().__init__()
        self.notice = notice

    def compose(self) -> ComposeResult:
        yield Static(self.notice, id="details-content", classes="details-content", markup=False)
        yield Button("Close", id="notice-close")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "notice-close":
            self.dismiss(None)


class HelpPrompt(ModalScreen[None]):
    BINDINGS: ClassVar = [Binding("escape", "cancel", "Close")]

    def compose(self) -> ComposeResult:
        yield Static(
            "Keys: o open · arrows/backspace browse · Enter play/open · v select · "
            "a add · A add & play · Space pause · n/p next/previous · [ ] seek · "
            "d remove · J/K reorder · r retry · c clear watched · ? help · q quit · Shift+F10 menu. "
            "Files and Queue headers and the player bar also expose frequent mouse actions; use … for more.",
            classes="help-content",
            markup=False,
        )
        yield Button("Close", id="help-close")

    def action_cancel(self) -> None:
        self.dismiss(None)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "help-close":
            self.dismiss(None)


class VLCQApp(App[None]):
    TITLE = "vlcq"
    CSS = """
    #sections { height: 1fr; width: 1fr; }
    .section { height: 1fr; min-height: 1; background: $surface; }
    #queue-section { background: $surface-darken-1; }
    .section.collapsed { height: 1; min-height: 1; }
    .section.focused { background: $primary-background; }
    #queue-section.focused { background: $secondary-background; }
    .section-header { height: 1; min-height: 1; width: 1fr; }
    .section-header Button, #player-actions Button {
        width: auto; min-width: 3; height: 1; min-height: 1; margin: 0; padding: 0 1;
        border: none; content-align: center middle;
    }
    #files-toggle, #queue-toggle, #files-actions, #queue-actions, #player-menu { width: 3; min-width: 3; padding: 0; }
    #files-open, #files-search, #files-add, #queue-play, #queue-next, #queue-clear,
    #player-previous, #player-pause, #player-next { min-width: 5; }
    .section-title { width: 1fr; height: 1; overflow-x: hidden; }
    .section-body { height: 1fr; min-height: 1; }
    .section.collapsed .section-body { display: none; }
    #browser, #queue { height: 1fr; min-height: 1; overflow-x: auto; overflow-y: auto; }
    #browser > ListItem, #queue > ListItem { width: auto; min-width: 100%; height: 1; min-height: 1; }
    .browser-row, .queue-row { width: 1fr; height: 1; min-height: 1; }
    .browser-check { width: 3; min-width: 3; height: 1; min-height: 1; margin: 0; padding: 0; border: none; }
    .row-label { width: 1fr; height: 1; min-height: 1; overflow-x: hidden; }
    .history-bar { width: 14; min-width: 14; height: 1; min-height: 1; padding: 0; content-align: right middle; }
    .folder-entry { color: $primary-lighten-2; text-style: bold; }
    .video-entry { color: $text; }
    .selected-video { color: $warning; text-style: bold; }
    .queue-playing, .queue-paused { color: $warning; text-style: bold; }
    .queue-completed { color: $success; }
    .queue-missing, .queue-failed { color: $error; text-style: bold; }
    .queue-stopped, .queue-skipped { color: $text-muted; }
    .queue-queued { color: $text; }
    .queue-selected { background: $boost; text-style: bold; }
    .history-progress { color: $warning; }
    .history-completed { color: $success; }
    .queued-badge { color: $accent; }
    #player-line { height: 1; min-height: 1; background: $boost; }
    #player { width: 1fr; height: 1; min-height: 1; padding: 0 1; overflow-x: hidden; }
    #progress-line { height: 2; min-height: 2; }
    #progress { width: 1fr; height: 2; min-height: 2; }
    #progress-percent { width: 7; height: 2; min-height: 2; content-align: right middle; padding: 0 1; }
    #player-actions { height: 1; min-height: 1; }
    #notice { height: 1; min-height: 1; padding: 0 1; color: $text-muted; overflow-x: hidden; }
    #context-menu { position: absolute; background: $surface; border: round $accent; padding: 0; overflow-y: auto; }
    .context-action { width: 1fr; min-width: 20; height: 1; min-height: 1; margin: 0; padding: 0 1; border: none; content-align: left middle; }
    .dialog-actions { height: 1; min-height: 1; align-horizontal: center; }
    .dialog-actions Button {
        height: 1; min-height: 1; min-width: 10; margin: 0 1; padding: 0 1;
        border: none; content-align: center middle;
    }
    .dialog-title, .details-content, .help-content { width: 80%; max-height: 12; padding: 1; background: $surface; border: solid $accent; }
    .details-content, .help-content { height: auto; overflow-y: auto; }
    RootPrompt, SearchFilterPrompt, ResumePrompt, DetailsPrompt, ConfirmClear, ConfirmClearAll, QuitPrompt, NoticePrompt, HelpPrompt { align: center middle; }
    RootPrompt > Input, SearchFilterPrompt > Input { width: 80%; background: $surface; }
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
        Binding("R", "resume_highlighted", "Resume", show=False),
        Binding("S", "start_over_highlighted", "Start over", show=False),
        Binding("c", "clear_completed", "Clear completed"),
        Binding("shift+f10", "context_menu", "Actions"),
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
        watched_percent: int | None = None,
    ) -> None:
        super().__init__()
        self.watched_percent = resolve_watched_percent(watched_percent)
        self.database = database
        self.queue = QueueService(database, self.watched_percent)
        self.root = self.queue.open(root)
        # Keep the one-shot repair signal so initial rendering does not turn
        # an invalid persisted identity into a new, unrelated highlight.
        self._queue_selection_invalidated = self.database.consume_selected_identity_invalidated()
        # ``browser_path`` remains as a compatibility/navigation anchor for
        # integrations, while the visible model is always rooted at ``root``.
        self.browser_path = self.root
        self.browser_entries: list[BrowserEntry] = []
        self._browser_source_entries: list[BrowserEntry] = []
        self._tree_children: dict[Path, list[BrowserEntry]] = {}
        self._tree_loaded: set[Path] = set()
        self.expanded_paths: set[Path] = set()
        self._tree_tasks: dict[Path, asyncio.Task[None]] = {}
        self._tree_generation = 0
        self._search_generation = 0
        self._recursive_entries: list[BrowserEntry] = []
        self._recursive_task: asyncio.Task[None] | None = None
        self._search_loading = False
        self.selected_paths: set[Path] = set()
        self._browser_highlight_path: Path | None = None
        self.history: dict[Path, HistoryProjection] = {}
        self._history_cache: dict[
            Path, tuple[tuple[int, int, int, int] | None, HistoryProjection | None]
        ] = {}
        self._history_tasks: dict[str, asyncio.Task[None]] = {}
        self._browser_mount_task: asyncio.Task[None] | None = None
        self._browser_rows_by_path: dict[Path, BrowserListItem] = {}
        self._browser_scroll_anchor = 0.0
        self._browser_refresh_token = 0
        self._root_generation = 0
        self.search_query = ""
        self.history_filter = "all"
        self.browser_reverse = False
        self.controller = PlaybackController(self.queue, watched_percent=self.watched_percent)
        self.no_vlc = no_vlc
        self.autoplay = autoplay
        self.autoplay_target_paths: list[Path] = []
        self.resume_command = False
        self._last_pane = "browser"
        self._notice = "Ready"
        self._source_focus: Widget | None = None
        self._restoring_queue_selection = False

    def compose(self) -> ComposeResult:
        with Vertical(id="sections"):
            with Vertical(id="files-section", classes="section"):
                with Horizontal(id="files-header", classes="section-header"):
                    yield CompactButton("▾", id="files-toggle", tooltip="Collapse Files")
                    yield Static("Files", id="files-title", classes="section-title")
                    yield CompactButton("Open", id="files-open", tooltip="Open or change library root")
                    yield CompactButton("Search", id="files-search", tooltip="Search and filter files")
                    yield CompactButton("Add", id="files-add", tooltip="Add highlighted or selected files")
                    yield CompactButton("…", id="files-actions", tooltip="More Files actions")
                with Vertical(id="files-body", classes="section-body"):
                    yield Static("No folders or playable videos here — use Files actions to open a root.", id="files-empty")
                    yield ListView(id="browser")
            with Vertical(id="queue-section", classes="section"):
                with Horizontal(id="queue-header", classes="section-header"):
                    yield CompactButton("▾", id="queue-toggle", tooltip="Collapse Queue")
                    yield Static("Queue", id="queue-title", classes="section-title")
                    yield CompactButton("Play", id="queue-play", tooltip="Play or pause current queue item")
                    yield CompactButton("Next", id="queue-next", tooltip="Play next queue item")
                    yield CompactButton("Clear", id="queue-clear", tooltip="Clear watched/completed queue entries")
                    yield CompactButton("…", id="queue-actions", tooltip="More Queue actions")
                with Vertical(id="queue-body", classes="section-body"):
                    yield Static("Queue is empty — use Files actions to add a video.", id="queue-empty")
                    yield ListView(id="queue")
        with Horizontal(id="player-line"):
            yield Static("Idle · disconnected", id="player")
        with Horizontal(id="progress-line"):
            yield ProgressBar(total=100, id="progress", show_eta=False)
            yield Static("?%", id="progress-percent")
        with Horizontal(id="player-actions"):
            yield CompactButton("Previous", id="player-previous", tooltip="Previous queue item")
            yield CompactButton("Play", id="player-pause", tooltip="Play or pause current item")
            yield CompactButton("Next", id="player-next", tooltip="Next queue item")
            yield CompactButton("…", id="player-menu", tooltip="More player and application actions")
        yield Static("Ready", id="notice")

    def check_action(self, action: str, parameters: tuple[object, ...]) -> bool | None:
        del action, parameters
        # Text input owns ordinary typing and shortcut characters, including
        # Enter submission.  Modal buttons and Input.Submitted remain active.
        return not isinstance(self.focused, Input)

    async def on_mount(self) -> None:
        await self.refresh_browser()
        self.query_one("#browser", ListView).focus()
        self.refresh_queue()
        self.update_status("Ready")
        if not self.no_vlc:
            try:
                await self.controller.start()
                self.update_status("VLC connected")
            except (VLCError, OSError):
                self.update_status("VLC unavailable — press r to retry")
        if self.autoplay or self.resume_command:
            target = self._startup_target_index()
            if target is not None:
                try:
                    if self.no_vlc or self.controller.client is not None:
                        await self._play_queue_index(target, automatic=self.autoplay and not self.resume_command)
                except (IndexError, OSError, VLCError):
                    self.update_status("Automatic resume failed")
                self.refresh_queue()
        self.set_interval(1, self.refresh_playback)

    async def on_unmount(self) -> None:
        for task in self._history_tasks.values():
            task.cancel()
        self._history_tasks.clear()
        if self._browser_mount_task is not None:
            self._browser_mount_task.cancel()
        if self._recursive_task is not None:
            self._recursive_task.cancel()
        for folder in list(self._tree_tasks):
            self._cancel_tree_task(folder)
        if not self.no_vlc:
            await self.controller.stop()

    def on_resize(self, event: events.Resize) -> None:
        del event
        # Section CSS allocates all available space on every resize.  No
        # rebuild is performed, so list identity, selection and scroll remain.
        self._refresh_headers()

    def _row_ancestor(self, widget: Widget | None) -> BrowserListItem | QueueListItem | None:
        if widget is None:
            return None
        return next(
            (
                item
                for item in widget.ancestors_with_self
                if isinstance(item, (BrowserListItem, QueueListItem))
            ),
            None,
        )

    def on_click(self, event: events.Click) -> None:
        widget = event.widget
        if widget is None:
            return
        row = self._row_ancestor(widget)
        if row is not None:
            if isinstance(row, BrowserListItem):
                try:
                    browser = self.query_one("#browser", ListView)
                except NoMatches:
                    return
                if row in browser.children:
                    browser.index = list(browser.children).index(row)
                if event.button == 3:
                    event.stop()
                    self._open_row_menu(row, event.screen_x, event.screen_y)
                elif event.button == 1 and row.is_dir:
                    self.run_worker(self._open_browser_folder(row.path), exclusive=True)
            else:
                try:
                    queue = self.query_one("#queue", ListView)
                except NoMatches:
                    return
                if row in queue.children:
                    queue.index = list(queue.children).index(row)
                    try:
                        self.database.set_selected(row.entry_id)
                        self._apply_queue_selection(row.entry_id)
                    except ValueError:
                        self.database.set_selected(None)
                        self._apply_queue_selection(None)
                if event.button == 3:
                    event.stop()
                    self._open_row_menu(row, event.screen_x, event.screen_y)
            return
        if event.button == 3:
            event.stop()
            return
        progress = next((item for item in widget.ancestors_with_self if isinstance(item, ProgressBar)), None)
        if progress is None:
            return
        if self.no_vlc or self.controller.client is None:
            self.update_status("Seek unavailable while VLC is disconnected")
            return
        current = self.queue.current()
        status = self.controller.status
        duration = status.duration_ms
        if (
            current is None
            or status.path is None
            or not self.controller._same_path(status.path, current.path)
            or duration <= 0
        ):
            self.update_status("Seek unavailable because duration is unknown or media is not ready")
            return
        region = progress.region
        screen_x = event.screen_x if event.screen_x is not None else region.x
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
            files_section = self.query_one("#files-section")
            queue_section = self.query_one("#queue-section")
        except NoMatches:
            return
        ancestors = event.widget.ancestors_with_self
        in_files = files_section in ancestors
        in_queue = queue_section in ancestors
        files_section.set_class(in_files, "focused")
        queue_section.set_class(in_queue, "focused")
        if in_files:
            self._last_pane = "browser"
        elif in_queue:
            self._last_pane = "queue"

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "browser":
            self._last_pane = "browser"
            index = event.list_view.index
            if index is not None and 0 <= index < len(self.browser_entries):
                self._browser_highlight_path = self.browser_entries[index].path
        else:
            self._last_pane = "queue"
            if not self._restoring_queue_selection:
                index = event.list_view.index
                row = (
                    event.list_view.children[index]
                    if index is not None and 0 <= index < len(event.list_view.children)
                    else None
                )
                try:
                    if isinstance(row, QueueListItem):
                        if self.database.get_selected_id() != row.entry_id:
                            self.database.set_selected(row.entry_id)
                        self._apply_queue_selection(row.entry_id)
                    elif self.database.get_selected_id() is not None:
                        self.database.set_selected(None)
                        self._apply_queue_selection(None)
                except ValueError:
                    # A stale highlight can race a root/queue replacement; it
                    # must never retarget a newly reused list position.
                    self.database.set_selected(None)
        self._refresh_headers()

    def _render_headers(self) -> None:
        visible_selected = sum(1 for entry in self.browser_entries if entry.path in self.selected_paths)
        hidden_selected = len(self.selected_paths) - visible_selected
        selection = ""
        if self.selected_paths:
            selection = f" · {len(self.selected_paths)} selected"
            if hidden_selected:
                selection += f" ({hidden_selected} hidden)"
        discovery = []
        if self.search_query:
            discovery.append(f"search:{self.search_query}")
        if self.history_filter != "all":
            discovery.append(self.history_filter.replace("not-completed", "not watched"))
        if discovery:
            selection += " · " + ", ".join(discovery)
        location = self.root.name
        if self.browser_path != self.root:
            try:
                location += f" · focus: {self.browser_path.relative_to(self.root)}"
            except ValueError:
                pass
        title = f"Files · {location}{selection}"
        queue_count = len(self.queue.entries())
        queue_title = f"Queue · {queue_count} entr{'y' if queue_count == 1 else 'ies'}"
        try:
            self.query_one("#files-title", Static).update(title)
            self.query_one("#queue-title", Static).update(queue_title)
            self._refresh_action_buttons()
        except NoMatches:
            pass

    def _refresh_action_buttons(self) -> None:
        """Keep direct controls aligned with the same targeting predicates."""
        try:
            files_add = self.query_one("#files-add", Button)
            queue_play = self.query_one("#queue-play", Button)
            queue_next = self.query_one("#queue-next", Button)
            queue_clear = self.query_one("#queue-clear", Button)
            player_pause = self.query_one("#player-pause", Button)
            player_previous = self.query_one("#player-previous", Button)
            player_next = self.query_one("#player-next", Button)
        except NoMatches:
            return
        current = self.queue.current()
        connected = self.no_vlc or self.controller.client is not None
        clearable = any(
            entry.state == "completed"
            or (
                (history := self.history.get(entry.path)) is not None
                and history.watched(self.watched_percent)
            )
            for entry in self.queue.entries()
        )
        files_add.disabled = not bool(self._paths_for_add())
        queue_play.disabled = current is None or not connected
        player_pause.disabled = current is None or not connected
        queue_next.disabled = not self.queue.entries() or not connected
        player_next.disabled = not self.queue.entries() or not connected
        player_previous.disabled = not self.queue.entries() or not connected
        queue_clear.disabled = not clearable

    def _refresh_headers(self) -> None:
        self._render_headers()

    def update_status(self, message: str) -> None:
        if self.queue.undo_expired:
            self.queue.consume_undo_expired()
            message = f"Undo expired after queue mutation · {message}"
        self._notice = message
        try:
            self.query_one("#notice", Static).update(message)
        except NoMatches:
            pass

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
        return {
            path.expanduser().resolve(strict=False): projection
            for path in paths
            if (projection := self.history.get(path.expanduser().resolve(strict=False))) is not None
        }

    async def _load_history(
        self, paths: list[Path], *, force: bool = False
    ) -> dict[Path, HistoryProjection]:
        normalized = list(dict.fromkeys(path.expanduser().resolve(strict=False) for path in paths))
        if not normalized:
            return {}
        root = self.root
        identities: list[tuple[Path, tuple[int, int, int, int]] | None] = []
        for start in range(0, len(normalized), 100):
            identities.extend(
                await asyncio.gather(
                    *(asyncio.to_thread(_file_identity, path, root) for path in normalized[start : start + 100])
                )
            )
            await asyncio.sleep(0)
        uncached: list[tuple[Path, tuple[int, int, int, int]]] = []
        result: dict[Path, HistoryProjection] = {}
        for path, identity in zip(normalized, identities, strict=True):
            if identity is None:
                self._history_cache[path] = (None, None)
                self.history.pop(path, None)
                continue
            canonical, fingerprint = identity
            cached = self._history_cache.get(canonical)
            if not force and cached is not None and cached[0] == fingerprint:
                if cached[1] is not None:
                    result[canonical] = cached[1]
            else:
                uncached.append((canonical, fingerprint))
        for start in range(0, len(uncached), 100):
            batch = uncached[start : start + 100]
            try:
                loaded = self.database.history_for_identities(batch)
            except (OSError, PathError, RuntimeError):
                loaded = {}
            for path, fingerprint in batch:
                projection = loaded.get(path)
                self._history_cache[path] = (fingerprint, projection)
                if projection is not None:
                    result[path] = projection
            await asyncio.sleep(0)
        if root != self.root:
            return result
        for path in normalized:
            self.history.pop(path, None)
        self.history.update(result)
        return result

    def _cancel_history_task(self, target: str) -> None:
        task = self._history_tasks.pop(target, None)
        if task is not None and not task.done():
            task.cancel()

    def _schedule_history_refresh(
        self,
        paths: list[Path],
        *,
        target: str,
        expected_browser_path: Path | None = None,
        expected_queue_ids: tuple[int, ...] | None = None,
        force: bool = False,
    ) -> None:
        if not paths:
            return
        pending = self._history_tasks.get(target)
        if pending is not None and not pending.done():
            return
        generation = self._root_generation

        async def refresh() -> None:
            try:
                await self._load_history(paths, force=force)
                if generation != self._root_generation:
                    return
                if target == "browser":
                    if expected_browser_path != self.browser_path:
                        return
                    if self.search_query or self.history_filter != "all":
                        self._render_browser_rows()
                    else:
                        self._apply_browser_history_rows()
                elif target == "queue":
                    current_ids = tuple(entry.id for entry in self.queue.entries())
                    if expected_queue_ids != current_ids:
                        return
                    self._apply_queue_history_rows()
            except asyncio.CancelledError:
                raise
            except (OSError, PathError, RuntimeError):
                return
            finally:
                task = self._history_tasks.get(target)
                if task is asyncio.current_task():
                    self._history_tasks.pop(target, None)

        self._history_tasks[target] = asyncio.create_task(refresh())

    def _history_watched(self, path: Path) -> bool:
        history = self.history.get(path.expanduser().resolve(strict=False))
        return history is not None and history.watched(self.watched_percent)

    def _history_percentage(self, path: Path) -> int | None:
        history = self.history.get(path.expanduser().resolve(strict=False))
        if history is None:
            return None
        if history.completion_observed and history.duration_ms > 0:
            return 100
        return clamped_percentage(history.position_ms, history.duration_ms)

    def _history_visible(self, path: Path) -> bool:
        history = self.history.get(path.expanduser().resolve(strict=False))
        return history is not None and history.has_recorded_progress

    def _history_label(self, path: Path) -> str:
        history = self.history.get(path.expanduser().resolve(strict=False))
        if history is None or not history.has_recorded_progress:
            return ""
        if self._history_watched(path):
            category = "✓ Watched (Completed)"
        else:
            category = "~ In progress"
        progress = self._format_time(history.position_ms)
        duration = self._format_time(history.duration_ms) if history.duration_ms > 0 else "?"
        return f"{category} {progress}/{duration}"

    def _history_class(self, path: Path) -> str:
        history = self.history.get(path.expanduser().resolve(strict=False))
        if history is not None and self._history_watched(path):
            return "history-completed"
        if history is not None and history.position_ms > 0:
            return "history-progress"
        return ""

    def _browser_renderable(self, entry: BrowserEntry, selected: bool, queued: bool) -> Text:
        text = render_filename(
            entry.name,
            folder=entry.is_dir,
            depth=self._entry_depth(entry.path),
            expanded=entry.path in self.expanded_paths,
        )
        if entry.is_dir:
            return text
        if queued:
            text.append(" · queued", style="magenta")
        history = self._history_label(entry.path)
        if history:
            text.append(" · " + history, style="green" if self._history_watched(entry.path) else "yellow")
        return text

    def _queue_renderable(
        self, entry: QueueEntry, current_id: int | None, selected_id: int | None
    ) -> Text:
        text = Text(no_wrap=True, overflow="ellipsis")
        text.append("›" if selected_id == entry.id else " ", style="bold cyan")
        if current_id == entry.id:
            text.append("◆ ", style="bold yellow")
        else:
            text.append("  ")
        text.append(render_filename(entry.path.name))
        display_state = (
            entry.state
            if current_id == entry.id or entry.state not in _TRANSIENT_QUEUE_STATES
            else "queued"
        )
        state_label = _QUEUE_STATE_LABELS.get(display_state)
        if state_label:
            text.append(" · " + state_label, style="yellow")
        history = self._history_label(entry.path)
        if history:
            text.append(" · " + history, style="green" if self._history_watched(entry.path) else "yellow")
        return text

    def _apply_browser_history_rows(self) -> None:
        try:
            view = self.query_one("#browser", ListView)
        except NoMatches:
            return
        if not view.is_attached:
            return
        queued_paths = {entry.path for entry in self.queue.entries()}
        visible = {entry.path: entry for entry in self.browser_entries}
        for row in view.children:
            if not isinstance(row, BrowserListItem) or row.is_dir:
                continue
            entry = visible.get(row.path)
            if entry is None:
                continue
            try:
                row.query_one(".row-label", Label).update(
                    self._browser_renderable(entry, entry.path in self.selected_paths, entry.path in queued_paths)
                )
                row.query_one(ItemProgress).set_progress(
                    self._history_percentage(entry.path),
                    self._history_watched(entry.path),
                    visible=self._history_visible(entry.path),
                )
            except NoMatches:
                continue
        self._render_headers()

    def _apply_queue_history_rows(self) -> None:
        try:
            view = self.query_one("#queue", ListView)
        except NoMatches:
            return
        if not view.is_attached:
            return
        entries = {entry.id: entry for entry in self.queue.entries()}
        current = self.queue.current()
        current_id = current.id if current is not None else None
        selected_id = self.database.get_selected_id()
        for row in view.children:
            if not isinstance(row, QueueListItem):
                continue
            entry = entries.get(row.entry_id)
            if entry is None:
                continue
            try:
                row.query_one(".row-label", Label).update(
                    self._queue_renderable(entry, current_id, selected_id)
                )
                row.query_one(ItemProgress).set_progress(
                    self._history_percentage(entry.path),
                    self._history_watched(entry.path),
                    visible=self._history_visible(entry.path),
                )
            except NoMatches:
                continue
        self._render_headers()

    def _refresh_browser_history(self) -> None:
        paths = [entry.path for entry in self.browser_entries if entry.supported]
        self._schedule_history_refresh(
            paths,
            target="browser",
            expected_browser_path=self.browser_path,
            force=True,
        )

    def _entry_depth(self, path: Path) -> int:
        try:
            return len(path.relative_to(self.root).parts) - 1
        except ValueError:
            return 0

    def _ordered_children(self, folder: Path) -> list[BrowserEntry]:
        entries = list(self._tree_children.get(folder, ()))
        if not self.browser_reverse:
            return entries
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
        return [*folders, *files]

    def _visible_tree_entries(self) -> list[BrowserEntry]:
        result: list[BrowserEntry] = []

        def flatten(folder: Path) -> None:
            for entry in self._ordered_children(folder):
                result.append(entry)
                if entry.is_dir and entry.path in self.expanded_paths and entry.path in self._tree_loaded:
                    flatten(entry.path)

        flatten(self.root)
        return result

    def _filtered_browser_entries(self) -> list[BrowserEntry]:
        """Return visible rows, retaining ancestors for active filters."""
        if not self.search_query and self.history_filter == "all":
            return self._visible_tree_entries()
        source = self._recursive_entries
        if not source:
            return []
        matching_files: set[Path] = set()
        for entry in source:
            if entry.is_dir:
                continue
            if self.search_query and self.search_query not in entry.name.casefold():
                continue
            history = self.history.get(entry.path)
            watched = history is not None and history.watched(self.watched_percent)
            if self.history_filter == "progress" and not (
                history is not None and history.position_ms > 0 and not watched
            ):
                continue
            if self.history_filter == "not-completed" and watched:
                continue
            matching_files.add(entry.path)
        included = set(matching_files)
        by_path = {entry.path: entry for entry in source}
        for path in tuple(matching_files):
            parent = by_path.get(path)
            while parent is not None and parent.parent is not None:
                included.add(parent.parent)
                parent = by_path.get(parent.parent)
        return [entry for entry in source if entry.path in included]

    def _update_browser_row(
        self, row: BrowserListItem, entry: BrowserEntry, queued_paths: set[Path]
    ) -> None:
        try:
            row.query_one(".row-label", Label).update(
                self._browser_renderable(entry, entry.path in self.selected_paths, entry.path in queued_paths)
            )
            if not entry.is_dir:
                row.query_one(ItemProgress).set_progress(
                    self._history_percentage(entry.path),
                    self._history_watched(entry.path),
                    visible=self._history_visible(entry.path),
                )
        except NoMatches:
            pass

    async def _mount_browser_rows(
        self,
        browser: ListView,
        rows: list[BrowserListItem],
        highlighted_path: Path | None,
        old_scroll_y: float,
    ) -> None:
        """Replace the list in DOM order while reusing path-identical rows."""
        await browser.remove_children()
        if rows:
            await browser.mount(*rows)
        if highlighted_path is not None:
            restored = next(
                (index for index, entry in enumerate(self.browser_entries) if entry.path == highlighted_path),
                None,
            )
            browser.index = restored
        elif self.browser_entries and self._browser_highlight_path is None:
            browser.index = 0
        if self.browser_entries:
            browser.scroll_y = old_scroll_y

    def _schedule_browser_mount(
        self,
        browser: ListView,
        rows: list[BrowserListItem],
        highlighted_path: Path | None,
        old_scroll_y: float,
    ) -> None:
        if self._browser_mount_task is not None and not self._browser_mount_task.done():
            self._browser_mount_task.cancel()
        task = asyncio.create_task(
            self._mount_browser_rows(browser, rows, highlighted_path, old_scroll_y)
        )
        self._browser_mount_task = task

        def finished(done: asyncio.Task[None]) -> None:
            if self._browser_mount_task is done:
                self._browser_mount_task = None

        task.add_done_callback(finished)

    def _render_browser_rows(self) -> None:
        try:
            browser = self.query_one("#browser", ListView)
        except NoMatches:
            return
        highlighted_path: Path | None = self._browser_highlight_path
        old_scroll_y = browser.scroll_y
        if old_scroll_y > 0:
            self._browser_scroll_anchor = old_scroll_y
        if browser.index is not None and 0 <= browser.index < len(self.browser_entries):
            highlighted_path = self.browser_entries[browser.index].path
        if highlighted_path is not None:
            self._browser_highlight_path = highlighted_path
        self.browser_entries = self._filtered_browser_entries()
        self._browser_source_entries = list(self.browser_entries)
        empty = self.query_one("#files-empty", Static)
        empty.display = not bool(self.browser_entries)
        if not self.browser_entries:
            empty.update(
                "No folders or playable videos match the current view — use Files actions to adjust it."
                if self.search_query or self.history_filter != "all"
                else "No folders or playable videos here — use Open or Add to choose a library."
            )
        queued_paths = {entry.path for entry in self.queue.entries()}
        old_rows = list(browser.children)
        for old_row in old_rows:
            if isinstance(old_row, BrowserListItem):
                self._browser_rows_by_path[old_row.path] = old_row
        same_shape = len(old_rows) == len(self.browser_entries) and all(
            isinstance(row, BrowserListItem)
            and row.path == entry.path
            and row.is_dir == entry.is_dir
            for row, entry in zip(old_rows, self.browser_entries)
        )
        mounted_now = same_shape
        if same_shape:
            for row, entry in zip(old_rows, self.browser_entries, strict=True):
                assert isinstance(row, BrowserListItem)
                self._update_browser_row(row, entry, queued_paths)
                row.set_class(entry.path in self.selected_paths, "selected-video")
        else:
            old_by_path: dict[Path, BrowserListItem] = {}
            for old_row in old_rows:
                if isinstance(old_row, BrowserListItem):
                    old_by_path[old_row.path] = old_row
            new_rows: list[BrowserListItem] = []
            for entry in self.browser_entries:
                reusable_row = old_by_path.get(entry.path) or self._browser_rows_by_path.get(entry.path)
                if reusable_row is not None and reusable_row.is_dir == entry.is_dir:
                    self._update_browser_row(reusable_row, entry, queued_paths)
                    reusable_row.set_class(entry.path in self.selected_paths, "selected-video")
                    new_rows.append(reusable_row)
                else:
                    new_rows.append(
                        BrowserListItem(
                            entry,
                            self._browser_renderable(
                                entry,
                                entry.path in self.selected_paths,
                                entry.path in queued_paths,
                            ),
                            selected=entry.path in self.selected_paths,
                            depth=self._entry_depth(entry.path),
                            history_percentage=self._history_percentage(entry.path),
                            history_watched=self._history_watched(entry.path),
                            history_visible=self._history_visible(entry.path),
                        )
                    )
                    self._browser_rows_by_path[entry.path] = new_rows[-1]
            mounted_now = False
            self._schedule_browser_mount(
                browser,
                new_rows,
                highlighted_path,
                max(old_scroll_y, self._browser_scroll_anchor),
            )
        if mounted_now and highlighted_path is not None:
            restored = next(
                (index for index, entry in enumerate(self.browser_entries) if entry.path == highlighted_path),
                None,
            )
            browser.index = restored if restored is not None else None
        elif mounted_now and self.browser_entries and browser.index is None and self._browser_highlight_path is None:
            browser.index = 0
        if mounted_now and self.browser_entries:
            browser.scroll_y = old_scroll_y
        self._render_headers()

    async def _load_tree_branch(self, folder: Path, generation: int) -> None:
        try:
            entries = await asyncio.to_thread(list_folder, folder, self.root)
        except (OSError, PathError) as exc:
            if generation == self._tree_generation and folder == self.root:
                self.update_status(f"Browse failed: {exc}")
            return
        if generation != self._tree_generation:
            return
        if folder != self.root and folder not in self.expanded_paths:
            return
        entries = [
            BrowserEntry(
                entry.path,
                entry.name,
                entry.is_dir,
                entry.supported,
                depth=self._entry_depth(entry.path),
                parent=folder,
                expanded=entry.path in self.expanded_paths,
                loading=entry.path in self._tree_tasks,
            )
            for entry in entries
        ]
        self._tree_children[folder] = entries
        self._tree_loaded.add(folder)
        self._render_browser_rows()
        self._schedule_history_refresh(
            [entry.path for entry in self.browser_entries if entry.supported],
            target="browser",
            expected_browser_path=self.browser_path,
            force=False,
        )

    def _start_tree_branch_load(self, folder: Path) -> None:
        self._cancel_tree_task(folder)
        generation = self._tree_generation
        task = asyncio.create_task(self._load_tree_branch(folder, generation))
        self._tree_tasks[folder] = task

        def finished(done: asyncio.Task[None]) -> None:
            if self._tree_tasks.get(folder) is done:
                self._tree_tasks.pop(folder, None)

        task.add_done_callback(finished)

    def _cancel_tree_task(self, folder: Path) -> None:
        task = self._tree_tasks.pop(folder, None)
        if task is not None and not task.done():
            task.cancel()

    async def _refresh_recursive_discovery(self, generation: int | None = None) -> None:
        """Discover the whole root only for search/history filters."""
        current_task = asyncio.current_task()
        previous_task = self._recursive_task
        if previous_task is not None and previous_task is not current_task and not previous_task.done():
            previous_task.cancel()
        self._recursive_task = current_task
        self._search_generation += 1
        search_generation = self._search_generation
        self._search_loading = True
        from .paths import discover_tree

        try:
            discovered = await asyncio.to_thread(discover_tree, self.root)
            if generation is not None and generation != self._tree_generation:
                return
            if search_generation != self._search_generation:
                return
            self._recursive_entries = [
                BrowserEntry(
                    item.path,
                    item.name,
                    item.is_dir,
                    item.supported,
                    depth=item.depth,
                    parent=item.parent,
                    expanded=item.path in self.expanded_paths,
                    loading=item.path in self._tree_tasks,
                )
                for item in discovered
            ]
            await self._load_history(
                [entry.path for entry in self._recursive_entries if entry.supported], force=False
            )
            if generation is None or generation == self._tree_generation:
                self._render_browser_rows()
        except (OSError, PathError):
            if generation is None or generation == self._tree_generation:
                self._recursive_entries = []
        finally:
            if self._recursive_task is current_task:
                self._recursive_task = None
                self._search_loading = False

    async def refresh_browser(self) -> None:
        self._browser_refresh_token += 1
        self._tree_generation += 1
        generation = self._tree_generation
        self._cancel_history_task("browser")
        for folder in list(self._tree_tasks):
            self._cancel_tree_task(folder)
        try:
            self.search_query = self.search_query.casefold()
        except AttributeError:
            self.search_query = ""
        if self.root not in self._tree_loaded:
            self._start_tree_branch_load(self.root)
            task = self._tree_tasks.get(self.root)
            if task is not None:
                await task
        else:
            self._render_browser_rows()
        if self.search_query or self.history_filter != "all":
            await self._refresh_recursive_discovery(generation)
        else:
            self._refresh_browser_history()

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
        self,
        row: QueueListItem,
        entry: QueueEntry,
        current_id: int | None,
        selected_id: int | None,
    ) -> None:
        display_state = (
            entry.state
            if current_id == entry.id or entry.state not in _TRANSIENT_QUEUE_STATES
            else "queued"
        )
        classes = self._queue_state_class(display_state)
        if selected_id == entry.id:
            classes += " queue-selected"
        row.set_classes(classes)
        try:
            row.query_one(".row-label", Label).update(
                self._queue_renderable(entry, current_id, selected_id)
            )
            row.query_one(ItemProgress).set_progress(
                self._history_percentage(entry.path),
                self._history_watched(entry.path),
                visible=self._history_visible(entry.path),
            )
        except NoMatches:
            pass

    def _apply_queue_selection(self, selected_id: int | None) -> None:
        """Keep the durable queue highlight visible even while Files has focus."""
        try:
            view = self.query_one("#queue", ListView)
        except NoMatches:
            return
        entries = {entry.id: entry for entry in self.queue.entries()}
        current_id = self.database.get_current_id()
        for row in view.children:
            if isinstance(row, QueueListItem) and (entry := entries.get(row.entry_id)) is not None:
                self._update_queue_row(row, entry, current_id, selected_id)

    def refresh_queue(
        self,
        selected_path: Path | None = None,
        *,
        selection_captured: bool = False,
        selected_entry_id: int | None = None,
    ) -> None:
        try:
            view = self.query_one("#queue", ListView)
        except NoMatches:
            return

        old_rows = list(view.children)
        old_view_id: int | None = None
        if view.index is not None and 0 <= view.index < len(old_rows):
            old_row = old_rows[view.index]
            if isinstance(old_row, QueueListItem):
                old_view_id = old_row.entry_id
        old_persisted_id = self.database.get_selected_id()
        selection_invalidated = (
            self._queue_selection_invalidated
            or self.database.consume_selected_identity_invalidated()
        )
        entries = self.queue.entries()
        if selected_entry_id is None and selected_path is not None:
            selected_entry_id = next(
                (entry.id for entry in entries if entry.path == selected_path), None
            )
        target_id = selected_entry_id if selected_entry_id is not None else old_persisted_id
        if target_id is None and old_view_id is not None:
            # This preserves an in-memory highlight for callers/tests that set
            # ListView.index directly, but only by entry identity.
            target_id = old_view_id
        entry_ids = {entry.id for entry in entries}
        target_present = target_id in entry_ids if target_id is not None else False
        had_identity = (
            old_view_id is not None
            or old_persisted_id is not None
            or selection_captured
            or selection_invalidated
        )

        queue_paths = [entry.path for entry in entries]
        self._schedule_history_refresh(
            queue_paths,
            target="queue",
            expected_queue_ids=tuple(entry.id for entry in entries),
            force=True,
        )
        current = self.queue.current()
        current_id = current.id if current is not None else None
        empty = self.query_one("#queue-empty", Static)
        empty.display = not bool(entries)
        if not entries:
            empty.update("Queue is empty — use Files actions to add a video.")

        rows = list(view.children)
        reuse_rows = len(rows) == len(entries) and all(
            isinstance(row, QueueListItem) and row.entry_id == entry.id
            for row, entry in zip(rows, entries)
        )
        self._restoring_queue_selection = True
        try:
            if reuse_rows:
                for row, entry in zip(rows, entries, strict=True):
                    assert isinstance(row, QueueListItem)
                    self._update_queue_row(
                        row, entry, current_id, target_id if target_present else None
                    )
            else:
                view.clear()
                for entry in entries:
                    view.append(
                        QueueListItem(
                            entry,
                            current_id,
                            target_id if target_present else None,
                            self._queue_renderable(
                                entry, current_id, target_id if target_present else None
                            ),
                            history_percentage=self._history_percentage(entry.path),
                            history_watched=self._history_watched(entry.path),
                            history_visible=self._history_visible(entry.path),
                        )
                    )
            if target_present:
                restored = next(index for index, entry in enumerate(entries) if entry.id == target_id)
                view.index = restored
                if self.database.get_selected_id() != target_id:
                    self.database.set_selected(target_id)
            elif entries and not had_identity:
                view.index = 0
                self.database.set_selected(entries[0].id)
            else:
                # Do not let a removed identity silently select a new row at
                # the same position.
                view.index = None
                if self.database.get_selected_id() is not None:
                    self.database.set_selected(None)
        finally:
            self._restoring_queue_selection = False
            self._queue_selection_invalidated = False
        self._apply_queue_selection(self.database.get_selected_id())
        self._render_headers()

    def refresh_playback(self) -> None:
        current = self.queue.current()
        if self.no_vlc:
            state = current.state if current else "idle"
            position_ms = 0
            duration_ms = 0
            connected = False
        else:
            state = self.controller.status.state if current else "idle"
            position_ms = self.controller.status.position_ms if current else 0
            duration_ms = self.controller.status.duration_ms if current else 0
            connected = self.controller.client is not None
        if current is None:
            player_text = f"Idle · {'offline' if self.no_vlc else ('connected' if connected else 'disconnected')}"
        else:
            total = self._format_time(duration_ms) if duration_ms > 0 else "?"
            player_text = (
                f"{state.upper()} · {current.path.name} · {self._format_time(position_ms)} / {total} · "
                f"{'offline' if self.no_vlc else ('connected' if connected else 'disconnected')}"
            )
        percent = min(100.0, position_ms * 100 / duration_ms) if duration_ms > 0 else 0.0
        try:
            self.query_one("#player", Static).update(player_text)
            self.query_one("#progress", ProgressBar).update(progress=percent)
            self.query_one("#progress-percent", Static).update(
                f"{round(percent):d}%" if duration_ms > 0 else "?%"
            )
            self.query_one("#notice", Static).update(self._notice)
        except NoMatches:
            return
        self._refresh_browser_history()
        self.refresh_queue()

    def _browser_entry(self) -> BrowserEntry | None:
        try:
            view = self.query_one("#browser", ListView)
        except NoMatches:
            return None
        if view.index is None or not 0 <= view.index < len(self.browser_entries):
            return None
        return self.browser_entries[view.index]

    def _queue_index(self) -> int | None:
        try:
            view = self.query_one("#queue", ListView)
        except NoMatches:
            return None
        entries = self.queue.entries()
        if view.index is None or not 0 <= view.index < len(entries):
            return None
        return view.index

    def _queue_selected_id(self) -> int | None:
        index = self._queue_index()
        if index is None:
            return None
        return self.queue.entries()[index].id

    def _queue_selected_path(self) -> Path | None:
        index = self._queue_index()
        if index is None:
            return None
        return self.queue.entries()[index].path

    def _browser_has_focus(self) -> bool:
        try:
            section = self.query_one("#files-section")
        except NoMatches:
            return False
        return self.focused is not None and section in self.focused.ancestors_with_self

    def _queue_has_focus(self) -> bool:
        try:
            section = self.query_one("#queue-section")
        except NoMatches:
            return False
        return self.focused is not None and section in self.focused.ancestors_with_self

    def _section_collapsed(self, section: str) -> bool:
        return self.query_one(f"#{section}-section").has_class("collapsed")

    def _toggle_section(self, section: Literal["files", "queue"]) -> None:
        container = self.query_one(f"#{section}-section")
        collapsed = not container.has_class("collapsed")
        container.set_class(collapsed, "collapsed")
        if collapsed:
            self.query_one(f"#{section}-toggle", Button).label = "▸"
            self.query_one(f"#{section}-toggle", Button).tooltip = f"Expand {section.title()}"
            focused = self.focused
            body = self.query_one(f"#{section}-body")
            if focused is not None and body in focused.ancestors_with_self:
                self.query_one(f"#{section}-toggle", Button).focus()
        else:
            self.query_one(f"#{section}-toggle", Button).label = "▾"
            self.query_one(f"#{section}-toggle", Button).tooltip = f"Collapse {section.title()}"
        self._refresh_headers()

    def _paths_for_add(self) -> list[Path]:
        if self.selected_paths:
            return sorted(self.selected_paths, key=lambda path: natural_key(str(path)))
        entry = self._browser_entry()
        if entry is not None and entry.supported:
            return [entry.path]
        return []

    def _queue_path_index(self, path: Path) -> int | None:
        return next((index for index, entry in enumerate(self.queue.entries()) if entry.path == path), None)

    def _startup_target_index(self) -> int | None:
        entries = self.queue.entries()
        if not entries:
            return None
        if self.resume_command:
            return self.queue.resume_target_index()
        for path in self.autoplay_target_paths:
            canonical = path.expanduser().resolve(strict=False)
            for index, entry in enumerate(entries):
                if entry.path == canonical:
                    return index
        return 0

    def _require_vlc_for_play(self) -> bool:
        if not self.no_vlc and self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return False
        return True

    def _resume_offer_for_path(self, path: Path) -> ResumeOffer:
        return ResumeOffer(self.database.history_for(path, root=self.root), self.watched_percent)

    @staticmethod
    def _offer_requires_choice(offer: ResumeOffer) -> bool:
        return not offer.completed and (offer.usable_resume or offer.legacy_fallback)

    async def _play_queue_offline(
        self, index: int, *, choice: str | None = None, automatic: bool = False
    ) -> bool:
        entries = self.queue.entries()
        if not 0 <= index < len(entries):
            raise IndexError("queue index out of range")
        entry = entries[index]
        current = self.queue.current()
        if current is not None and current.id == entry.id and current.state in {"playing", "paused"}:
            if current.state == "paused":
                self.queue.set_current_state(current.id, "playing")
            return True
        offer = ResumeOffer(
            self.queue.database.history_for(entry.path, root=self.root), self.watched_percent
        )
        if offer.completed:
            self.queue.play_now(index)
            return True
        if self._offer_requires_choice(offer):
            if choice is None and not automatic:
                raise ResumeChoiceRequired(offer)
            if choice == "cancel":
                return False
            if choice not in {None, "resume", "start_over"}:
                raise ValueError("unknown resume choice")
        self.queue.play_now(index)
        return True

    async def _play_queue_index(
        self, index: int, choice: str | None = None, *, automatic: bool = False
    ) -> bool:
        entries = self.queue.entries()
        if not 0 <= index < len(entries):
            raise IndexError("queue index out of range")
        entry_id = entries[index].id
        try:
            if self.no_vlc:
                return await self._play_queue_offline(index, choice=choice, automatic=automatic)
            if not self._require_vlc_for_play():
                return False
            return await self.controller.play_with_policy(index, choice=choice, automatic=automatic)
        except ResumeChoiceRequired as exc:
            def chosen(value: str | None) -> None:
                if value is None:
                    return
                current_index = next(
                    (position for position, entry in enumerate(self.queue.entries()) if entry.id == entry_id),
                    None,
                )
                if current_index is not None:
                    self.run_worker(self._play_queue_index(current_index, choice=value), exclusive=True)

            self.push_screen(ResumePrompt(exc.offer), chosen)
            return False

    async def _activate_browser_video(self, entry: BrowserEntry, choice: str | None = None) -> None:
        if not self._require_vlc_for_play():
            return
        offer = self._resume_offer_for_path(entry.path)
        if choice is None and self._offer_requires_choice(offer):
            def chosen(value: str | None) -> None:
                if value is not None:
                    self.run_worker(self._activate_browser_video(entry, choice=value), exclusive=True)

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

    async def _play_details_target(self, choice: str) -> None:
        entry = self._details_target()
        if entry is None or entry.is_dir:
            self.update_status("Nothing playable is highlighted")
            return
        offer = ResumeOffer(self.history.get(entry.path), self.watched_percent)
        if choice == "resume" and not self._offer_requires_choice(offer):
            self.update_status("No trustworthy resume point is recorded for this video")
            return
        try:
            if self._last_pane == "queue":
                index = self._queue_path_index(entry.path)
                if index is None:
                    self.update_status("Queue item is no longer available")
                    return
                played = await self._play_queue_index(index, choice=choice)
            else:
                await self._activate_browser_video(entry, choice=choice)
                return
        except (IndexError, OSError, PathError, RuntimeError, VLCError) as exc:
            self.update_status(f"Play failed: {exc}")
            return
        if played:
            self.update_status(f"{'Resuming' if choice == 'resume' else 'Starting over'} {entry.path.name}")
            self.refresh_queue()

    async def action_resume_highlighted(self) -> None:
        await self._play_details_target("resume")

    async def action_start_over_highlighted(self) -> None:
        await self._play_details_target("start_over")

    async def _open_browser_folder(self, path: Path) -> None:
        try:
            canonical = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError):
            self.update_status("Folder is no longer available inside the library root")
            return
        if not is_beneath(canonical, self.root) or not canonical.is_dir():
            self.update_status("Folder is no longer available inside the library root")
            return
        self.browser_path = canonical
        if canonical in self.expanded_paths:
            self.expanded_paths.remove(canonical)
            self._cancel_tree_task(canonical)
            self._render_browser_rows()
            self.update_status("Folder collapsed")
            return
        self.expanded_paths.add(canonical)
        self._render_browser_rows()
        if canonical not in self._tree_loaded:
            self._start_tree_branch_load(canonical)
            self.update_status("Loading folder…")
        else:
            self.update_status("Folder expanded")

    async def action_activate(self) -> None:
        entry = self._browser_entry()
        if self._queue_has_focus():
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
        if entry is None:
            self.update_status("Nothing is highlighted — open a folder or highlight a video")
        elif entry.is_dir:
            await self._open_browser_folder(entry.path)
        else:
            await self._activate_browser_video(entry)

    async def action_parent(self) -> None:
        if not self._browser_has_focus():
            self.update_status("Parent navigation is available in the Files section")
            return
        if self.browser_path != self.root:
            current = self.browser_path
            self.expanded_paths.discard(current)
            self._cancel_tree_task(current)
            self.browser_path = current.parent
            self._render_browser_rows()
        else:
            self.update_status("At root")
            return
        self.update_status("At root" if self.browser_path == self.root else "Browsing")

    async def action_select(self) -> None:
        if not self._browser_has_focus():
            self.update_status("Select is available in the Files section")
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
            self._render_browser_rows()
        elif entry.is_dir:
            # In a flattened tree, retain folder navigation semantics while
            # keeping the legacy v-then-a workflow useful: toggle the first
            # visible playable child, never the directory itself.
            child = next(
                (
                    candidate
                    for candidate in self.browser_entries
                    if candidate.supported and candidate.parent == entry.path
                ),
                None,
            )
            if child is not None:
                if child.path in self.selected_paths:
                    self.selected_paths.remove(child.path)
                    self.update_status(f"Deselected {child.name}")
                else:
                    self.selected_paths.add(child.path)
                    self.update_status(f"Selected {child.name} ({len(self.selected_paths)} total)")
                self._render_browser_rows()
            else:
                self.update_status("Folders cannot be selected; highlight a playable video")
        else:
            self.update_status("Folders cannot be selected; highlight a playable video")

    async def action_add_selected(self) -> None:
        paths = self._paths_for_add()
        if not paths:
            self.update_status("Nothing to add — highlight a playable video or select one")
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
        self._render_browser_rows()
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
            self.update_status("Nothing to play — highlight a playable video or select one")
            return
        if not self._require_vlc_for_play():
            return
        offer = self._resume_offer_for_path(paths[0])
        if self._offer_requires_choice(offer):
            def chosen(value: str | None) -> None:
                if value is not None:
                    self.run_worker(self._finish_add_and_play(paths, choice=value), exclusive=True)

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
                self._root_generation += 1
                self._tree_generation += 1
                self.expanded_paths.clear()
                self._tree_children.clear()
                self._tree_loaded.clear()
                self._browser_rows_by_path.clear()
                self._browser_scroll_anchor = 0.0
                self._recursive_entries.clear()
                self._search_generation += 1
                if self._recursive_task is not None and not self._recursive_task.done():
                    self._recursive_task.cancel()
                self.selected_paths.clear()
                self._browser_highlight_path = None
                try:
                    self.query_one("#browser", ListView).index = None
                except NoMatches:
                    pass
                self._history_cache.clear()
                self.history.clear()
                self.call_later(self.refresh_browser)
                self.call_later(self.refresh_queue)
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
                self.queue.set_current_state(current.id, state)
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
            self.update_status("Seek unavailable in offline mode")
            return
        if self.controller.client is None:
            self.update_status("VLC unavailable — press r to retry")
            return
        try:
            await self.controller.seek(seconds)
        except (OSError, VLCError) as exc:
            self.update_status(f"Seek failed: {exc}")

    async def action_remove(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Remove is available in the Queue section")
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
            self.update_status("Move is available in the Queue section")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        selected_entry_id = self._queue_selected_id()
        try:
            self.queue.move(index, 1)
        except (IndexError, OSError, RuntimeError) as exc:
            self.update_status(f"Move failed: {exc}")
        else:
            self.update_status("Moved queue item down")
        self.refresh_queue(selected_entry_id=selected_entry_id, selection_captured=True)

    def action_move_up(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Move is available in the Queue section")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        selected_entry_id = self._queue_selected_id()
        try:
            self.queue.move(index, -1)
        except (IndexError, OSError, RuntimeError) as exc:
            self.update_status(f"Move failed: {exc}")
        else:
            self.update_status("Moved queue item up")
        self.refresh_queue(selected_entry_id=selected_entry_id, selection_captured=True)

    async def action_retry(self) -> None:
        if not self._queue_has_focus():
            self.update_status("Retry is available in the Queue section")
            return
        index = self._queue_index()
        if index is None:
            self.update_status("Nothing is highlighted in the queue")
            return
        try:
            if not self.no_vlc and self.controller.client is None:
                await self.controller.start()
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

    def _details_target(self) -> BrowserEntry | None:
        if self._last_pane == "queue":
            index = self._queue_index()
            if index is None:
                return None
            entry = self.queue.entries()[index]
            return BrowserEntry(entry.path, entry.path.name, False, True)
        return self._browser_entry()

    def _details_text(self, entry: BrowserEntry, history: HistoryProjection | None) -> str:
        queued = any(item.path == entry.path for item in self.queue.entries())
        queue_label = "queued" if queued else "not queued"
        if history is None or not history.has_recorded_progress:
            return (
                f"{entry.name}\nFull path: {entry.path}\n{queue_label}\n"
                "No recorded progress\nResume position: unknown\nFurthest progress: unknown\n"
                f"Duration: unknown\nWatched threshold: {self.watched_percent}%\n"
                "Watched: no\nLast played: unknown"
            )
        last_played = history.last_played_at or "unknown"
        if history.resume_position_ms is not None:
            resume = self._format_time(history.resume_position_ms)
        elif history.fallback_resume_position_ms is not None:
            resume = f"{self._format_time(history.fallback_resume_position_ms)} (furthest recorded fallback)"
        else:
            resume = "unknown"
        return (
            f"{entry.name}\nFull path: {entry.path}\n{queue_label}\n"
            f"Resume position: {resume}\nFurthest progress: {self._format_time(history.position_ms)}\n"
            f"Duration: {self._format_time(history.duration_ms) if history.duration_ms else 'unknown'}\n"
            f"Watched threshold: {self.watched_percent}%\n"
            f"Watched: {'yes' if history.watched(self.watched_percent) else 'no'}\n"
            f"Last played: {last_played}"
        )

    async def _open_details(
        self, entry: BrowserEntry | None = None, *, pane: Literal["browser", "queue"] | None = None
    ) -> None:
        target = entry or self._details_target()
        if pane is not None:
            details_pane: Literal["browser", "queue"] = pane
        elif self._last_pane == "queue":
            details_pane = "queue"
        else:
            details_pane = "browser"
        if target is None or target.is_dir:
            self.update_status("Nothing playable is highlighted")
            return
        await self._load_history([target.path], force=True)
        history = self.history.get(target.path.resolve(strict=False))
        offer = ResumeOffer(history, self.watched_percent)

        def chosen(value: str | None) -> None:
            if value is not None:
                self.run_worker(self._dispatch_details_choice(target, value, details_pane), exclusive=True)

        self.push_screen(
            DetailsPrompt(
                self._details_text(target, history),
                can_resume=self._offer_requires_choice(offer),
                can_start_over=True,
            ),
            chosen,
        )

    async def _dispatch_details_choice(
        self, entry: BrowserEntry, choice: str, pane: Literal["browser", "queue"]
    ) -> None:
        if pane == "queue":
            index = self._queue_path_index(entry.path)
            if index is None:
                self.update_status("Queue item is no longer available")
                return
            await self._play_queue_index(index, choice=choice)
            self.refresh_queue()
        else:
            self._last_pane = "browser"
            await self._activate_browser_video(entry, choice=choice)

    def _context_library_target(self, entry: BrowserEntry | None = None) -> FileTarget | None:
        highlighted = entry or self._browser_entry()
        if highlighted is None:
            return None
        selected = tuple(sorted(self.selected_paths, key=lambda path: natural_key(str(path))))
        return FileTarget(highlighted.path, self._root_generation, selected)

    def _target_batch_label(self, target: FileTarget, verb: str) -> str:
        paths = target.selected_paths or (target.path,)
        if len(paths) == 1 and not target.selected_paths:
            return verb
        hidden = len(paths) - sum(path in {entry.path for entry in self.browser_entries} for path in paths)
        suffix = f" ({len(paths)} selected"
        if hidden:
            suffix += f", {hidden} hidden"
        return verb + suffix + ")"

    def _context_actions_for_file(self, target: FileTarget) -> list[ContextAction]:
        entry = next((item for item in self._browser_source_entries if item.path == target.path), None)
        if entry is None:
            entry = BrowserEntry(target.path, target.path.name, target.path.is_dir(), target.path.is_file())
        actions: list[ContextAction] = []
        if entry.is_dir:
            actions.append(ContextAction("open-folder", "Open folder", target))
        else:
            offer = self._resume_offer_for_path(entry.path)
            actions.extend(
                [
                    ContextAction("play-now-file", f"Play now · {entry.name}", target),
                    ContextAction("resume-file", "Resume", target, self._offer_requires_choice(offer)),
                    ContextAction("start-over-file", "Start over", target),
                    ContextAction("select-file", "Deselect" if entry.path in self.selected_paths else "Select", target),
                    ContextAction("add-end-file", self._target_batch_label(target, "Add to end"), target),
                    ContextAction("play-next-file", self._target_batch_label(target, "Play next"), target),
                    ContextAction("add-play-file", self._target_batch_label(target, "Add & play"), target),
                    ContextAction("details-file", "Details", target),
                ]
            )
        return actions

    def _context_actions_for_files_section(self, target: SectionTarget) -> list[ContextAction]:
        entry = self._browser_entry()
        file_target = self._context_library_target(entry)
        actions: list[ContextAction] = []
        if file_target is not None:
            actions.extend(self._context_actions_for_file(file_target))
        actions.extend(
            [
                ContextAction("open-root", "Open/change root", target),
                ContextAction("parent", "Up", target, self.browser_path != self.root),
                ContextAction("search-filter", "Search / filters…", target),
                ContextAction("sort-files", "Reverse filename order", target),
                ContextAction("add-selection", self._batch_label("Add to end"), target, bool(self._paths_for_add())),
                ContextAction("next-selection", self._batch_label("Play next"), target, bool(self._paths_for_add())),
                ContextAction("add-play-selection", self._batch_label("Add & play"), target, bool(self._paths_for_add())),
                ContextAction("clear-selection", "Clear selection", target, bool(self.selected_paths)),
            ]
        )
        return actions

    def _batch_label(self, verb: str) -> str:
        paths = self._paths_for_add()
        if not paths:
            return verb
        hidden = len(self.selected_paths) - sum(path in {entry.path for entry in self.browser_entries} for path in self.selected_paths)
        suffix = f" ({len(paths)} selected"
        if hidden:
            suffix += f", {hidden} hidden"
        return verb + suffix + ")"

    def _queue_has_clearable(self) -> bool:
        return any(
            entry.state == "completed"
            or (
                (history := self.history.get(entry.path)) is not None
                and history.watched(self.watched_percent)
            )
            for entry in self.queue.entries()
        )

    def _context_actions_for_queue_section(self, target: SectionTarget) -> list[ContextAction]:
        actions = [
            ContextAction("sort-queue", "Sort naturally", target, bool(self.queue.entries())),
            ContextAction("clear-completed", "Clear watched/completed", target, self._queue_has_clearable()),
            ContextAction("clear-all", "Clear queue", target, bool(self.queue.entries())),
            ContextAction("undo", "Undo latest removal", target, self.queue.undo_available),
        ]
        queue_target = self._context_queue_target()
        if queue_target is not None:
            actions.extend(self._context_actions_for_queue(queue_target))
        return actions

    def _context_queue_target(self) -> QueueTarget | None:
        index = self._queue_index()
        if index is None:
            return None
        return QueueTarget(self.queue.entries()[index].id, self._root_generation)

    def _context_actions_for_queue(self, target: QueueTarget) -> list[ContextAction]:
        entry = next((item for item in self.queue.entries() if item.id == target.entry_id), None)
        if entry is None:
            return []
        offer = ResumeOffer(
            self.database.history_for(entry.path, root=self.root), self.watched_percent
        )
        return [
            ContextAction("play-now-queue", f"Play now · {entry.path.name}", target),
            ContextAction("resume-queue", "Resume", target, self._offer_requires_choice(offer)),
            ContextAction("start-over-queue", "Start over", target),
            ContextAction("play-next-queue", "Play next", target),
            ContextAction("move-up", "Move up", target),
            ContextAction("move-down", "Move down", target),
            ContextAction("remove-queue", "Remove from queue", target),
            ContextAction("details-queue", "Details", target),
        ]

    def _context_actions_for_player(self, target: PlayerTarget) -> list[ContextAction]:
        current = self.queue.current()
        connected = self.no_vlc or self.controller.client is not None
        return [
            ContextAction("pause", "Pause / resume", target, current is not None and connected),
            ContextAction("previous", "Previous", target, bool(self.queue.entries())),
            ContextAction("seek-back", "Seek back 10s", target, connected and current is not None),
            ContextAction("seek-forward", "Seek forward 10s", target, connected and current is not None),
            ContextAction("next", "Next", target, bool(self.queue.entries())),
            ContextAction("reconnect", "Reconnect", target, not self.no_vlc),
            ContextAction("active-details", "Active player details", target, current is not None),
            ContextAction(
                "remaining",
                "Show remaining time",
                target,
                current is not None and self.controller.status.duration_ms > 0,
            ),
            ContextAction("last-notice", "Show full last notice", target, len(self._notice) > 0),
            ContextAction("help", "Help", target),
            ContextAction("quit", "Quit", target),
        ]

    def _open_context_menu(
        self, actions: list[ContextAction], source: Widget | None, x: int | None, y: int | None
    ) -> None:
        if not actions:
            self.update_status("No actions are available here")
            return
        self._source_focus = source
        self.push_screen(
            ActionMenu(actions, source, x or 1, y or 1),
            self._context_menu_result,
        )

    def _open_row_menu(self, row: BrowserListItem | QueueListItem, x: int | None, y: int | None) -> None:
        if isinstance(row, BrowserListItem):
            file_target = self._context_library_target(
                next((entry for entry in self._browser_source_entries if entry.path == row.path), None)
            )
            actions = self._context_actions_for_file(file_target) if file_target is not None else []
        else:
            queue_target = QueueTarget(row.entry_id, self._root_generation)
            actions = self._context_actions_for_queue(queue_target)
        self._open_context_menu(actions, row, x, y)

    def _open_section_menu(self, section: Literal["files", "queue"], source: Widget | None, x: int, y: int) -> None:
        target = SectionTarget(section, self._root_generation)
        actions = (
            self._context_actions_for_files_section(target)
            if section == "files"
            else self._context_actions_for_queue_section(target)
        )
        self._open_context_menu(actions, source, x, y)

    def _context_menu_result(self, result: ContextAction | None) -> None:
        source = self._source_focus
        self._source_focus = None
        if source is not None and source.is_attached:
            source.focus()
        if result is not None:
            self.run_worker(self._dispatch_context_action(result), exclusive=True)

    def _valid_file_target(self, target: FileTarget) -> bool:
        if target.root_generation != self._root_generation:
            self.update_status("Action expired after the library root changed")
            return False
        try:
            path = target.path.resolve(strict=False)
        except RuntimeError:
            self.update_status("Action target is no longer valid")
            return False
        if not is_beneath(path, self.root):
            self.update_status("Action refused a path outside the library root")
            return False
        if not path.exists():
            self.update_status("Action target is no longer available")
            return False
        if not path.is_file() and not path.is_dir():
            self.update_status("Action target is not a regular file or folder")
            return False
        if path.is_file() and path.suffix.casefold() not in VIDEO_EXTENSIONS:
            self.update_status("Action target is not a supported video")
            return False
        return True

    def _valid_queue_target(self, target: QueueTarget) -> QueueEntry | None:
        if target.root_generation != self._root_generation:
            self.update_status("Action expired after the library root changed")
            return None
        entry = next((item for item in self.queue.entries() if item.id == target.entry_id), None)
        if entry is None:
            self.update_status("Queue action expired because that entry was removed")
            return None
        try:
            stat = entry.path.stat()
            fingerprint = (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns)
            if fingerprint != self.database.media_fingerprint(entry.media_id):
                self.update_status("Queue action refused a replaced media file")
                return None
        except (OSError, RuntimeError):
            self.database.set_state(entry.id, "missing")
            self.update_status("Queue action refused missing media")
            return None
        return entry

    async def _dispatch_context_action(self, action: ContextAction) -> None:
        target = action.target
        if isinstance(target, FileTarget) and not self._valid_file_target(target):
            return
        queue_entry: QueueEntry | None = None
        if isinstance(target, QueueTarget):
            queue_entry = self._valid_queue_target(target)
            if queue_entry is None:
                return
        if isinstance(target, SectionTarget) and target.root_generation != self._root_generation:
            self.update_status("Action expired after the library root changed")
            return
        if isinstance(target, PlayerTarget) and target.root_generation != self._root_generation:
            self.update_status("Player action expired after the library root changed")
            return
        key = action.key
        if key == "files-toggle":
            self._toggle_section("files")
        elif key == "queue-toggle":
            self._toggle_section("queue")
        elif key == "open-root":
            self.action_open_root()
        elif key == "parent":
            await self.action_parent()
        elif key == "search-filter":
            self.action_search_filter()
        elif key == "sort-files":
            self.browser_reverse = not self.browser_reverse
            await self.refresh_browser()
        elif key in {"add-selection", "add-end-file"}:
            paths = list(target.selected_paths) if isinstance(target, FileTarget) and target.selected_paths else [target.path] if isinstance(target, FileTarget) else []
            await self._add_paths(paths)
        elif key in {"next-selection", "play-next-file", "play-next-queue"}:
            if isinstance(target, QueueTarget):
                assert queue_entry is not None
                paths = [queue_entry.path]
            elif isinstance(target, FileTarget):
                paths = list(target.selected_paths) if target.selected_paths else [target.path]
            else:
                paths = []
            await self._play_next_paths(paths)
        elif key in {"add-play-selection", "add-play-file"}:
            paths = list(target.selected_paths) if isinstance(target, FileTarget) and target.selected_paths else [target.path] if isinstance(target, FileTarget) else []
            await self._add_and_play_paths(paths)
        elif key == "open-folder" and isinstance(target, FileTarget):
            await self._open_browser_folder(target.path)
        elif key in {"play-now-file", "resume-file", "start-over-file"} and isinstance(target, FileTarget):
            browser_entry = BrowserEntry(target.path, target.path.name, False, True)
            choice = {"resume-file": "resume", "start-over-file": "start_over"}.get(key)
            if choice == "resume" and not self._offer_requires_choice(self._resume_offer_for_path(browser_entry.path)):
                self.update_status("No trustworthy resume point is recorded for this video")
            elif key == "play-now-file":
                await self._activate_browser_video(browser_entry)
            else:
                await self._activate_browser_video(browser_entry, choice=choice)
        elif key == "select-file" and isinstance(target, FileTarget):
            if target.path in self.selected_paths:
                self.selected_paths.remove(target.path)
            else:
                self.selected_paths.add(target.path)
            self._render_browser_rows()
        elif key == "clear-selection":
            await self.action_clear_selection()
        elif key == "details-file" and isinstance(target, FileTarget):
            await self._open_details(BrowserEntry(target.path, target.path.name, False, True), pane="browser")
        elif key == "sort-queue":
            selected_entry_id = self._queue_selected_id()
            self.queue.sort_natural()
            self.refresh_queue(selected_entry_id=selected_entry_id, selection_captured=True)
            self.update_status("Queue sorted naturally")
        elif key == "clear-completed":
            self.action_clear_completed()
        elif key == "clear-all":
            self.action_clear_all()
        elif key == "undo":
            await self.action_undo()
        elif key in {"play-now-queue", "resume-queue", "start-over-queue"} and isinstance(target, QueueTarget):
            index = next((i for i, item in enumerate(self.queue.entries()) if item.id == target.entry_id), None)
            if index is None:
                return
            choice = {"resume-queue": "resume", "start-over-queue": "start_over"}.get(key)
            await self._play_queue_index(index, choice=choice)
            self.refresh_queue()
        elif key == "move-up":
            self.action_move_up()
        elif key == "move-down":
            self.action_move_down()
        elif key == "remove-queue":
            await self.action_remove_target(target)
        elif key == "details-queue" and isinstance(target, QueueTarget):
            current_entry = next((item for item in self.queue.entries() if item.id == target.entry_id), None)
            if current_entry is not None:
                await self._open_details(BrowserEntry(current_entry.path, current_entry.path.name, False, True), pane="queue")
        elif key == "pause":
            await self.action_pause()
        elif key == "previous":
            await self.action_previous()
        elif key == "next":
            await self.action_next()
        elif key == "seek-back":
            await self.action_seek(-10)
        elif key == "seek-forward":
            await self.action_seek(10)
        elif key == "reconnect":
            await self.action_reconnect()
        elif key == "active-details":
            current = self.queue.current()
            if current is not None:
                await self._open_details(BrowserEntry(current.path, current.path.name, False, True), pane="queue")
        elif key == "remaining":
            status = self.controller.status
            if status.duration_ms > 0:
                remaining = max(0, status.duration_ms - status.position_ms)
                self.update_status(f"Remaining: {self._format_time(remaining)}")
            else:
                self.update_status("Remaining time is unknown")
        elif key == "last-notice":
            self.push_screen(NoticePrompt(self._notice))
        elif key == "help":
            self.action_help()
        elif key == "quit":
            self.action_quit_app()

    async def _add_paths(self, paths: list[Path]) -> None:
        if not paths:
            self.update_status("Nothing to add")
            return
        try:
            self.queue.add(paths)
        except (OSError, PathError, RuntimeError, ValueError) as exc:
            self.update_status(f"Add failed: {exc}")
            return
        self.selected_paths.difference_update(paths)
        await self.refresh_browser()
        self.refresh_queue()
        self.update_status(f"Added {len(paths)} video{'s' if len(paths) != 1 else ''} to queue")

    async def _play_next_paths(self, paths: list[Path]) -> None:
        if not paths:
            self.update_status("Nothing to place next")
            return
        try:
            self.queue.play_next(paths)
        except (OSError, PathError, RuntimeError, ValueError) as exc:
            self.update_status(f"Play next failed: {exc}")
            return
        self.selected_paths.difference_update(paths)
        await self.refresh_browser()
        self.refresh_queue()
        self.update_status(f"Placed {len(paths)} video{'s' if len(paths) != 1 else ''} next")

    async def _add_and_play_paths(self, paths: list[Path]) -> None:
        if not paths:
            self.update_status("Nothing to play")
            return
        if not self._require_vlc_for_play():
            return
        offer = self._resume_offer_for_path(paths[0])
        if self._offer_requires_choice(offer):
            def chosen(value: str | None) -> None:
                if value is not None:
                    self.run_worker(self._finish_add_and_play(paths, choice=value), exclusive=True)

            self.push_screen(ResumePrompt(offer), chosen)
            return
        await self._finish_add_and_play(paths)

    def action_search_filter(self) -> None:
        def chosen(value: tuple[str, str] | None) -> None:
            if value is None:
                return
            self.search_query, self.history_filter = value[0].casefold(), value[1]
            self.run_worker(self.refresh_browser(), exclusive=True)

        self.push_screen(SearchFilterPrompt(self.search_query, self.history_filter), chosen)

    async def action_remove_target(self, target: ContextTarget) -> None:
        if not isinstance(target, QueueTarget):
            return
        entries = self.queue.entries()
        index = next((i for i, item in enumerate(entries) if item.id == target.entry_id), None)
        if index is None:
            self.update_status("Queue action expired because that entry was removed")
            return
        queue = self.query_one("#queue", ListView)
        queue.index = index
        await self.action_remove()

    async def action_reconnect(self) -> None:
        if self.no_vlc:
            self.update_status("VLC controls are disabled in offline mode")
            return
        try:
            await self.controller.reconnect()
        except (OSError, VLCError) as exc:
            self.update_status(f"Reconnect failed: {exc}")
        else:
            self.update_status("VLC reconnected; playback was not restarted")

    def action_context_menu(self) -> None:
        if self._browser_has_focus():
            source = self.focused
            self._open_section_menu("files", source, source.region.x if source else 1, source.region.y if source else 1)
        elif self._queue_has_focus():
            source = self.focused
            self._open_section_menu("queue", source, source.region.x if source else 1, source.region.y if source else 1)
        else:
            source = self.query_one("#player-menu", Button)
            current = self.queue.current()
            self._open_context_menu(
                self._context_actions_for_player(PlayerTarget(current.path if current else None, self._root_generation)),
                source,
                source.region.x,
                source.region.y,
            )

    def action_toggle_files(self) -> None:
        self._toggle_section("files")

    def action_toggle_queue(self) -> None:
        self._toggle_section("queue")

    def action_help(self) -> None:
        self.push_screen(HelpPrompt())

    async def _finish_quit(self, choice: str) -> None:
        await self.controller.stop(stop_vlc=choice == "stop")
        self.exit()

    def action_quit_app(self) -> None:
        def chosen(value: str | None) -> None:
            if value:
                self.run_worker(self._finish_quit(value), exclusive=True)

        self.push_screen(QuitPrompt(), chosen)

    def action_clear_completed(self) -> None:
        if not self._queue_has_clearable():
            self.update_status("No watched/completed queue entries to clear")
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

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        event.stop()
        button = event.button
        button_id = button.id
        if button.has_class("browser-check"):
            row = self._row_ancestor(button)
            if isinstance(row, BrowserListItem):
                view = self.query_one("#browser", ListView)
                view.index = list(view.children).index(row)
                await self.action_select()
            return
        if button_id == "files-toggle":
            self.action_toggle_files()
        elif button_id == "queue-toggle":
            self.action_toggle_queue()
        elif button_id == "files-open":
            self.action_open_root()
        elif button_id == "files-search":
            self.action_search_filter()
        elif button_id == "files-add":
            await self.action_add_selected()
        elif button_id == "queue-play":
            await self.action_pause()
        elif button_id == "queue-next":
            await self.action_next()
        elif button_id == "queue-clear":
            self.action_clear_completed()
        elif button_id == "player-previous":
            await self.action_previous()
        elif button_id == "player-pause":
            await self.action_pause()
        elif button_id == "player-next":
            await self.action_next()
        elif button_id == "files-actions":
            self._open_section_menu("files", button, button.region.x, button.region.y)
        elif button_id == "queue-actions":
            self._open_section_menu("queue", button, button.region.x, button.region.y)
        elif button_id == "player-menu":
            current = self.queue.current()
            self._open_context_menu(
                self._context_actions_for_player(PlayerTarget(current.path if current else None, self._root_generation)),
                button,
                button.region.x,
                button.region.y,
            )

    async def action_open_root_from_menu(self) -> None:
        self.action_open_root()
