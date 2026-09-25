# Project guidance

## Project context

vlcq is a macOS-only Python 3.12+ CLI and Textual TUI that owns a
deterministic queue for a dedicated VLC 3 process. It uses httpx for VLC's
authenticated loopback HTTP interface and SQLite for private, durable queue
and playback-progress state. Media operations must remain root-confined and
non-destructive. Run pytest, ruff, and mypy for implementation changes.

## Planning

### Proposals

- Identify security, privacy, and media-safety impact.
- Keep non-goals explicit when scope could expand beyond VLC queue control.

### Requirements

- Include scenarios for failure behavior and non-destructive path handling when relevant.

### Task checklists

- Include focused tests for every behavior change.
- Include real VLC verification when process or HTTP integration changes.

## Implementation safety

- Preserve user media and existing database contents under every failure mode.
- Never log VLC credentials, raw status payloads, or unrelated media paths.
