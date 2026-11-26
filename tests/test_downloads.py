from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

from vlcq.database import Database
from vlcq.downloads import (
    DownloadSnapshot,
    DownloadStatus,
    QBittorrentDownloads,
    aggregate,
    format_eta,
)
from vlcq.tui import BrowserListItem, DownloadBadge, QueueListItem, VLCQApp


def _torq_config(tmp_path: Path, port: int = 8080) -> Path:
    path = tmp_path / "torq" / "config.toml"
    path.parent.mkdir()
    path.write_text(f'[qbittorrent]\nhost = "127.0.0.1"\nport = {port}\nusername = "torq"\n')
    return path


def _qbittorrent(torrents: list[dict[str, object]], files: dict[str, list[dict[str, object]]],
                 *, login_status: int = 204, login_body: str = "") -> tuple[httpx.MockTransport, list[str]]:
    calls: list[str] = []
    logged_in = False

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal logged_in
        calls.append(request.url.path)
        assert request.method in {"GET", "POST"}
        assert request.headers["referer"] == "http://127.0.0.1:8080"
        if request.url.path == "/api/v2/auth/login":
            logged_in = login_status < 300 and login_body != "Fails."
            return httpx.Response(login_status, text=login_body)
        if not logged_in:
            return httpx.Response(403)
        if request.url.path == "/api/v2/torrents/info":
            return httpx.Response(200, text=json.dumps(torrents))
        if request.url.path == "/api/v2/torrents/files":
            return httpx.Response(200, text=json.dumps(files[request.url.params["hash"]]))
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


def test_poll_reports_incomplete_wanted_files_only(tmp_path: Path) -> None:
    save = tmp_path / "tv"
    transport, calls = _qbittorrent(
        [
            {"hash": "a", "progress": 0.5, "state": "downloading", "save_path": str(save),
             "download_path": "", "eta": 125},
            {"hash": "done", "progress": 1, "state": "stalledUP", "save_path": str(save)},
            {"hash": "gone", "progress": 0, "state": "missingFiles", "save_path": str(save)},
            {"hash": "p", "progress": 0.2, "state": "stoppedDL", "save_path": str(save),
             "download_path": str(tmp_path / "incoming"), "eta": 8640000},
        ],
        {
            "a": [
                {"name": "Show/e01.mkv", "size": 100, "progress": 1, "priority": 1},
                {"name": "Show/e02.mkv", "size": 100, "progress": 0.25, "priority": 1},
                {"name": "Show/e03.mkv", "size": 100, "progress": 0.0, "priority": 0},
            ],
            "p": [{"name": "movie.mkv", "size": 50, "progress": 0.2, "priority": 1}],
        },
    )
    source = QBittorrentDownloads(
        config_path=_torq_config(tmp_path), password_lookup=lambda user: "secret",
        transport=transport,
    )
    snapshot = source.poll()
    assert snapshot == {
        (save / "Show" / "e02.mkv").resolve(): DownloadStatus(0.25, 100, "downloading", 125),
        (tmp_path / "incoming" / "movie.mkv").resolve(): DownloadStatus(0.2, 50, "stopped", None),
    }
    assert "gone" not in str(calls) and source.last_error is None


def test_poll_accepts_legacy_ok_login_and_rejects_failures(tmp_path: Path) -> None:
    config = _torq_config(tmp_path)
    ok, _ = _qbittorrent([], {}, login_status=200, login_body="Ok.")
    assert QBittorrentDownloads(config_path=config, password_lookup=lambda u: "x", transport=ok).poll() == {}

    bad, _ = _qbittorrent([], {}, login_status=200, login_body="Fails.")
    source = QBittorrentDownloads(config_path=config, password_lookup=lambda u: "x", transport=bad)
    assert source.poll() == {}
    assert source.last_error is not None and "rejected" in source.last_error


def test_poll_is_quiet_without_torq(tmp_path: Path) -> None:
    source = QBittorrentDownloads(config_path=tmp_path / "missing.toml")
    assert source.poll() == {}
    assert source.describe()[0] is False

    no_password = QBittorrentDownloads(config_path=_torq_config(tmp_path), password_lookup=lambda u: None)
    assert no_password.poll() == {}
    assert "Keychain" in (no_password.last_error or "")


def test_aggregate_and_formatting() -> None:
    combined = aggregate([
        DownloadStatus(1.0, 300, "stopped"),
        DownloadStatus(0.0, 100, "downloading", 90),
    ])
    assert combined == DownloadStatus(0.75, 400, "downloading", 90)
    assert aggregate([]) is None
    assert DownloadStatus(0.999, 1, "downloading").percent == 99
    assert [format_eta(value) for value in (None, 5, 125, 3_900, 200_000)] == ["", "5s", "2m", "1h05", "2d"]


def test_badge_hidden_until_downloading() -> None:
    badge = DownloadBadge()
    assert badge.display is False
    badge.set_status(DownloadStatus(0.42, 10, "downloading", 600))
    assert badge.display is True
    assert str(badge.renderable) == "↓42% 10m"
    badge.set_status(DownloadStatus(0.42, 10, "stalled"))
    assert str(badge.renderable) == "↓42% stalled"
    badge.set_status(None)
    assert badge.display is False


class _FakeSource:
    def __init__(self, snapshot: DownloadSnapshot) -> None:
        self.snapshot = snapshot

    def poll(self) -> DownloadSnapshot:
        return dict(self.snapshot)

    def close(self) -> None:
        pass


@pytest.mark.asyncio
async def test_rows_show_live_download_progress(tmp_path: Path) -> None:
    root = (tmp_path / "library").resolve()
    show = root / "Show"
    show.mkdir(parents=True)
    episode = show / "e01.mkv"
    other = show / "e02.mkv"
    for path in (episode, other):
        path.write_bytes(b"partial")
    source = _FakeSource({episode: DownloadStatus(0.3, 100, "downloading", 60),
                          other: DownloadStatus(0.9, 100, "downloading", 30)})
    app = VLCQApp(root=root, database=Database(tmp_path / "db.sqlite3"), no_vlc=True,
                  downloads=source)  # type: ignore[arg-type]

    async with app.run_test(size=(120, 30)) as pilot:
        await pilot.pause(0.2)
        app.expanded_paths.add(show)
        await app._load_tree_branch(show, app._tree_generation)
        app.queue.add([episode])
        app.refresh_queue()
        await pilot.pause(0.2)

        rows = {row.path: row for row in app.query(BrowserListItem)}
        assert str(rows[show].query_one(DownloadBadge).renderable).startswith("↓60%")
        assert str(rows[episode].query_one(DownloadBadge).renderable) == "↓30% 1m"
        queue_badge = app.query_one(QueueListItem).query_one(DownloadBadge)
        assert queue_badge.display and str(queue_badge.renderable) == "↓30% 1m"

        # The file finishes: every badge disappears on the next poll.
        source.snapshot = {}
        await app._poll_downloads()
        await pilot.pause()
        assert not any(badge.display for badge in app.query(DownloadBadge))
