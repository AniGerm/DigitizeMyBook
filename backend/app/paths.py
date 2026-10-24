from __future__ import annotations

import os
from pathlib import Path


APP_NAME = "digitizeMyBook"


def data_root() -> Path:
    xdg = os.environ.get("XDG_DATA_HOME")
    if xdg:
        root = Path(xdg) / APP_NAME
    else:
        root = Path.home() / ".local" / "share" / APP_NAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def jobs_root() -> Path:
    path = data_root() / "jobs"
    path.mkdir(parents=True, exist_ok=True)
    return path


def job_dir(job_id: str) -> Path:
    path = jobs_root() / job_id
    path.mkdir(parents=True, exist_ok=True)
    return path


def uploads_root() -> Path:
    path = data_root() / "uploads"
    path.mkdir(parents=True, exist_ok=True)
    return path
