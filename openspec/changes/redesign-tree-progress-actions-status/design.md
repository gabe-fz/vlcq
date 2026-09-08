## Context

See `proposal.md` for motivation and `specs/vlcq/spec.md` for the revised behavior contract. The current Textual UI uses a `ListView` populated from one `browser_path`, rebuilds that list when entering a folder, renders history as text appended to one-line labels, and exposes nearly all mouse actions through three ellipsis menus. Playback history already separates monotonic furthest progress from resume position and explicit completion evidence.

The design must preserve canonical-root confinement, fingerprint-matched read-only history, identity-based queue targeting, asynchronous filesystem work, and the compact 80-by-24 layout. There is no general configuration file or settings UI today.

## Goals / Non-Goals

**Goals:**

- Keep multiple expanded library branches visible without sacrificing checkbox selection, row context menus, or identity-safe action targets.
- Centralize percentage and watched classification so the TUI, filtering, resume decisions, clear-completed behavior, and JSON export agree.
- Render readable, non-interactive per-item bars without rebuilding unchanged rows during polling/history refresh.
- Expose frequent mouse actions in one-cell-high action bars and enlarge only the bounded player/status area.

**Non-Goals:**

- Persisting tree expansion between application launches.
- Reading media metadata or interpreting a filename as a show/episode.
- Measuring unique viewing coverage; furthest position remains the metric.
- Replacing SQLite history, changing VLC's natural-end detector, or marking the queue outcome `completed` merely because the threshold was crossed.

## Decisions

### 1. Use a lazily loaded flattened tree model in the existing list surface

Represent each visible Files item with canonical path, kind, depth, parent path, and expansion/loading state. Keep an `expanded_paths` set and a children cache keyed by canonical directory path. Build the visible list with a depth-first flattening pass, loading only root children and explicitly expanded branches. Folder disclosure toggles expansion in place; it does not replace a current-directory variable. Highlight restoration is path-based, and collapsing a parent leaves descendant selection intact.

This retains the current custom row composition (checkbox, literal Rich text, context targeting, and progress widget), which would be awkward to reproduce with labels inside Textual's generic `Tree`. It also allows stable row reuse keyed by canonical path rather than clearing and rebuilding the entire `ListView`.

Alternative considered: eagerly recurse through the whole root at startup. Rejected because large libraries, unreadable folders, and network-mounted paths would delay first paint and violate the responsiveness requirement.

### 2. Separate lazy browsing from explicit recursive search/filter discovery

Normal tree browsing calls the existing root-confined folder lister in worker threads for one branch at a time. Search and non-default history filters require whole-library results, so they run a cancellable recursive discovery job off the event loop. The scanner skips dot entries and unsupported files, resolves each candidate beneath the root, deduplicates canonical directories/files to avoid symlink cycles, and periodically checks a root/search generation token. Results include ancestor chains so matching files can be flattened with location context without mutating the user's saved expansion set. Clearing search restores that expansion set.

History identity checks and database reads remain bounded in chunks rather than issuing one unbounded query. UI application of each result verifies the current root generation and current query/filter token.

Alternative considered: search only already-loaded branches. Rejected because it would make search silently incomplete and conflict with the whole-tree mental model.

### 3. Derive watched state from one validated threshold

Add a small configuration resolver in `config.py` for `VLCQ_WATCHED_PERCENT`. It defaults to integer `90`, accepts only decimal whole values from 1 through 100, and raises a clear `ValueError` before opening/mutating the database when invalid. The CLI resolves it once and passes it to the TUI and progress export; tests and integrations may still pass an explicit constructor/function value. An environment setting is chosen over a new settings screen or database migration because the project currently has no settings UI/config-file format and the request requires configuration, not runtime adjustment.

Create shared pure helpers for clamped whole-number furthest percentage and watched classification:

- explicit `completion_observed` is always watched;
- otherwise watched is true only when duration is positive and `position_ms * 100 >= duration_ms * threshold`;
- unknown/non-positive duration never fabricates threshold completion;
- displayed percentages clamp to 0–100.

Use these helpers in Files/Queue presentation, filters, Details, resume/start behavior, automatic handling of already-watched media, Clear completed target selection, and version-1 progress export. Threshold classification remains derived: it does not rewrite explicit completion evidence, timestamps, or queue state and cannot trigger automatic advancement. Consequently changing the configured threshold reclassifies existing records reversibly on next launch. Clear completed includes entries with explicit completed queue outcome or watched history, while retaining the existing confirmation and safe active-item stop rules.

