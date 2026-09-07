from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest

from vlcq.vlc import VLCClient, VLCError, VLCProcess, parse_status


def test_process_uses_visible_macos_interface() -> None:
    process = VLCProcess("/Applications/VLC.app/Contents/MacOS/VLC")
    arguments = process.launch_arguments()
    assert "--intf=macosx" in arguments
    assert "--intf=dummy" not in arguments
    assert "--extraintf=http" in arguments


@pytest.mark.parametrize(
    "banner",
    [b"VLC media player 3.0.21 Vetinari\n", b"VLC version 3.0.17.3 Vetinari\n"],
)
@pytest.mark.asyncio
async def test_validate_version_accepts_standard_vlc_media_player_banner(
    monkeypatch: pytest.MonkeyPatch, banner: bytes
) -> None:
    class FakeStdout:
        async def readline(self) -> bytes:
            return banner

    class FakeProbe:
        stdout = FakeStdout()
        returncode: int | None = None

        def terminate(self) -> None:
            self.returncode = 0

        async def wait(self) -> int:
            return 0

    async def create_probe(*_args: object, **_kwargs: object) -> Any:
        return FakeProbe()

    monkeypatch.setattr("vlcq.vlc.asyncio.create_subprocess_exec", create_probe)
    await VLCProcess("/Applications/VLC.app/Contents/MacOS/VLC")._validate_version()


@pytest.mark.asyncio
async def test_validate_version_rejects_unsupported_major(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeStdout:
        async def readline(self) -> bytes:
            return b"VLC media player 4.0.0\n"

    class FakeProbe:
        stdout = FakeStdout()
        returncode: int | None = None

        def terminate(self) -> None:
            self.returncode = 0

        async def wait(self) -> int:
            return 0

    async def create_probe(*_args: object, **_kwargs: object) -> Any:
        return FakeProbe()

    monkeypatch.setattr("vlcq.vlc.asyncio.create_subprocess_exec", create_probe)
    with pytest.raises(VLCError, match="requires a compatible VLC 3"):
        await VLCProcess("/Applications/VLC.app/Contents/MacOS/VLC")._validate_version()


def test_parse_status_is_tolerant_and_rejects_remote_media(tmp_path: Path) -> None:
    video = tmp_path / "a.mkv"
    video.write_bytes(b"x")
    status = parse_status(
        {
            "state": "playing",
            "time": 12,
            "length": 30,
            "information": {"category": {"meta": {"uri": video.as_uri()}}},
        }
    )
    assert (
        status.state == "playing"
        and status.position_ms == 12_000
        and status.path == video.resolve()
    )
    with pytest.raises(VLCError):
        parse_status(
            {"state": "playing", "information": {"category": {"meta": {"uri": "https://x/a"}}}}
        )


@pytest.mark.asyncio
async def test_client_resolves_current_media_from_playlist(tmp_path: Path) -> None:
    video = tmp_path / "episode [01].mkv"
    video.write_bytes(b"x")
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("status.json"):
            return httpx.Response(
                200,
                json={"state": "playing", "time": 2, "length": 10, "currentplid": 42},
                headers={"content-type": "application/json"},
            )
        if request.url.path.endswith("playlist.json"):
            return httpx.Response(
                200,
                json={
                    "children": [
                        {
                            "name": "Playlist",
                            "children": [
                                {"id": "42", "type": "leaf", "uri": video.as_uri()}
                            ],
                        }
                    ]
                },
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    client = VLCClient(9999, "secret", transport=httpx.MockTransport(handler))
    status = await client.play(video)
    assert status.path == video.resolve()
    assert requests[0].url.params["command"] == "in_play"
    assert requests[0].url.params["input"] == video.as_uri()
    assert [request.url.path for request in requests] == [
        "/requests/status.json",
        "/requests/playlist.json",
    ]

    # The stable playlist id is cached, so normal polling does not double the
    # number of HTTP requests once the media identity has been established.
    assert (await client.status()).path == video.resolve()
    assert [request.url.path for request in requests].count("/requests/playlist.json") == 1
    await client.close()


@pytest.mark.asyncio
async def test_client_auth_commands_and_no_redirects(tmp_path: Path) -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("status.json"):
            return httpx.Response(
                200,
                json={"state": "paused", "time": 2, "length": 10},
                headers={"content-type": "application/json"},
            )
        return httpx.Response(404)

    client = VLCClient(9999, "secret", transport=httpx.MockTransport(handler))
    status = await client.status()
    assert status.state == "paused"
    await client.command("pl_pause")
    assert requests[-1].url.params["command"] == "pl_pause"
    assert requests[-1].headers["authorization"].startswith("Basic ")
    await client.close()


@pytest.mark.asyncio
async def test_client_rejects_unexpected_response_content() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200, text="<html>not status</html>", headers={"content-type": "text/html"}
        )

    client = VLCClient(9999, "secret", transport=httpx.MockTransport(handler))
    with pytest.raises(VLCError, match="unexpected content"):
        await client.status()
    await client.close()
