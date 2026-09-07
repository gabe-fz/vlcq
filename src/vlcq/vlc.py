from __future__ import annotations

import asyncio
import json
import re
import secrets
import shutil
import socket
from pathlib import Path
from typing import cast

import httpx

from .models import VLCStatus
from .paths import PathError, file_uri_to_path


class VLCError(RuntimeError):
    pass


def _number(value: object) -> int:
    try:
        if not isinstance(value, (str, int, float)):
            return 0
        return max(0, int(float(value)))
    except (TypeError, ValueError, OverflowError):
        return 0


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
    if uri:
        try:
            path = file_uri_to_path(str(uri))
        except PathError as exc:
            raise VLCError("VLC reported unsafe media") from exc
    return VLCStatus(
        state, _number(payload.get("time")) * 1000, _number(payload.get("length")) * 1000, path
    )


def _current_playlist_id(payload: object) -> str | None:
    if not isinstance(payload, dict):
        return None
    value = payload.get("currentplid")
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return None
    playlist_id = str(value)
    return None if playlist_id == "-1" else playlist_id


def _playlist_item_path(payload: object, playlist_id: str) -> Path | None:
    """Find a current item's MRL in VLC's nested playlist response."""
    pending = [payload]
    while pending:
        item = pending.pop()
        if isinstance(item, list):
            pending.extend(item)
            continue
        if not isinstance(item, dict):
            continue
        if str(item.get("id")) == playlist_id:
            uri = item.get("uri")
            if not isinstance(uri, str) or not uri:
                return None
            try:
                return file_uri_to_path(uri)
            except PathError as exc:
                raise VLCError("VLC reported unsafe media") from exc
        pending.extend(cast(object, value) for value in item.values())
    return None


class VLCClient:
    def __init__(
        self, port: int, password: str, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.port = port
        self._client = httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{port}",
            auth=("", password),
            follow_redirects=False,
            timeout=2,
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

    async def _status_from_payload(self, payload: object) -> VLCStatus:
        status = parse_status(payload)
        playlist_id = _current_playlist_id(payload)
        if status.path is not None or playlist_id is None:
            return status
        path = self._playlist_paths.get(playlist_id)
        if path is None:
            response = await self._client.get("/requests/playlist.json")
            playlist = self._response_payload(response)
            path = _playlist_item_path(playlist, playlist_id)
            if path is not None:
                self._playlist_paths[playlist_id] = path
        if path is None:
            return status
        return VLCStatus(status.state, status.position_ms, status.duration_ms, path)

    async def status(self) -> VLCStatus:
        try:
            response = await self._client.get("/requests/status.json")
            return await self._status_from_payload(self._response_payload(response))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise VLCError("VLC is unavailable") from exc

    async def command(self, command: str, **parameters: str | int) -> VLCStatus:
        allowed = {"in_play", "pl_pause", "pl_stop", "seek"}
        if command not in allowed:
            raise VLCError("unsupported VLC command")
        params: dict[str, str | int] = {"command": command, **parameters}
        try:
            response = await self._client.get("/requests/status.json", params=params)
            return await self._status_from_payload(self._response_payload(response))
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise VLCError("VLC command failed") from exc

    async def play(self, path: Path) -> VLCStatus:
        return await self.command("in_play", input=path.as_uri())

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
