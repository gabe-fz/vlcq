## Context

See `proposal.md` for motivation. Inspection of `src/vlcq/tui.py` found:

- `compose()` places two `Vertical` panes in a `Horizontal`, with multiple toolbar rows, a permanent details area, transport buttons, and two live status summaries.
- Browser file rows mount a Label, a full Button, and a history Static sequentially. This creates multi-line rows with detached history and oversized checkbox controls. `_history_label()` supplies “No recorded progress” for every unplayed file.
- `_render_status()` appends `_playback_summary` while `refresh_playback()` also renders the player separately. Button labels wrap in narrow columns. Compact mode is set on mount based on width, not designed around collapse and live resizing.
- Browser Labels use raw filename strings; bracketed release names must be explicitly treated as literal text rather than Rich markup.
- `on_click()` opens directory rows without first distinguishing right-click. There is no in-app context menu implementation.
- `_refresh_controls()` and `on_button_pressed()` depend on persistent button IDs. A redesign must replace those dependencies rather than keep invisible legacy controls.
- Existing history refresh and queue-row reuse deliberately preserve identity, highlight, and scrolling. Preserve those mechanisms.
- `tests/test_ux_e2e.py` clicks old toolbar IDs at 80x24 and 120x40; it needs behavioral replacements, not deletion. The prior `improve-playback-ux` change is task-complete but not archived, with uncommitted spec/design edits.

Baseline inspected: `/var/folders/ck/9t77dr5s2rv6djfphklxpmvc0000gn/T/pi-clipboard-45811620-42a6-4c78-b3f4-8f4c20474714.png`. It contains private media names and must not be committed.

## Goals / Non-Goals

**Goals:** A filename-first, mouse-friendly TUI with predictable action targets, stable viewport use, and an implementation separable from queue/controller semantics.

**Non-Goals:** New playback policies, storage changes, native OS menus, persistent pane geometry, terminal-emulator reconfiguration, or repair of the independent VLC readiness error shown in the baseline.

## Decisions

### 1. A stacked layout at every width

Use two lightweight collapsible sections with one-line headers and an expanded body that fills its allocated space. Both start expanded with equal space; an expanded sibling absorbs freed space. Both may be collapsed. Preserve mounted list state or explicitly restore it on expansion; do not refresh/rebuild the list merely because its visibility changes. When collapsing the focused body, focus its header and ensure hidden content no longer receives shortcuts. Keep header controls pinned while path text truncates.

Approximate populated layout (not exact colors or glyphs):

```text
v Files  plex-tv / Season 1              2 selected (1 hidden)  ...
  > Subfolder
  [x] Episode 01.mkv                         queued  08:12/22:52
  [ ] Episode 02.mkv
  [ ] Episode 03.mkv                                 completed
v Queue  3                                                    ...
  > Episode 01.mkv                                  playing
    Episode 03.mkv                                  queued
    Episode 02.mkv                                  queued
Playing · Episode 01.mkv · 08:12 / 22:52 · connected             ...
-------------------- progress -------------------------- 36%
Ready
```

No top application Header, permanent keyboard Footer, details panel, or toolbar grid is needed. Put shortcut documentation in Help; tooltips/accessible labels name the small menu/disclosure controls. The idle bottom area can be shorter, but playback uses at most three lines: player, progress, notice. Keep progress free of duplicate ETA/readouts.

**Alternative rejected:** A responsive side-by-side mode would reintroduce narrow filename columns and two layout models. Simply reducing button heights would retain the excess controls and detached rows.

### 2. One-line rows with semantic text

Use a shared row presentation model with independent filename, checkbox/selection, state, and history regions. Rich `Text` or markup-disabled widgets render filenames literally. Style text spans explicitly so the ListView highlight does not erase semantic distinctions: blue/cyan folders, readable video filenames, accent selection/queued markers, amber in-progress/paused, green completion, red missing/failed, and muted inactive states. Include icons or short words so color is not the only signal.

No-history rows render no badge; Details explicitly reports absent history and unknown values. Known history remains secondary on the filename's row. In tight widths reserve selection and queue-state indicators, then truncate the filename/secondary text without wrapping; Details exposes all fields. Inline checkbox hit targets occupy a few cells, not a Button with default height. Preserve literal bracketed names and test focused and unfocused rows.

**Alternative rejected:** Hiding every history field would remove useful resumption/completion cues. Repeating absence is less useful than showing positive evidence.

### 3. One context-action model, multiple entry points

Create typed action descriptors (label, enabled predicate, target, callback) shared by right-click, header `...`, keyboard menu access (`Shift+F10`), and the bottom menu. Use a small Textual modal/popup action list, clamped to the viewport with scroll overflow. Keep the popup separate from the list so opening it does not resize the screen. Existing modal confirmations retain compact action rows.

