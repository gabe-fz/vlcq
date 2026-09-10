## MODIFIED Requirements

### Requirement: Current-media subtitle menu
vlcq SHALL expose a Subtitles action from Files and Queue row context menus, including the keyboard context-menu path, for each supported, canonical video file beneath the active library root. For inactive media, the action SHALL asynchronously inspect the file without loading, enqueueing, or playing it and SHALL list Off, every embedded subtitle stream reported by the local media inspector, and every safely associated sidecar subtitle. For current media, vlcq SHALL reconcile offline candidates with tracks reported by the same owned VLC playback generation before issuing a playback command. The picker SHALL distinguish embedded and sidecar candidates, present available language, title or sidecar variant, and recognized characteristics without inventing missing metadata, and indicate a resolved remembered show preference or English fallback separately from a choice confirmed active in VLC. When VLC cannot identify its exact active stream, vlcq SHALL continue to identify VLC's preserved current/default state rather than guessing. Discovery and picker opening SHALL NOT change queue order, playback position, or media files.

#### Scenario: Browse subtitles before playback
- **WHEN** the user opens Subtitles on an inactive supported video beneath the active root
- **THEN** vlcq lists its discoverable embedded and safely associated sidecar subtitles without starting VLC or changing the queue

#### Scenario: Inspect a current item
- **WHEN** the user opens Subtitles on the validated current row
- **THEN** vlcq reconciles discovered candidates with that playback generation and identifies only a VLC-confirmed choice as active

#### Scenario: Turn subtitles off for a show
- **WHEN** the user selects Off from an inactive episode whose show can be confidently inferred while show remembering is enabled
- **THEN** vlcq stores Off as that show's preference without starting playback

#### Scenario: Discovery target changes
- **WHEN** the root changes, the target disappears, or its canonical identity changes while offline discovery is pending
- **THEN** vlcq discards the result and does not show or persist a choice for the stale target

#### Scenario: Offline inspection is unavailable
- **WHEN** the required local inspector is absent, times out, emits malformed or excessive output, or cannot read the target
- **THEN** vlcq reports bounded actionable feedback, leaves playback and queue state unchanged, and does not infer tracks from incomplete output

#### Scenario: Select a reported subtitle
- **WHEN** the user selects a reported track on the matching current row
- **THEN** vlcq selects it for the same validated playback generation and reports the result without changing queue order or playback position

#### Scenario: Turn subtitles off
- **WHEN** the user selects Off on the matching current row
- **THEN** vlcq disables subtitles for that playback generation, identifies Off as active, and records the show preference only when remembering is enabled

#### Scenario: Open an inactive row menu
- **WHEN** the user opens a context menu for supported media that is not the validated current item
- **THEN** Subtitles is available for offline inspection without playing, loading, or enqueueing that media

#### Scenario: Current item changes before selection
- **WHEN** playback changes after a current-media subtitle list opens but before a choice is applied
- **THEN** vlcq rejects the stale VLC action and does not apply it to the new item

### Requirement: Explicit choices and remembered show matching
vlcq SHALL provide a persistent Remember subtitles by show toggle. While enabled, an explicit choice of a discovered embedded track, associated sidecar, or Off for media whose show is confidently inferred SHALL store a semantic preference for that whole show without requiring playback. A remembered track preference SHALL describe its embedded-or-sidecar source, language, recognized full-dialogue, signs/songs, forced, and SDH characteristics, plus a bounded non-path sidecar variant when identifiable; it SHALL NOT store ffprobe or VLC stream identifiers or absolute paths. On another item confidently identified as the same show, vlcq SHALL resolve the best available offline candidate and, once that item plays, reconcile it to the current VLC generation. A remembered explicit choice, including Off, a sidecar, or a non-English language, SHALL take priority over the general English preference. Disabling the toggle SHALL stop automatic use and recording of show preferences without deleting existing remembered preferences.

#### Scenario: Choose before playback for the whole show
- **WHEN** remembering is enabled and the user chooses a discovered subtitle on an inactive episode with a confident show identity
- **THEN** vlcq stores its semantic descriptor for the show and marks the best matching candidate as the resolved preference while browsing other episodes

#### Scenario: Match a later episode sidecar
- **WHEN** a remembered preference describes an English auto-generated sidecar and a later episode has a safely associated sidecar with matching semantics and variant
- **THEN** vlcq prefers that sidecar over otherwise similar embedded or differently named sidecar candidates

#### Scenario: Match a later episode
- **WHEN** the user chooses an embedded or sidecar subtitle and a later item is confidently identified as the same show
- **THEN** vlcq resolves the best available candidate matching the stored source, language, variant, and recognized characteristics even when episode-local paths, titles, or stream identifiers differ

#### Scenario: Remember subtitles off
- **WHEN** remembering is enabled, the user explicitly selects Off, and another item is confidently identified as the same show
- **THEN** vlcq keeps subtitles off for that item even when embedded or sidecar English subtitles are available

#### Scenario: Disable remembering
- **WHEN** the user disables Remember subtitles by show
- **THEN** existing show preferences remain stored but are neither recorded nor automatically applied until the toggle is re-enabled

#### Scenario: Show identity is uncertain
- **WHEN** vlcq cannot confidently infer a show for inactive media
- **THEN** the picker remains available for inspection but refuses to persist a pre-playback choice as a show preference and explains why

