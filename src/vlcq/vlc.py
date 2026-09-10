from __future__ import annotations

import asyncio
import json
import math
import re
import secrets
import shutil
import socket
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import cast

import httpx

from .models import VLCStatus
from .paths import PathError, file_uri_to_path


class VLCError(RuntimeError):
    pass


def _rate(value: object) -> float | None:
    try:
        if not isinstance(value, (str, int, float)) or isinstance(value, bool):
            return None
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) and result > 0 else None


def _number(value: object) -> int:
    try:
        if not isinstance(value, (str, int, float)):
            return 0
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


@dataclass(frozen=True)
class VLCPlaylistItem:
    """A validated leaf in VLC's ephemeral playlist."""

    playlist_id: str
    path: Path

    @property
    def id(self) -> str:
        return self.playlist_id

    @property
    def vlc_id(self) -> str:
        return self.playlist_id


# Descriptive aliases keep the playlist identity type discoverable to callers
# that use either VLC's terminology or the controller's terminology.
PlaylistItem = VLCPlaylistItem
VLCPlaylistEntry = VLCPlaylistItem


def _playlist_id(value: object) -> str:
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        raise VLCError("VLC returned malformed playlist identity")
    result = str(value)
    if not result or result == "-1":
        raise VLCError("VLC returned malformed playlist identity")
    return result


def _playlist_nodes(payload: object) -> Iterator[dict[str, object]]:
    """Yield nested playlist nodes while ignoring unrelated JSON metadata."""
    if isinstance(payload, dict):
        yield cast(dict[str, object], payload)
        for key in ("children", "playlist", "items"):
            children = payload.get(key)
            if isinstance(children, list):
                for child in children:
                    yield from _playlist_nodes(child)
            elif children is not None:
                raise VLCError("VLC returned malformed playlist structure")
    elif isinstance(payload, list):
        for child in payload:
            yield from _playlist_nodes(child)
    else:
        raise VLCError("VLC returned malformed playlist structure")


def parse_playlist(payload: object) -> list[VLCPlaylistItem]:
    """Parse VLC's nested playlist response into safe, stable leaf identities."""
    if not isinstance(payload, (dict, list)):
        raise VLCError("unsupported VLC playlist response")
    result: list[VLCPlaylistItem] = []
    by_id: dict[str, Path] = {}
    by_path: dict[Path, str] = {}
    for node in _playlist_nodes(payload):
        has_uri = "uri" in node
        node_type = node.get("type")
        if not has_uri:
            if node_type == "leaf":
                raise VLCError("VLC returned malformed playlist entry")
            continue
        playlist_id = _playlist_id(node.get("id"))
        uri = node.get("uri")
        if not isinstance(uri, str) or not uri:
            raise VLCError("VLC returned malformed playlist entry")
        try:
            path = file_uri_to_path(uri)
        except PathError as exc:
            raise VLCError("VLC playlist contains unsafe media") from exc
        previous_path = by_id.get(playlist_id)
        previous_id = by_path.get(path)
        if previous_path is not None and previous_path != path:
            raise VLCError("VLC playlist contains ambiguous identities")
        if previous_id is not None and previous_id != playlist_id:
            raise VLCError("VLC playlist contains ambiguous identities")
        if previous_path is None and previous_id is None:
            by_id[playlist_id] = path
            by_path[path] = playlist_id
            result.append(VLCPlaylistItem(playlist_id, path))
    return result


def parse_status(payload: object) -> VLCStatus:
    if not isinstance(payload, dict):
        raise VLCError("unsupported VLC status response")
    state = str(payload.get("state", "unavailable"))
    if state not in {"playing", "paused", "stopped"}:
        state = "unavailable"
    uri: object | None = None
    information = payload.get("information")
    if isinstance(information, dict):
        category = information.get("category")
        if isinstance(category, dict):
            meta = category.get("meta")
            if isinstance(meta, dict):
                # ``url`` is ordinary media metadata (for example, a web
                # page embedded in a file), not the input's MRL.  Some VLC
                # builds expose an actual URI here, but current media identity
                # normally comes from currentplid + playlist.json.
                uri = meta.get("uri")
    path = None
    if uri is not None:
        if not isinstance(uri, str) or not uri:
            raise VLCError("VLC reported malformed media identity")
        try:
            path = file_uri_to_path(uri)
        except PathError as exc:
            raise VLCError("VLC reported unsafe media") from exc
    return VLCStatus(
        state,
        _number(payload.get("time")) * 1000,
        _number(payload.get("length")) * 1000,
        path,
        _current_playlist_id(payload),
        _rate(payload.get("rate")),
    )


