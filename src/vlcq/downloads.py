"""Read-only download progress from torq's qBittorrent engine.

torq keeps qBittorrent running headless with its
Web API on localhost and the password in the login Keychain.  vlcq only ever
reads ``torrents/info`` and ``torrents/files``; it never adds, starts, stops
or removes anything.

Everything here is synchronous and meant to run in a worker thread.  Any
failure (no torq config, no password, qBittorrent not running) yields an empty
snapshot rather than an exception, so the TUI simply shows no download badges.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

KEYCHAIN_SERVICE = "torq-qbittorrent"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8080
DEFAULT_USERNAME = "torq"

# States in which a torrent cannot make progress at all; their files may not
# even exist, so a "0%" badge would only be noise.
_INERT_STATES = frozenset({"missingFiles", "error", "unknown"})
_STOPPED_STATES = frozenset({"pausedDL", "stoppedDL"})
_QUEUED_STATES = frozenset({"queuedDL", "checkingDL", "checkingResumeData", "allocating", "moving"})
_STALLED_STATES = frozenset({"stalledDL", "metaDL", "forcedMetaDL"})


@dataclass(frozen=True)
class DownloadStatus:
    """One incomplete, wanted file inside a torrent qBittorrent is handling."""

    progress: float  # 0.0 – 1.0
    size: int
    state: str  # "downloading" | "stalled" | "queued" | "stopped"
    eta_seconds: int | None = None

    @property
    def percent(self) -> int:
        # Never claim 100% for a file that qBittorrent still reports as partial.
        return min(99, max(0, int(self.progress * 100)))


type DownloadSnapshot = dict[Path, DownloadStatus]


def classify_state(state: str) -> str | None:
    if state in _INERT_STATES:
        return None
    if state in _STOPPED_STATES:
        return "stopped"
    if state in _QUEUED_STATES:
        return "queued"
    if state in _STALLED_STATES:
        return "stalled"
    return "downloading"


def aggregate(statuses: list[DownloadStatus]) -> DownloadStatus | None:
    """Combine several files (a folder's contents) into one size-weighted status."""
    if not statuses:
        return None
    total = sum(status.size for status in statuses)
    if total > 0:
        progress = sum(status.progress * status.size for status in statuses) / total
    else:
        progress = sum(status.progress for status in statuses) / len(statuses)
    for state in ("downloading", "stalled", "queued", "stopped"):
        if any(status.state == state for status in statuses):
            break
    etas = [status.eta_seconds for status in statuses if status.eta_seconds is not None]
    return DownloadStatus(progress, total, state, max(etas) if etas else None)


def _torq_config_path() -> Path:
    base = os.environ.get("TORQ_CONFIG_DIR")
    return (Path(base).expanduser() if base else Path.home() / ".config" / "torq") / "config.toml"


def _keychain_password(account: str) -> str | None:
    if env := os.environ.get("TORQ_PASSWORD"):
        return env
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-a", account, "-s", KEYCHAIN_SERVICE, "-w"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    password = result.stdout.strip()
    return password if result.returncode == 0 and password else None


def downloads_enabled() -> bool:
    return os.environ.get("VLCQ_DOWNLOADS", "on").strip().lower() not in {"0", "off", "no", "false"}


class QBittorrentDownloads:
    """Poll qBittorrent's Web API using torq's connection settings."""

    def __init__(
        self,
        *,
        config_path: Path | None = None,
        password_lookup: Callable[[str], str | None] = _keychain_password,
        transport: httpx.BaseTransport | None = None,
        resolve: Callable[[Path], Path] | None = None,
    ) -> None:
        self._config_path = config_path or _torq_config_path()
        self._password_lookup = password_lookup
        self._transport = transport
        self._resolve = resolve or (lambda path: path.resolve(strict=False))
        self._client: httpx.Client | None = None
        self._username = DEFAULT_USERNAME
        self._password: str | None = None
        self.last_error: str | None = None

    # -- connection --------------------------------------------------------

    def _settings(self) -> tuple[str, int, str]:
        section: Mapping[str, Any] = {}
        try:
            section = tomllib.loads(self._config_path.read_text()).get("qbittorrent", {})
        except FileNotFoundError:
            pass
        host = str(section.get("host", DEFAULT_HOST))
        port = int(section.get("port", DEFAULT_PORT))
        username = str(section.get("username", DEFAULT_USERNAME))
        return host, port, username

    def configured(self) -> bool:
        return self._config_path.is_file()

    def _connect(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        if not self.configured():
            raise RuntimeError("torq is not set up (no ~/.config/torq/config.toml)")
        host, port, username = self._settings()
        password = self._password_lookup(username)
        if password is None:
            raise RuntimeError("no torq Web API password in Keychain")
        base = f"http://{host}:{port}"
        self._username, self._password = username, password
        self._client = httpx.Client(
            base_url=base,
            # qBittorrent's CSRF check compares Referer/Origin with its host.
            headers={"Referer": base},
            timeout=httpx.Timeout(5.0, connect=2.0),
            transport=self._transport,
        )
        self._login()
        return self._client

    def _login(self) -> None:
        assert self._client is not None
        response = self._client.post(
            "/api/v2/auth/login",
            data={"username": self._username, "password": self._password or ""},
        )
        # qBittorrent < 5.1 answers 200 "Ok."/"Fails."; newer builds answer
        # 204 with an empty body on success.
        if not response.is_success or response.text.strip() == "Fails.":
            raise RuntimeError("qBittorrent rejected torq's Web API login")

    def _get(self, path: str, **params: str) -> Any:
        client = self._connect()
        response = client.get(path, params=params)
        if response.status_code == 403:  # session expired / qBittorrent restarted
            self._login()
            response = client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # -- snapshot ----------------------------------------------------------

    def poll(self) -> DownloadSnapshot:
        """Return incomplete wanted files keyed by canonical path; {} on failure."""
        try:
            snapshot = self._poll()
        except (httpx.HTTPError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
            self.last_error = str(exc) or type(exc).__name__
            self.close()
            return {}
        self.last_error = None
        return snapshot

    def _poll(self) -> DownloadSnapshot:
        snapshot: DownloadSnapshot = {}
        torrents = self._get("/api/v2/torrents/info")
        for torrent in torrents:
            if float(torrent.get("progress", 1)) >= 1:
                continue
            state = classify_state(str(torrent.get("state", "")))
            if state is None:
                continue
            # While a torrent is incomplete qBittorrent keeps it in its
            # temporary download_path when one is configured.
            base = torrent.get("download_path") or torrent.get("save_path")
            if not base:
                continue
            raw_eta = int(torrent.get("eta", 0) or 0)
            eta = raw_eta if 0 < raw_eta < 8_640_000 else None  # 8640000 == "infinity"
            files = self._get("/api/v2/torrents/files", hash=str(torrent["hash"]))
            for item in files:
                progress = float(item.get("progress", 0))
                if progress >= 1 or int(item.get("priority", 1)) == 0:
                    continue
                path = self._resolve(Path(base) / str(item["name"]))
                snapshot[path] = DownloadStatus(progress, int(item.get("size", 0)), state, eta)
        return snapshot

    def describe(self) -> tuple[bool, str]:
        """One-line diagnostic for ``vlcq doctor``."""
        if not downloads_enabled():
            return False, "download progress disabled by VLCQ_DOWNLOADS"
        if not self.configured():
            return False, "download progress unavailable: torq is not set up"
        try:
            snapshot = self._poll()
        except (httpx.HTTPError, RuntimeError, OSError, ValueError, KeyError, TypeError) as exc:
            return False, f"download progress unavailable: {exc or type(exc).__name__}"
        finally:
            self.close()
        return True, f"download progress ready: {len(snapshot)} file(s) downloading in qBittorrent"


def format_eta(seconds: int | None) -> str:
    if seconds is None:
        return ""
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        return f"{seconds // 60}m"
    if seconds < 86_400:
        return f"{seconds // 3600}h{seconds % 3600 // 60:02d}"
    return f"{seconds // 86_400}d"