Suggested menu grouping:

- Folder: Open, Up when applicable, Open root, Search/filter, Clear selection when applicable.
- File: Play now, Resume when meaningful, Start over, Select/deselect, Add to end / Play next for the declared batch or clicked item, explicit batch Add & play, Details. Label batch operations with counts and hidden counts.
- Queue row: Play now, Resume, Start over, Play next, Move up/down, Remove from queue, Details; queue-wide sort, undo and confirmed clear remain available through the Queue header menu.
- Files header: highlighted-item actions plus open root, up, search/filter, sort, clear selection.
- Bottom: transport/seek, reconnect, active-player details (including remaining time), last full notice, Help, Quit.

Right-click must be intercepted before directory activation and checkbox logic. It changes highlight but not selection/playback. Capture canonical path plus root generation for file actions and stable queue entry ID for queue actions, not a mutable row index. Resolve/revalidate on execution. Closing the menu restores focus to its source if still visible. Polling must not retarget an open menu. Block app shortcuts from leaking through modal menus and search inputs.

Move enablement and dispatch out of `_refresh_controls()`'s button lookups into shared action logic. Invoke existing queue/controller operation paths, preserving resume cancellation, active-item stop, bounded undo and path validation. Do not implement independent mutation logic inside menu widgets.

**Alternative rejected:** An OS-native menu is terminal-dependent. Right-click alone is inaccessible when a terminal consumes it; compact visible triggers and keyboard access provide the same functions.

### 4. Details and discovery on demand; status only once

Search/filter opens a compact input/modal from Files; active search/filter and nonzero selection/hidden counts remain summarized in the header after dismissal. Preserve current directories-through-filter and cross-folder selection behavior. A Details modal shows the requested item, not whichever row polling last touched; it is read-only and includes full path and history. Use a bottom player formatter as the only source of visible live playback text. Separate short notices from full error details and never append the player summary to the notice. Idle/disconnected status must not look like zero-time playback evidence.

**Alternative rejected:** Keeping a selected-details strip wastes space and creates ambiguity between highlighted and playing media.

## Risks / Trade-offs

- [Right-click may be intercepted by a terminal] → Visible menu triggers, Shift+F10, real-PTY verification; no dependence on terminal-specific settings.
- [Menus target a stale row during polling/removal] → Capture stable identity, validate on dispatch, retain existing row reuse, and test mutation while open.
- [Collapse or popup focus leaks shortcuts] → Explicit focus restoration and modal routing tests, including text-entry shortcuts.
- [Fewer always-visible controls reduces initial discoverability] → Named section menu triggers, familiar disclosure/selection affordances, concise empty-state hints, Help rather than permanent prose.
- [80x24 space pressure] → One-line headers/rows, no permanent toolbar; assert at least five visible rows per expanded populated list and inspect rendered images at 80x24, 120x40, and 80x50, plus both collapse extremes.
- [Old spec conflicts get reintroduced during archive] → Synchronize the reviewed predecessor first, then apply this delta; never archive the predecessor over the redesigned main spec.

## Migration Plan

1. Preserve the existing uncommitted files. Review and synchronize `improve-playback-ux` before integrating this change's normative delta. This plan deliberately uses MODIFIED for history/mouse requirements introduced by that predecessor; do not relabel them ADDED to conceal ordering.
2. Implement with failing tests first, retaining queue/controller/storage APIs. Replace old button-ID tests with equivalent context-menu workflows and keep safety assertions.
3. Generate a synthetic fixture reproducing unplayed files, bracketed long names, selected/hidden items, recorded progress, active and failed queue states. Save pre-change and post-change rendered screenshots privately. Inspect actual rendered output, not just CSS/classes or text assertions. Verify terminal right-click delivery through an isolated PTY; if OS screenshots are unavailable, document that and obtain a post-install user screenshot before claiming visual acceptance.
4. Run focused tests, then full pytest, ruff and mypy. Existing real VLC smoke coverage can be run when available; mandatory real VLC integration verification applies if controller/process/HTTP behavior unexpectedly changes. Update README to the actual accepted interaction model.
5. After acceptance, install globally with `pipx install --force .`, verify the resolved `vlcq --help` and installed package version via the pipx environment's package metadata (`vlcq` currently has no `--version` option), and verify the installed UI against an isolated database. Do not disrupt a user's running playback or use their database for synthetic fixtures.
6. Validate OpenSpec strictly, synchronize/archive this change only after implementation evidence is complete, and commit/push scoped changes on main without including unrelated edits or private screenshots. Rollback is reinstalling the previous code; no database rollback is required because the schema is unchanged.