def _current_playlist_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("currentplid")
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    playlist_id = str(value)
    return None if playlist_id == "-1" else playlist_id


class VLCClient:
    def __init__(
        self, port: int, password: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.port = port
        self._client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            auth=("", password),
            follow_redirects=False,
            timeout=5,
            transport=transport,
        )
        self._playlist_paths: dict[str, Path] = {}

    @staticmethod
    def _response_payload(response: httpx.Response) -> object:
        response.raise_for_status()
        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
        if content_type not in {"application/json", "text/json", "text/plain"}:
            raise VLCError("VLC returned unexpected content")
        return response.json()

    async def _status_from_payload(
        self,
        payload: object,
        request_started: float | None = None,
        response_received: float | None = None,
    ) -> VLCStatus:
        parsed = parse_status(payload)
        status = VLCStatus(
            parsed.state,
            parsed.position_ms,
            parsed.duration_ms,
            parsed.path,
            parsed.playlist_id,
            parsed.rate,
            request_started,
            response_received,
        )
        playlist_id = status.playlist_id
        if status.path is not None or playlist_id is None:
            return status
        path = self._playlist_paths.get(playlist_id)
        if path is None:
            items = await self.playlist()
            path = next((item.path for item in items if item.playlist_id == playlist_id), None)
        if path is None:
            return status
        return VLCStatus(
            status.state,
            status.position_ms,
            status.duration_ms,
            path,
            playlist_id,
            status.rate,
            status.request_started,
            status.response_received,
        )

    async def playlist(self) -> list[VLCPlaylistItem]:
        """Inspect VLC's nested playlist without exposing its raw response."""
        try:
            response = await self._client.get("/requests/playlist.json")
            items = parse_playlist(self._response_payload(response))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise VLCError("VLC playlist inspection failed") from exc
        self._playlist_paths = {item.playlist_id: item.path for item in items}
        return items

    async def inspect_playlist(self) -> list[VLCPlaylistItem]:
        return await self.playlist()

    async def status(self) -> VLCStatus:
        try:
            clock = asyncio.get_running_loop().time
            started = clock()
            response = await self._client.get("/requests/status.json")
            status = await self._status_from_payload(self._response_payload(response))
            return VLCStatus(
                status.state,
                status.position_ms,
                status.duration_ms,
                status.path,
                status.playlist_id,
                status.rate,
                started,
                clock(),
            )
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise VLCError("VLC is unavailable") from exc

    @staticmethod
    def _validated_media_path(path: Path) -> Path:
        try:
            canonical = path.expanduser().resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise VLCError("VLC media is unavailable") from exc
        if not canonical.is_file():
            raise VLCError("VLC media is unavailable")
        return canonical

    @staticmethod
    def _validated_playlist_id(value: str | int) -> str:
        return _playlist_id(value)

    async def command(self, command: str, **parameters: str | int) -> VLCStatus:
        allowed = {
            "in_play",
            "in_enqueue",
            "pl_pause",
            "pl_stop",
            "pl_next",
            "pl_previous",
            "pl_delete",
            "pl_empty",
            "seek",
            "rate",
        }
        if command not in allowed:
            raise VLCError("unsupported VLC command")
        params: dict[str, str | int] = {"command": command, **parameters}
        try:
            clock = asyncio.get_running_loop().time
            started = clock()
            response = await self._client.get("/requests/status.json", params=params)
            status = await self._status_from_payload(self._response_payload(response))
            status = VLCStatus(
                status.state,
                status.position_ms,
                status.duration_ms,
                status.path,
                status.playlist_id,
                status.rate,
                started,
                clock(),
            )
            if command in {"in_enqueue", "pl_delete", "pl_empty"}:
                self._playlist_paths.clear()
            return status
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise VLCError("VLC command failed") from exc

    async def set_rate(self, rate: float) -> VLCStatus:
        if not math.isfinite(rate) or rate <= 0:
            raise VLCError("playback rate must be finite and positive")
        return await self.command("rate", val=str(rate))

    async def play(self, path: Path) -> VLCStatus:
        canonical = self._validated_media_path(path)
        return await self.command("in_play", input=canonical.as_uri())

    async def enqueue(self, path: Path) -> VLCStatus:
        canonical = self._validated_media_path(path)
        return await self.command("in_enqueue", input=canonical.as_uri())

    async def remove(self, playlist_id: str | int) -> VLCStatus:
        return await self.command("pl_delete", id=self._validated_playlist_id(playlist_id))

    async def replace_playlist(self, path: Path) -> VLCStatus:
        """Clear VLC's ephemeral playlist and start one validated local item."""
        await self.command("pl_empty")
        return await self.play(path)

    # Short aliases make the typed operations convenient for small controller
    # fakes while keeping the public verbs explicit in the implementation.
    replace = replace_playlist
    enqueue_path = enqueue
    remove_playlist_item = remove

    async def close(self) -> None:
        await self._client.aclose()


