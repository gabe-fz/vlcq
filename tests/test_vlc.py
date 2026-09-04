from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from vlcq.vlc import VLCClient, VLCError, VLCProcess, parse_status


def test_process_uses_visible_macos_interface() -> None:
    process = VLCProcess("/Applications/VLC.app/Contents/MacOS/VLC")
    arguments = process.launch_arguments()
    assert "--intf=macosx" in arguments
    assert "--intf=dummy" not in arguments
    assert "--extraintf=http" in arguments


def test_parse_status_is_tolerant_and_rejects_remote_media(tmp_path: Path) -> None:
    video = tmp_path / "a.mkv"
    video.write_bytes(b"x")
    status = parse_status(
        {
            "state": "playing",
            "time": 12,
            "length": 30,
            "information": {"category": {"meta": {"url": video.as_uri()}}},
        }
    )
    assert (
        status.state == "playing"
        and status.position_ms == 12_000
        and status.path == video.resolve()
    )
    with pytest.raises(VLCError):
        parse_status(
            {"state": "playing", "information": {"category": {"meta": {"url": "https://x/a"}}}}
        )


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
