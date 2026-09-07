## Why

The current TUI devotes too much space to large buttons, repeated explanations, and duplicate playback information. File history appears detached from filenames, making the library harder to understand than a simple file list.

## What Changes

- Replace side-by-side control-heavy panes with full-width Files above Queue, independently collapsible, and a compact bottom status area.
- Render single-line, semantically colored rows: filenames first, selection and queue state clearly attached, and compact history only when evidence exists. Move full history and paths into an explicit Details view.
- Make right-click context menus the primary action surface, with small visible menu triggers and keyboard access as fallbacks. Preserve mouse-only operation without giant toolbars.
- Keep folder navigation, search, filters, batch selection, deterministic queue operations, resume decisions, and safe undo; remove repeated instructional prose from the populated main screen.
- **BREAKING (presentation only)**: replace persistent action toolbars and per-row “No recorded progress” text. Existing command-line interfaces, playback shortcuts, storage, and media-safety contracts remain intact.
- Continue the existing OpenSpec workflow rather than reinitializing it. Reconcile this delta after the existing `improve-playback-ux` change is reviewed and synchronized; do not silently overwrite its uncommitted work.

## Capabilities

### New Capabilities

None; this is a redesign of the existing TUI capability.

### Modified Capabilities

- `vlcq`: main-screen layout, compact history presentation, contextual controls, mouse workflows, collapse/resize behavior, and nonduplicated player status.

## Impact

- Primary implementation: `src/vlcq/tui.py`; regression and rendered-layout coverage in `tests/test_cli_tui.py` and `tests/test_ux_e2e.py`; user instructions in `README.md`.
- No new runtime dependency, SQLite migration, CLI incompatibility, controller/HTTP behavior change, or Finder integration change is intended.
- Existing OpenSpec requirements for visible primary-action buttons and explicit no-history row labels conflict with this redesign and will be replaced explicitly. Requirements added by `improve-playback-ux` are modified only after that predecessor is synchronized.
- Security/privacy/media safety: context actions reuse existing root-confined validation and controller operations. No destructive file operations, extra network access, credential exposure, or history mutation. Capture acceptance images using synthetic filenames; keep the user's screenshot private.
- Non-goals: fixing the screenshot's VLC readiness failure, replacing Textual, adding a native GUI, changing playback/progress semantics, file management, or persistent custom layouts.