class VLCProcess:
    def __init__(self, executable: str | Path | None = None) -> None:
        default = "/Applications/VLC.app/Contents/MacOS/VLC"
        self.executable = str(
            executable or (default if Path(default).is_file() else shutil.which("vlc") or default)
        )
        self.password = secrets.token_urlsafe(32)
        self.port = self._free_port()
        self.process: asyncio.subprocess.Process | None = None
        self.client: VLCClient | None = None

    @staticmethod
    def _free_port() -> int:
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            return int(sock.getsockname()[1])

    async def _validate_version(self) -> None:
        probe = await asyncio.create_subprocess_exec(
            self.executable,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        if probe.stdout is None:
            raise VLCError("could not inspect the VLC version")
        try:
            output = await asyncio.wait_for(probe.stdout.readline(), 5)
        finally:
            if probe.returncode is None:
                probe.terminate()
                await probe.wait()
        first_line = output.decode("utf-8", "replace").strip()
        # VLC has used both "VLC media player" and "VLC version" prefixes
        # in its version banner. Match the version number rather than one
        # literal banner so supported VLC 3 builds are accepted while newer
        # major versions are rejected.
        version_match = re.match(r"^VLC (?:media player|version) (\d+)(?:\.|\s|$)", first_line)
        if version_match is None or version_match.group(1) != "3":
            raise VLCError("vlcq requires a compatible VLC 3 executable")

    def launch_arguments(self) -> list[str]:
        return [
            self.executable,
            "--intf=macosx",
            "--no-media-library",
            "--no-repeat",
            "--no-loop",
            "--no-random",
            "--extraintf=http",
            "--http-host=127.0.0.1",
            f"--http-port={self.port}",
            f"--http-password={self.password}",
            "--no-video-title-show",
        ]

    async def start(self, timeout: float = 10) -> VLCClient:
        if self.process is not None or self.client is not None:
            await self.stop()
        if not Path(self.executable).is_file():
            raise VLCError("VLC executable not found; install VLC 3 or configure its path")
        await self._validate_version()
        self.process = await asyncio.create_subprocess_exec(
            *self.launch_arguments(),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        self.client = VLCClient(self.port, self.password)
        deadline = asyncio.get_running_loop().time() + timeout
        while asyncio.get_running_loop().time() < deadline:
            if self.process.returncode is not None:
                break
            try:
                await self.client.status()
                return self.client
            except VLCError:
                await asyncio.sleep(0.1)
        await self.stop()
        raise VLCError("VLC HTTP interface did not become ready")

    async def stop(self) -> None:
        if self.client:
            try:
                await self.client.command("pl_stop")
            except (VLCError, OSError, RuntimeError):
                pass
            await self.client.close()
            self.client = None
        if self.process and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(), 3)
            except TimeoutError:
                if self.process.returncode is None:
                    self.process.kill()
                    await self.process.wait()
        self.process = None
