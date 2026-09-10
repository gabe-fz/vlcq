## Context

See `proposal.md` for motivation and `specs/subtitle-selection/spec.md` for behavior. Today `VLCStatus` retains playback identity/timing only, `VLCClient.command` has a closed command allowlist, `PlaybackController` serializes media transitions and tracks playback generations, and the Textual row menus contain flat actions. SQLite schema 3 already has a private settings table but no structured subtitle or show-preference storage.

VLC 3's HTTP status representation and command names for subtitle tracks must be treated as a versioned adapter boundary. Track IDs are ephemeral and metadata quality varies by container, muxer, and subtitle naming. Automatic selection also races media startup and native successor transitions, so it must share the controller's validated generation/identity boundary.

## Goals / Non-Goals

**Goals:**
- Represent VLC subtitle tracks as validated domain objects independent of raw response shape.
- Apply one deterministic policy per playback generation with explicit choices winning all races.
- Infer show identity conservatively and store semantic preferences that survive differing episode track IDs.
- Keep all persistence local, bounded, transactional, and compatible with existing queue/history state.

**Non-Goals:**
- Parsing subtitle files, downloading metadata, media fingerprinting for show recognition, or modifying containers.
- Selecting tracks for inactive rows or controlling VLC's native context menu.
- Perfectly classifying arbitrary track names or treating uncertain labels as English.
- Synchronizing preferences between machines or exposing them through the progress-export format.

## Decisions

### Add a narrow VLC subtitle adapter

Introduce an immutable subtitle-track model with ephemeral ID, display title, normalized ISO-style language when confidently available, and tri-state semantic flags for full-dialogue, signs/songs, forced, and SDH. Parse only bounded scalar metadata from VLC's status/track response and reject malformed IDs. Add dedicated `subtitle_tracks()` and `select_subtitle()` client operations rather than exposing arbitrary command strings.

Before implementation, characterize the installed VLC 3 HTTP interface and lock its actual response and selection command into fixtures and an opt-in integration test. The adapter may support explicitly recognized VLC 3 payload variants, but unknown structures fail as unavailable rather than being guessed.

Alternative: invoke VLC through RC, AppleScript, or direct libVLC bindings. This would add a second control/authentication channel or dependency and weaken the existing single loopback control boundary.

### Put arbitration under the playback controller lock

The controller owns subtitle discovery and selection because it already validates current path, playlist ID, and playback generation under a transition lock. Maintain per-generation state: automatic attempts, successfully applied policy, and whether an explicit choice succeeded. Selection revalidates generation and media identity immediately before issuing a command. Native successor reconciliation creates a new generation and policy opportunity.

Use bounded delayed discovery attempts during startup, canceled by generation changes, stop, or reconnect. A successful explicit action marks the generation manual before recording its preference, preventing a delayed automatic task from overriding it. A successful automatic application ends automatic work for that generation; hard failures consume a bounded attempt and surface through the existing notice path.

Alternative: apply during every status poll. That is simpler but can repeatedly override native/manual VLC changes and produce command loops.

### Use deterministic semantic descriptors and ranking

Normalize language from structured metadata first, then conservative exact aliases in labels (`en`, `eng`, `English` with token boundaries). Normalize characteristics from structured flags where VLC provides them and conservative title tokens otherwise. Unknown remains distinct from false. A full-dialogue track is one not identified as forced-only or signs/songs; explicit `full/dialogue` labels strengthen that classification.

A remembered descriptor stores selected mode (`off` or `track`), normalized language, and known characteristic values. Matching uses a deterministic score: required mode, exact language, agreement on known kind flags, then full-dialogue/non-forced/non-SDH defaults and stable VLC-reported order. A candidate with a conflicting known language is ineligible. Characteristics are best-match criteria rather than an all-or-nothing requirement so later episodes can still match when metadata is less complete.

Without a remembered descriptor, English fallback ranks confidently English full-dialogue first, then other confidently English tracks; among equals it prefers non-forced and non-SDH, followed by stable source order. It never guesses English solely from track position.

