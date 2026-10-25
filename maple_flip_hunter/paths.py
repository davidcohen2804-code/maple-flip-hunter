"""Filesystem locations (per-user, no admin rights needed)."""
from __future__ import annotations

import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
    else:
        base = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    p = base / "MapleFlipHunter"
    p.mkdir(parents=True, exist_ok=True)
    return p


def db_path() -> Path:
    return app_data_dir() / "market.sqlite3"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def log_dir() -> Path:
    p = app_data_dir() / "logs"
    p.mkdir(parents=True, exist_ok=True)
    return p


def sounds_dir() -> Path:
    p = app_data_dir() / "sounds"
    p.mkdir(parents=True, exist_ok=True)
    return p