### Requirement: English-first fallback
vlcq SHALL provide a persistent Prefer English subtitles preference that is enabled by default. For media to which no enabled remembered show preference applies, vlcq SHALL resolve an English full-dialogue candidate first and then another English candidate if no full-dialogue choice is available, considering both embedded and safely associated sidecar subtitles. Within the same class, deterministic ranking SHALL prefer non-forced over forced-only and non-SDH over SDH unless available metadata leaves those traits unknown, and SHALL use stable source and discovery ordering to break remaining ties. If the preference is disabled or no English candidate can be identified, vlcq SHALL preserve VLC's current/default subtitle choice at playback.

#### Scenario: Browse an English fallback
- **WHEN** inactive media has no applicable remembered preference and contains an English full-dialogue candidate
- **THEN** the picker identifies that candidate as the planned English fallback without claiming it is active

#### Scenario: English full dialogue is available
- **WHEN** media with no applicable remembered show preference has English full-dialogue and English signs/songs candidates
- **THEN** vlcq resolves the English full-dialogue candidate

#### Scenario: Only another English kind is available
- **WHEN** no English full-dialogue candidate is identifiable but another English candidate is identifiable
- **THEN** vlcq resolves the highest-ranked English candidate deterministically

#### Scenario: Embedded and sidecar candidates are available
- **WHEN** equally ranked English candidates exist in the container and as associated sidecars
- **THEN** vlcq resolves the same candidate deterministically during browsing and playback

#### Scenario: No identifiable English candidate
- **WHEN** discovery finds no candidate with English language metadata or an unambiguous English label
- **THEN** vlcq does not guess English from stream position or an unrelated filename

#### Scenario: No identifiable English track
- **WHEN** playback exposes no track matching a resolved English candidate or no English candidate was resolved
- **THEN** vlcq preserves VLC's existing subtitle choice instead of guessing from track order

#### Scenario: Disable English preference
- **WHEN** the user disables Prefer English subtitles and no enabled remembered show preference applies
- **THEN** vlcq shows no planned automatic choice and preserves VLC's existing subtitle choice at playback

### Requirement: Bounded automatic application
Automatic subtitle selection SHALL run at most once successfully for each validated playback generation, after VLC reports the current media. It SHALL rediscover or validate the applicable offline candidate against the unchanged canonical media identity, map embedded choices semantically to VLC-reported tracks rather than reusing inspector identifiers, and safely attach a selected associated sidecar through a narrowly validated local VLC operation when VLC has not already exposed it. It SHALL never override a successful explicit current-generation selection. Missing, malformed, delayed, or changing discovery/VLC metadata and selection failures SHALL preserve playback, avoid repeated command loops, provide bounded feedback, and retry only while establishing that generation or after an explicit user action.

#### Scenario: Apply an embedded pre-playback preference
- **WHEN** playback begins for an episode with a remembered embedded preference
- **THEN** vlcq selects the best semantic match among tracks reported for that same validated VLC generation

#### Scenario: Tracks appear after playback starts
- **WHEN** the current item is validated before VLC exposes the track needed by the resolved preference
- **THEN** vlcq performs bounded retries for that same generation and applies the choice once safely available

#### Scenario: Apply a sidecar pre-playback preference
- **WHEN** playback begins for an episode whose resolved remembered preference is a canonical associated sidecar beneath the active root
- **THEN** vlcq safely makes that sidecar available to the current VLC generation and confirms the resulting track selection without altering the sidecar

#### Scenario: Sidecar changes before application
- **WHEN** the chosen sidecar disappears, changes identity, becomes non-regular, or resolves outside the active root before VLC application
- **THEN** vlcq rejects it, leaves playback running with its existing subtitle state, and reports bounded feedback

#### Scenario: Manual choice precedes automatic choice
- **WHEN** the user successfully selects a current-generation track before an automatic preference is applied
- **THEN** vlcq records the explicit result as applicable and does not later override it automatically

#### Scenario: VLC rejects automatic selection
- **WHEN** VLC rejects an embedded or sidecar choice or reports inconsistent track metadata
- **THEN** playback continues unchanged, automatic attempts for that generation stop after a bounded limit, and the failure is available through vlcq's notice mechanism

### Requirement: Private durable subtitle preferences
Global subtitle toggles and remembered show descriptors SHALL persist in vlcq's private local database across restart and root changes without exposing absolute paths in user-facing output or preference records. Persistence changes SHALL migrate transactionally while preserving queue and playback-history data. Offline discovery SHALL invoke only the configured local inspector against canonical target media and associated regular sidecars beneath the active root, with bounded runtime and output; it SHALL not use a shell, scan unrelated directories, read subtitle contents for display, or contact a network service. Subtitle operations SHALL NOT download subtitles or modify media and sidecar files.

#### Scenario: Restart vlcq
- **WHEN** vlcq restarts after an embedded, sidecar, or Off show preference was saved
- **THEN** the semantic preference is restored without a persisted absolute media or sidecar path

#### Scenario: Preference migration fails
- **WHEN** upgrading an existing descriptor cannot be completed atomically
- **THEN** the migration rolls back without partial schema changes or loss of existing queue and history data and reports recovery guidance

#### Scenario: Symlinked sidecar escapes the root
- **WHEN** a filename-matching sidecar resolves outside the active canonical library root
- **THEN** vlcq excludes it from discovery and never passes it to the inspector or VLC

#### Scenario: Inspect and apply subtitles locally
- **WHEN** vlcq discovers or applies subtitles
- **THEN** it uses only bounded local inspection and its authenticated loopback-owned VLC process and neither modifies media nor contacts a non-loopback service

#### Scenario: Apply any subtitle preference
- **WHEN** vlcq applies an embedded, sidecar, or Off preference
- **THEN** it uses only canonical root-confined inputs and the authenticated loopback-owned VLC process, without modifying media or contacting a non-loopback service
