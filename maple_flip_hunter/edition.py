"""Build edition. The developer build is unrestricted. The Friends Edition is produced by writing "friends" into
`maple_flip_hunter/_edition.txt` before packaging (build/windows/build_friends.bat does it); nothing else differs
in the source tree, and no secret is involved — only the licence server URL and its PUBLIC verification key."""
from __future__ import annotations

import os
import sys
from pathlib import Path

DEVELOPER = "developer"
FRIENDS = "friends"


def _resource(name: str) -> Path:
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    p = base / "maple_flip_hunter" / name
    return p if p.exists() else Path(__file__).resolve().parent / name


def edition() -> str:
    env = os.environ.get("MFH_EDITION")
    if env in (DEVELOPER, FRIENDS):
        return env
    try:
        v = _resource("_edition.txt").read_text(encoding="utf-8").strip()
        return FRIENDS if v == FRIENDS else DEVELOPER
    except OSError:
        return DEVELOPER


def is_friends() -> bool:
    return edition() == FRIENDS