Alternative: remember raw VLC IDs or exact titles. IDs are per-input and titles commonly differ by episode, making both unsuitable for cross-episode behavior.

### Infer a conservative non-path show key

Create a deterministic show-key helper from the canonical root-relative media path. For media below the root, use a normalized immediate parent directory plus the nearest recognizable season directory; strip common season wrappers from the display identity. For root-level media, require a recognizable episodic filename token such as `SnnEnn`, `nnxnn`, or an unambiguous `Episode nn`, and use only the normalized stable prefix before that token. Generic numeric suffixes alone do not establish a show. If normalization yields an empty/generic key or no episodic evidence at root, inference returns none.

Hash the normalized root-relative show identity before persistence. Scope the key with a hash of the canonical library root so similarly named folders in separate libraries do not share preferences and absolute paths are not stored in the preference table. This is conservative, explainable, and local; it can miss unconventional layouts rather than misapply a remembered choice.

Alternative: use only parent folders. That works for organized libraries but incorrectly groups unrelated root-level shows and season subfolders. Content hashing or online show lookup is expensive and conflicts with privacy/non-network constraints.

### Persist global settings and show descriptors separately

Increment the SQLite schema and transactionally add a `subtitle_preferences` table keyed by the scoped show-key hash, containing a bounded versioned JSON descriptor and update timestamp. Store the two booleans as validated settings keys (`remember_subtitles_by_show`, default false; `prefer_english_subtitles`, default true). Provide typed database methods; malformed values fall back safely and are reported rather than interpreted permissively. Upsert only after VLC confirms an explicit choice. Disabling remembering leaves rows intact.

The show-memory default is false because the requested feature is explicitly a toggle; English preference is true by default because the requested baseline is to always attempt English full dialogue first. Database serialization and file permissions remain unchanged.

Alternative: encode every descriptor in the general settings table. A dedicated table gives bounded validation, replacement, testing, and future cleanup without mixing dynamic keys with singleton application state.

### Use a subtitle picker and persistent preference actions

Add `Subtitles…` to matching current Files and Queue row menus. It opens a compact modal populated asynchronously from the controller with Off plus tracks, active marker, scrolling, keyboard/mouse activation, cancellation, and focus restoration. The target captures root generation, playback generation, queue/media identity, and is revalidated on application. Inactive rows omit or disable the action.

Place checkmarked `Remember subtitles by show` and `Prefer English subtitles` toggle actions in Queue/application overflow so they remain reachable with an empty queue. Toggle actions update persisted state and bounded notice feedback; enabling a setting affects the current generation only if no explicit choice has occurred and automatic policy has not already completed.

Alternative: flatten every track into the row menu. Track counts can be large, discovery is asynchronous, and the existing menu model is flat, so a dedicated picker keeps menus bounded.

## Risks / Trade-offs

- **[VLC 3 payloads differ across builds]** → Characterize the supported macOS build, isolate parsing, retain fixtures for known variants, and fail closed for unknown shapes.
- **[Labels misclassify subtitle characteristics]** → Prefer structured fields, use conservative token-boundary rules and tri-state values, and make explicit selection easy.
- **[Show inference yields false matches]** → Require folder or explicit episodic evidence, scope by library root, reject generic keys, and let users disable remembering.
- **[Track metadata arrives late]** → Use cancellable bounded retries tied to generation, never unbounded polling commands.
- **[Users change subtitles in VLC itself]** → vlcq guarantees precedence only for selections made in vlcq; automatic policy runs once and therefore will not continuously fight native changes.
- **[Remembered matching is approximate]** → Store semantics rather than IDs and use deterministic best-match scoring; preserve VLC default when no eligible candidate exists.

## Migration Plan

1. Add the new preference table and schema version in one existing transactional migration while preserving all prior data.
2. Initialize no show rows, default show remembering to disabled, and treat absent English preference as enabled.
3. Deploy VLC adapter, controller policy, and UI together so persisted settings are not active without selection support.
4. On rollback, close vlcq and restore a pre-upgrade private SQLite backup; older binaries continue to reject the newer schema rather than partially reading it.
