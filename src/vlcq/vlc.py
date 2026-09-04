from __future__ import annotations

import asyncio
import json
import secrets
import shutil
import socket
from pathlib import Path

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
                uri = meta.get("url") or meta.get("uri")
    path = None
    if uri:
        try:
            path = file_uri_to_path(str(uri))
        except PathError as exc:
            raise VLCError("VLC reported unsafe media") from exc
    return VLCStatus(
        state, _number(payload.get("time")) * 1000, _number(payload.get("length")) * 1000, path
    )


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

    async def status(self) -> VLCStatus:
        try:
            response = await self._client.get("/requests/status.json")
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type not in {"application/json", "text/json", "text/plain"}:
                raise VLCError("VLC returned unexpected content")
            return parse_status(response.json())
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise VLCError("VLC is unavailable") from exc

    async def command(self, command: str, **parameters: str | int) -> VLCStatus:
        allowed = {"in_play", "pl_pause", "pl_stop", "seek"}
        if command not in allowed:
            raise VLCError("unsupported VLC command")
        params: dict[str, str | int] = {"command": command, **parameters}
        try:
            response = await self._client.get("/requests/status.json", params=params)
            response.raise_for_status()
            content_type = response.headers.get("content-type", "").split(";", 1)[0].strip()
            if content_type not in {"application/json", "text/json", "text/plain"}:
                raise VLCError("VLC returned unexpected content")
            return parse_status(response.json())
        except httpx.HTTPError as exc:
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
        output, _ = await asyncio.wait_for(probe.communicate(), 5)
        first_line = output.decode("utf-8", "replace").splitlines()[:1]
        if probe.returncode != 0 or not first_line or "VLC version 3." not in first_line[0]:
            raise VLCError("vlcq requires a compatible VLC 3 executable")

    async def start(self, timeout: float = 10) -> VLCClient:
        if not Path(self.executable).is_file():
            raise VLCError("VLC executable not found; install VLC 3 or configure its path")
        await self._validate_version()
        args = [
            self.executable,
            "--intf=dummy",
            "--no-media-library",
            "--extraintf=http",
            "--http-host=127.0.0.1",
            f"--http-port={self.port}",
            f"--http-password={self.password}",
            "--no-video-title-show",
        ]
        self.process = await asyncio.create_subprocess_exec(
            *args, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL
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
            except VLCError:
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