Alternative considered: persist `completion_observed=True` when progress crosses 90 percent. Rejected because changing the threshold could not reverse that mutation, it would conflate observed natural completion with a policy, and crossing the bar while playing could accidentally affect queue lifecycle.

### 4. Compose row content from semantic filename spans and a fixed progress component

Keep filenames literal by splitting only for presentation: tree disclosure/indent, filename stem, bracketed spans and numeric runs within the stem, punctuation, and extension receive distinct Rich styles, then concatenate to exactly the original name. This is syntax coloring only; no episode inference occurs. Folder names use their own folder style. Selection, queue state, and errors continue to have explicit glyphs/background/state text so color is never the sole signal.

For every item with positive progress, add a fixed-width read-only history component beside the flexible filename/state component. Render it with full-height block cells, a below-threshold fill color or watched fill color, an unfilled track, and an adjacent integer percent. For unknown duration, render an unknown track/`?%` rather than a zero-percent bar. At narrow widths, verbose time/history labels disappear before the bar; names truncate with ellipsis and never push controls off-screen. The bar widget ignores clicks, allowing only the separately identified bottom live bar to seek.

Alternative considered: use filename color to indicate progress. Rejected per the requested separation between filename-part colors and progress colors, and because queue/error/selection colors would become ambiguous.

### 5. Reuse existing actions behind compact direct buttons

Each one-line action bar dispatches existing action methods rather than implementing parallel behavior:

- Files: Open, Search/filter, Add, ellipsis.
- Queue: Play/pause, Next, Clear completed, ellipsis.
- Player: Previous, Play/pause, Next, ellipsis.

Buttons use compact glyph-plus-tooltip labels where terminal width requires it and full short labels where space permits. Their enabled state is refreshed from the same target/connection/selection predicates used by context actions. Files Add uses the existing explicit selection-or-highlight targeting. Clear completed retains confirmation. Ellipsis menus keep all less-common and contextual actions; right-click row menus remain intact. Focus restoration targets the originating direct or overflow button.

Alternative considered: remove the ellipsis menus after promoting common actions. Rejected because Details, reorder, undo, reconnect, help, quit, and other required mouse workflows still need a compact home.

### 6. Make the bottom area bounded but taller

Replace the current two one-cell player/progress lines plus notice with a fixed-height player/status container: summary line, two-cell visual live-progress region with adjacent percentage, one-line player action bar, and one-line notice. On the minimum supported terminal, CSS uses a compact variant that keeps the same information and controls bounded while preserving at least five visible items in each expanded pane. The exact bar glyph fill can occupy one or two terminal rows depending on available height, but its container height never changes during polling.

Live progress remains the only clickable seek surface and retains connected/current-path/known-duration checks. Item history bars are separate widget classes and are never accepted by seek hit-testing.

## Risks / Trade-offs

- **[Recursive search on a very large tree consumes time and memory]** → Run it off-loop, deduplicate directories, cancel stale generations, apply bounded chunks, and keep normal browsing lazy.
- **[Symlink aliases or cycles duplicate/escape content]** → Resolve every path, require root containment, and track visited canonical directories and files.
- **[Frequent tree flattening loses highlight or scroll]** → Key models and reusable row widgets by canonical path and restore highlight/scroll anchor by identity.
- **[Fixed bars crowd deeply indented filenames at 80 columns]** → Bound indentation display, elide optional metadata first, keep a compact minimum bar, and expose full path/history in Details.
- **[Threshold-derived completion differs from durable natural completion]** → Keep explicit evidence and queue outcome unchanged, expose the threshold in Details, and centralize all user-facing watched decisions in one helper.
- **[Environment-only configuration is less discoverable than a settings UI]** → Document `VLCQ_WATCHED_PERCENT`, its default/range, and invalid-value behavior in README and CLI help text where configuration is described.
- **[Additional status height reduces list space]** → Use fixed compact rows, test all supported viewport sizes, and collapse optional status styling rather than hiding controls.

## Migration Plan

1. Add threshold resolution and shared watched/percentage helpers without changing stored schema.
2. Introduce tree models/discovery and transition browser actions/tests from current-folder navigation to path-keyed expansion.
3. Add semantic filename rendering, per-item progress components, direct action bars, and the revised status container.
4. Update filtering, replay/resume decisions, clear-completed selection, Details, and progress export to use the shared threshold policy.
5. Run the complete test, lint, and type-check suite and reinstall the CLI globally before release.

Rollback requires only reinstalling the prior application version. No database migration or destructive conversion occurs; threshold-derived watched state disappears on rollback while explicit completion evidence and progress remain intact.
