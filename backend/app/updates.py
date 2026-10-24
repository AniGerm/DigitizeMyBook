"""Öffentliche GitHub-Releases: .deb prüfen, laden und installieren. Kein Token."""

from __future__ import annotations

import logging
import os
import re
import subprocess
import threading
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

GITHUB_REPO = "Anigerm/DigitizeMyBook"
DEB_NAME_RE = re.compile(r"^DigitizeMyBook-.*\.deb$", re.IGNORECASE)
APPLY_UPDATE = Path("/usr/lib/digitizemybook/apply-update.sh")
LATEST_URL = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"

_lock = threading.Lock()
_state: dict[str, Any] = {
    "phase": "idle",
    "current_version": None,
    "latest_version": None,
    "available": False,
    "deb_name": None,
    "deb_url": None,
    "deb_size": 0,
    "download_path": None,
    "download_percent": 0,
    "error": None,
    "checked_at": None,
    "repo": GITHUB_REPO,
}


@dataclass(frozen=True)
class DebReleaseInfo:
    version: str
    deb_url: str
    deb_name: str
    size: int


def app_version() -> str:
    candidates = [
        Path("/opt/digitizemybook/VERSION"),
        Path(__file__).resolve().parents[2] / "VERSION",
    ]
    for path in candidates:
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "0.1.0"


def is_version_newer(remote: str, local: str) -> bool:
    def parse(value: str) -> list[int]:
        parts = re.sub(r"^[vV]", "", value.strip()).split(".")
        out: list[int] = []
        for part in parts:
            match = re.match(r"^(\d+)", part)
            out.append(int(match.group(1)) if match else 0)
        return out

    left, right = parse(remote), parse(local)
    width = max(len(left), len(right))
    left += [0] * (width - len(left))
    right += [0] * (width - len(right))
    return left > right


def release_to_deb_info(release: dict[str, Any]) -> DebReleaseInfo | None:
    asset = None
    for item in release.get("assets") or []:
        name = str(item.get("name") or "")
        if DEB_NAME_RE.match(name):
            asset = item
            break
    if not asset:
        return None
    url = str(asset.get("browser_download_url") or "")
    if not url:
        return None
    tag = str(release.get("tag_name") or "").lstrip("vV")
    return DebReleaseInfo(
        version=tag,
        deb_url=url,
        deb_name=str(asset.get("name") or ""),
        size=int(asset.get("size") or 0),
    )


def fetch_latest_deb_release() -> DebReleaseInfo | None:
    request = urllib.request.Request(
        LATEST_URL,
        headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "digitizeMyBook",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        import json

        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return None
    return release_to_deb_info(payload)


def _set_state(**kwargs: Any) -> dict[str, Any]:
    with _lock:
        _state.update(kwargs)
        _state["current_version"] = app_version()
        _state["repo"] = GITHUB_REPO
        return dict(_state)


def get_status() -> dict[str, Any]:
    with _lock:
        snap = dict(_state)
    snap["current_version"] = app_version()
    snap["repo"] = GITHUB_REPO
    return snap


def check_for_update() -> dict[str, Any]:
    _set_state(phase="checking", error=None, download_percent=0)
    try:
        latest = fetch_latest_deb_release()
        current = app_version()
        if not latest:
            return _set_state(
                phase="error",
                error="Kein DigitizeMyBook-*.deb im neuesten Release",
                available=False,
                latest_version=None,
                checked_at=time.time(),
            )
        newer = is_version_newer(latest.version, current)
        return _set_state(
            phase="update-available" if newer else "up-to-date",
            available=newer,
            latest_version=latest.version,
            current_version=current,
            deb_name=latest.deb_name if newer else None,
            deb_url=latest.deb_url if newer else None,
            deb_size=latest.size if newer else 0,
            download_path=None,
            download_percent=0,
            error=None,
            checked_at=time.time(),
        )
    except Exception as exc:
        logger.warning("Update-Check fehlgeschlagen: %s", exc)
        return _set_state(
            phase="error",
            error=str(exc),
            available=False,
            checked_at=time.time(),
        )


def _cache_dir() -> Path:
    path = Path.home() / ".cache" / "digitizemybook" / "updates"
    path.mkdir(parents=True, exist_ok=True)
    return path


def download_update() -> dict[str, Any]:
    status = get_status()
    if not status.get("available") or not status.get("deb_url") or not status.get("deb_name"):
        return _set_state(phase="error", error="Kein Update zum Herunterladen")
    deb_name = str(status["deb_name"])
    deb_url = str(status["deb_url"])
    dest = _cache_dir() / deb_name
    _set_state(phase="downloading", download_percent=0, error=None, download_path=None)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        request = urllib.request.Request(deb_url, headers={"User-Agent": "digitizeMyBook"})
        with urllib.request.urlopen(request, timeout=120) as response:
            total = int(response.headers.get("Content-Length") or status.get("deb_size") or 0)
            received = 0
            with tmp.open("wb") as handle:
                while True:
                    chunk = response.read(65536)
                    if not chunk:
                        break
                    handle.write(chunk)
                    received += len(chunk)
                    percent = int(received * 100 / total) if total else 0
                    _set_state(download_percent=min(100, percent))
        tmp.replace(dest)
        return _set_state(phase="downloaded", download_path=str(dest), download_percent=100, error=None)
    except Exception as exc:
        logger.warning("Update-Download fehlgeschlagen: %s", exc)
        tmp.unlink(missing_ok=True)
        return _set_state(phase="error", error=str(exc), download_path=None)


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def install_update() -> dict[str, Any]:
    status = get_status()
    path = status.get("download_path")
    if not path or not Path(str(path)).is_file():
        return _set_state(phase="error", error="Update noch nicht heruntergeladen")
    deb_path = str(path)
    if not DEB_NAME_RE.match(Path(deb_path).name):
        return _set_state(phase="error", error="Ungültiger Paketname")
    _set_state(phase="installing", error=None)
    log_path = _cache_dir() / "install-update.log"
    helper = APPLY_UPDATE if APPLY_UPDATE.is_file() else None
    if helper:
        script = f"""#!/bin/bash
trap '' HUP
exec >>{_shell_quote(str(log_path))} 2>&1
echo "==== $(date -Is) digitizemybook update ===="
pkexec {_shell_quote(str(helper))} {_shell_quote(deb_path)}
"""
    else:
        script = f"""#!/bin/bash
trap '' HUP
exec >>{_shell_quote(str(log_path))} 2>&1
echo "==== $(date -Is) digitizemybook update (fallback) ===="
export DEBIAN_FRONTEND=noninteractive
pkexec apt-get install -y --reinstall {_shell_quote(deb_path)} \\
  || pkexec dpkg -i {_shell_quote(deb_path)}
systemctl restart digitizemybook 2>/dev/null || true
"""
    script_path = _cache_dir() / "run-install.sh"
    script_path.write_text(script, encoding="utf-8")
    script_path.chmod(0o755)
    try:
        subprocess.Popen(
            ["setsid", "bash", str(script_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
    except FileNotFoundError:
        subprocess.Popen(
            ["bash", str(script_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            env=os.environ.copy(),
        )
    return _set_state(phase="installing", error=None)
