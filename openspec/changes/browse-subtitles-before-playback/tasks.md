## 1. Offline subtitle model and persistence

- [ ] 1.1 Add immutable offline candidate/snapshot/target models with embedded-or-sidecar source, bounded metadata, ephemeral identity, and planned-choice state; verify focused model validation and label tests pass.
- [ ] 1.2 Extend semantic descriptors to version 2 with optional source and sidecar variant while accepting existing version-1 rows source-agnostically; verify database restart, malformed-row, privacy, and backward-compatibility tests pass without changing queue/history data.
- [ ] 1.3 Generalize remembered and English matching to offline candidates with deterministic source/variant/trait ordering; verify tests cover generated sidecar preference, embedded-versus-sidecar ties, unknown metadata, Off, and version-1 matching.

## 2. Bounded ffprobe discovery

- [ ] 2.1 Implement an injectable ffprobe adapter using an argument-vector subprocess, canonical file URI, narrow JSON projection, timeout, output ceiling, concurrency cap, and cancellation cleanup; verify tests cover successful embedded streams plus missing executable, nonzero exit, timeout, malformed/excess output, and no-shell/path-leading-dash safety.
- [ ] 2.2 Implement immediate-sibling sidecar association for the documented allowlist and same-stem convention, including one logical `.idx`/`.sub` candidate and root/symlink/regular-file checks; verify focused tests exclude hidden, unrelated, missing, and root-escaping files without reading subtitle contents.
- [ ] 2.3 Parse bounded ffprobe tags/dispositions and sidecar suffix tokens into normalized language, characteristics, source, and variant metadata; verify fixture tests cover common embedded metadata, `Episode.en.whisper.srt`, forced/SDH/signs labels, unknown values, and deterministic ordering.
- [ ] 2.4 Add session-only successful-result caching and request coalescing keyed by media and sibling-directory identities, with root-change and explicit invalidation; verify async tests cover cache hits, mutation invalidation, stale completion rejection, and bounded concurrent subprocesses.

## 3. Show preference workflow and TUI

- [ ] 3.1 Expose Subtitles on every supported canonical Files and Queue video row regardless of VLC state while preserving root-generation target capture; verify Textual tests cover inactive, current, empty/no-VLC, keyboard `Shift+F10`, and stale-row menus.
- [ ] 3.2 Add a cancellable loading flow and adapt the picker to distinguish embedded/sidecar candidates and planned remembered/English choices from VLC-confirmed active choices; verify compact-terminal keyboard/mouse, focus restoration, cancellation, empty discovery, and error-notice tests pass.
- [ ] 3.3 Persist an inactive choice or Off only as a whole-show preference when remembering is enabled and show identity is confident, without launching VLC or mutating the queue; verify TUI/controller tests cover success, disabled remembering, uncertain show identity, preference replacement, and unchanged playback/queue state.
- [ ] 3.4 Resolve and display the stored whole-show preference on another episode, including generated sidecars, and display English fallback only as planned; verify cross-episode and restart tests pass without persisting absolute paths or probe/VLC identifiers.

## 4. VLC reconciliation and sidecar application

- [ ] 4.1 Add a typed VLC 3 `addsubtitle` operation that accepts only a freshly validated root-confined regular sidecar and sends its canonical file URI through the authenticated loopback client; verify request-fixture tests cover exact parameters, allowlist closure, escaping/missing paths, and credential/path-safe errors.
- [ ] 4.2 Rework automatic application under the controller transition lock to rediscover an unchanged target, map embedded choices semantically to VLC tracks, avoid duplicate sidecar attachment, attach a selected sidecar, re-read tracks, and select/confirm once per generation; verify controller tests cover success, delayed tracks, stale media/playlist/root, changed sidecars, explicit-choice race, bounded failure, and uninterrupted playback.
- [ ] 4.3 Preserve the existing current-row picker semantics while reconciling offline and VLC snapshots, never presenting a planned choice as active; verify focused tests cover VLC payloads with and without active flags and generation changes while the picker is open.
- [ ] 4.4 Extend the opt-in real-VLC test to generate a disposable video plus embedded and sidecar subtitles, then verify discovery, `addsubtitle`, selection, Off, and cleanup against installed VLC 3 without touching user media.

## 5. Installation, documentation, and release verification

- [ ] 5.1 Add a dependency diagnostic and update installation/help/README text for the required FFmpeg `ffprobe`, same-stem sidecar convention, whole-show behavior, supported sidecar formats, planned-versus-active markers, and failure recovery; verify CLI/help documentation tests and missing-ffprobe output.
- [ ] 5.2 Run `.venv/bin/pytest`, `.venv/bin/ruff check .`, `.venv/bin/mypy src`, and `openspec validate browse-subtitles-before-playback --strict`; resolve every failure.
- [ ] 5.3 Run the opt-in generated-media real-VLC test on an environment with VLC 3 and ffprobe and record a passing result rather than an environment skip.
- [ ] 5.4 Reinstall the completed CLI into the global tool environment with the established installer, then verify the globally resolved `vlcq` executes the updated build and can inspect a generated embedded/sidecar fixture before playback.
