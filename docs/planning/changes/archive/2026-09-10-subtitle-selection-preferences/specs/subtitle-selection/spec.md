## Purpose

Provide safe subtitle control in vlcq and deterministic preferences that reduce repeated subtitle selection across episodes while preserving explicit user choices.

## ADDED Requirements

### Requirement: Current-media subtitle menu
vlcq SHALL expose a Subtitles action from the Files and Queue row context menus, including the keyboard context-menu path, when that row identifies the media currently loaded in the owned VLC process. The action SHALL open a list containing Off and every subtitle track VLC reports for that current playback generation, with the active choice identified. When the supported VLC payload does not expose whether a specific track or Off is active, vlcq SHALL identify VLC's preserved current/default state as active and SHALL NOT guess a more specific choice; a successful generation-local vlcq choice or recognized active metadata SHALL identify the corresponding track or Off instead. Track labels SHALL present available language, title, and recognized characteristics without inventing missing metadata. For an inactive, stale, unavailable, or ambiguously matched row, subtitle selection SHALL be unavailable and SHALL NOT load, enqueue, probe, or start that media.

#### Scenario: Select a reported subtitle
- **WHEN** the user opens Subtitles on the matching current row and selects a reported track
- **THEN** vlcq selects that track for the same validated playback generation and reports the result without changing queue order or playback position

#### Scenario: Turn subtitles off
- **WHEN** the user selects Off for the matching current row
- **THEN** vlcq disables subtitles for that playback generation and identifies Off as the active choice

#### Scenario: Open an inactive row menu
- **WHEN** the user opens a context menu for media that is not the validated current item
- **THEN** subtitle selection is disabled or omitted and vlcq does not play or inspect that media to discover tracks

#### Scenario: Current item changes before selection
- **WHEN** playback changes after the subtitle list opens but before a choice is applied
- **THEN** vlcq rejects the stale choice and does not apply it to the new item

### Requirement: Explicit choices and remembered show matching
vlcq SHALL provide a persistent Remember subtitles by show toggle. While enabled, a successful explicit choice of a subtitle track or Off SHALL store a preference for the conservatively inferred show containing the current media. A remembered track preference SHALL describe language and recognized characteristics, including full-dialogue, signs/songs, forced, and SDH when identifiable, rather than storing VLC's ephemeral track identifier. On a later item confidently identified as the same show, vlcq SHALL attempt the best available semantic match. A remembered explicit choice, including Off or a non-English language, SHALL take priority over the general English preference. Disabling the toggle SHALL stop automatic use and recording of show preferences without deleting existing remembered preferences.

#### Scenario: Match a later episode
- **WHEN** remembering is enabled, the user explicitly selects an English SDH full-dialogue track, and a later item is confidently identified as the same show
- **THEN** vlcq selects the best available track matching English, SDH, and full-dialogue characteristics even when its VLC track identifier or title differs

#### Scenario: Remember subtitles off
- **WHEN** remembering is enabled, the user explicitly selects Off, and another item is confidently identified as the same show
- **THEN** vlcq keeps subtitles off for that item even when English tracks are available

#### Scenario: Disable remembering
- **WHEN** the user disables Remember subtitles by show
- **THEN** existing show preferences remain stored but are neither recorded nor automatically applied until the toggle is re-enabled

#### Scenario: Show identity is uncertain
- **WHEN** vlcq cannot confidently infer that the new media belongs to a show with a remembered preference
- **THEN** it does not apply a show preference and proceeds to the general fallback policy

### Requirement: English-first fallback
vlcq SHALL provide a persistent Prefer English subtitles preference that is enabled by default. For each newly validated current item to which no enabled remembered show preference applies, vlcq SHALL attempt to select an English full-dialogue subtitle first and then another English subtitle if no full-dialogue English track is available. Within the same class, deterministic ranking SHALL prefer non-forced over forced-only and non-SDH over SDH unless the available metadata leaves those traits unknown. If the preference is disabled or no English track can be identified, vlcq SHALL preserve VLC's current/default subtitle choice.

#### Scenario: English full dialogue is available
- **WHEN** a new current item has no applicable remembered show preference and reports English full-dialogue and English signs/songs tracks
- **THEN** vlcq selects the English full-dialogue track

#### Scenario: Only another English kind is available
- **WHEN** no English full-dialogue track is identifiable but another English subtitle track is identifiable
- **THEN** vlcq selects the highest-ranked English track deterministically

#### Scenario: No identifiable English track
- **WHEN** VLC reports no track with English language metadata or an unambiguous English label
- **THEN** vlcq preserves VLC's existing subtitle choice instead of guessing from track order

#### Scenario: Disable English preference
- **WHEN** the user disables Prefer English subtitles and no enabled remembered show preference applies
- **THEN** vlcq preserves VLC's existing subtitle choice

### Requirement: Bounded automatic application
Automatic subtitle selection SHALL run at most once successfully for each validated playback generation, after VLC reports the current media and its subtitle tracks. It SHALL never override a successful explicit selection made for that generation. Missing, malformed, delayed, or changing VLC track metadata and selection failures SHALL be recoverable: vlcq SHALL preserve playback, avoid repeated command loops, provide bounded user feedback, and retry only while establishing that generation or after an explicit user action.

#### Scenario: Tracks appear after playback starts
- **WHEN** the current item is validated before VLC exposes its subtitle tracks
- **THEN** vlcq performs bounded retries for that same generation and applies the applicable preference once tracks become available

#### Scenario: Manual choice precedes automatic choice
- **WHEN** the user successfully selects a track before an automatic preference is applied for that generation
- **THEN** vlcq records the explicit result as applicable and does not later override it automatically

#### Scenario: VLC rejects automatic selection
- **WHEN** VLC rejects a selected track or reports inconsistent track metadata
- **THEN** playback continues unchanged, automatic attempts for that generation stop after a bounded limit, and the failure is available through vlcq's notice mechanism

### Requirement: Private durable subtitle preferences
Global subtitle toggles and remembered show descriptors SHALL persist in vlcq's private local database across restart and root changes without exposing absolute paths in user-facing output. Persistence changes SHALL migrate transactionally while preserving queue and playback-history data. Subtitle preferences SHALL NOT cause network access, subtitle downloads, or modification of media files.

#### Scenario: Restart vlcq
- **WHEN** vlcq restarts after subtitle toggles or a show preference were saved
- **THEN** the saved values are restored and remain subject to the same precedence and confidence rules

#### Scenario: Preference migration fails
- **WHEN** upgrading an existing database cannot create the subtitle preference storage atomically
- **THEN** the migration rolls back without partial schema changes or loss of existing queue and history data and reports recovery guidance

#### Scenario: Apply any subtitle preference
- **WHEN** vlcq discovers or selects subtitles
- **THEN** it communicates only with its authenticated loopback-owned VLC process and neither modifies the media nor contacts a non-loopback service
