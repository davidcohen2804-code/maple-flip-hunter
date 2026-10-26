"""Secure update for the Friends Edition: the server publishes {version, url, sha256, signature}; the client checks
the Ed25519 signature with the bundled PUBLIC key, downloads the file, checks its sha256, and only then swaps the
running onefile EXE (via a tiny helper .bat) — an unsigned or tampered manifest/file is ignored."""
from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Optional

import httpx

from .. import __version__
from .tokens import _b64d

log = logging.getLogger(__name__)


def _ver(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in v.split(".") if x.isdigit())


def verify_manifest(manifest: dict[str, Any], public_key) -> bool:
    try:
        payload = {k: manifest[k] for k in ("version", "url", "sha256", "published")}
        body = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
        public_key.verify(_b64d(manifest["signature"]), body)
        return True
    except Exception:  # noqa: BLE001
        return False


def check(server: str, public_key) -> Optional[dict[str, Any]]:
    """Returns a verified manifest when a newer version is published, else None."""
    if not server or public_key is None:
        return None
    try:
        r = httpx.get(f"{server}/v1/update", timeout=8)
        if r.status_code != 200:
            return None
        m = r.json()
    except Exception:  # noqa: BLE001
        return None
    if not verify_manifest(m, public_key):
        log.warning("update manifest signature invalid — ignored")
        return None
    if _ver(str(m.get("version", "0"))) <= _ver(__version__):
        return None
    return m


def download(manifest: dict[str, Any]) -> Optional[Path]:
    """Download to a temp file and verify its sha256; None on any mismatch."""
    try:
        with httpx.stream("GET", manifest["url"], timeout=60, follow_redirects=True) as r:
            r.raise_for_status()
            h = hashlib.sha256()
            fd, tmp = tempfile.mkstemp(suffix=".exe", prefix="mfh-update-")
            with os.fdopen(fd, "wb") as f:
                for chunk in r.iter_bytes():
                    h.update(chunk)
                    f.write(chunk)
        if h.hexdigest().lower() != str(manifest["sha256"]).lower():
            os.unlink(tmp)
            log.warning("update download hash mismatch — discarded")
            return None
        return Path(tmp)
    except Exception as exc:  # noqa: BLE001
        log.warning("update download failed: %s", exc)
        return None


def apply(new_exe: Path) -> bool:
    """Replace the running onefile EXE with the verified download and restart (Windows only)."""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return False
    me = Path(sys.executable)
    bat = Path(tempfile.gettempdir()) / "mfh-update.bat"
    bat.write_text(f"""@echo off
:wait
tasklist /FI "PID eq {os.getpid()}" | find "{os.getpid()}" >nul && (timeout /t 1 >nul & goto wait)
copy /y "{new_exe}" "{me}" >nul
del "{new_exe}" >nul 2>&1
start "" "{me}"
del "%~f0"
""", encoding="utf-8")
    subprocess.Popen(["cmd", "/c", str(bat)], creationflags=0x08000000)
    return True
