"""Friends Edition licence client.

* First start: POST /v1/activate {machine, version} → server records the trial start (its own clock) and returns a
  signed token. The 72 h run from the SERVER's first activation for this machine id; reinstalling, deleting
  %APPDATA% or the registry copy, or moving the clock does not start a new trial.
* Every start and every 10 minutes: POST /v1/status → fresh token (reflects activation codes you redeem server-side).
* Offline: the cached signed token is used; `exp` is compared with the latest trusted time, which is
  max(server time last seen, local time). A local clock set BACK below the last trusted time is treated as
  tampering (locked until the server is reachable). Offline grace is 24 h from the last server contact.
* Activation code: POST /v1/redeem {machine, code} → licensed token.

Nothing secret is stored in the client: only the server URL and its PUBLIC key."""
from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional

import httpx

from .. import __version__
from ..paths import app_data_dir
from .machine import machine_id
from .tokens import load_public_key, verify

log = logging.getLogger(__name__)

TRIAL_SECONDS = 72 * 3600
OFFLINE_GRACE_S = 24 * 3600
CLOCK_SLACK_S = 300
# Fill these for your own deployment (build/windows/build_friends.bat writes them into friends_config.json).
DEFAULT_SERVER = os.environ.get("MFH_LICENSE_SERVER", "")
DEFAULT_PUBLIC_KEY = os.environ.get("MFH_LICENSE_PUBKEY", "")


@dataclass
class LicenseState:
    status: str                       # trial | licensed | expired | locked | unknown
    expires_at: Optional[float]
    remaining_s: Optional[float]
    message: str
    online: bool
    kind: str = "trial"               # trial | activation (14-day code) | lifetime
    grace_left_s: Optional[float] = None   # set while the server is unreachable: how long the cached token still counts

    @property
    def remaining_text(self) -> str:
        base = self._remaining_base()
        if self.grace_left_s is not None and base:
            # the server is down or over its free daily quota: say so instead of failing silently
            h, m = int(self.grace_left_s // 3600), int((self.grace_left_s % 3600) // 60)
            return f"{base} · ⚠ licence server unreachable — works offline for {h} h {m} min more"
        return base

    def _remaining_base(self) -> str:
        if self.kind == "lifetime" and self.status == "licensed":
            return "Lifetime licence"
        if self.kind == "revoked":
            return "Licence revoked"
        if self.remaining_s is None:
            return ""
        d, h = int(self.remaining_s // 86400), int((self.remaining_s % 86400) // 3600)
        label = "Trial" if self.kind == "trial" else "Activation"
        return f"{label}: {d} day{'s' if d != 1 else ''} {h} h left" if d else f"{label}: {h} h {int((self.remaining_s % 3600) // 60)} min left"


class LicenseClient:
    def __init__(self, server: str = "", public_key: str = "", state_file: Optional[Path] = None,
                 registry_key: str = r"Software\MapleFlipHunter\Friends", timeout: float = 8.0) -> None:
        cfg = self._load_config()
        self.server = (server or cfg.get("server") or DEFAULT_SERVER).rstrip("/")
        pub = public_key or cfg.get("public_key") or DEFAULT_PUBLIC_KEY
        self.public_key = load_public_key(pub) if pub else None
        self.state_file = state_file or (app_data_dir() / "friends_license.json")
        self.registry_key = registry_key
        self.timeout = timeout
        self.version = __version__
        self.machine = machine_id()
        self.token: Optional[str] = None
        self.last_server_time: float = 0.0       # highest server time ever seen (persisted)
        self.last_contact: float = 0.0           # local time of the last successful server call
        self.last_error = ""
        self._load_state()

    # ------------------------------------------------------------------ config / persistence
    @staticmethod
    def _load_config() -> dict[str, Any]:
        base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parents[1]))
        for p in (base / "maple_flip_hunter" / "friends_config.json", Path(__file__).resolve().parents[1] / "friends_config.json"):
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
        return {}

    def _load_state(self) -> None:
        data: dict[str, Any] = {}
        try:
            data = json.loads(self.state_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            pass
        reg = self._registry_read()
        # take the copy with the highest trusted time (deleting one copy does not roll anything back)
        for src in (data, reg):
            if src.get("last_server_time", 0) >= self.last_server_time:
                self.last_server_time = float(src.get("last_server_time", 0) or 0)
                self.token = src.get("token") or self.token
                self.last_contact = float(src.get("last_contact", 0) or 0)

    def _save_state(self) -> None:
        data = {"token": self.token, "last_server_time": self.last_server_time, "last_contact": self.last_contact}
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            self.state_file.write_text(json.dumps(data), encoding="utf-8")
        except OSError:
            pass
        self._registry_write(data)

    def _registry_read(self) -> dict[str, Any]:
        if sys.platform != "win32":
            return {}
        try:
            import winreg

            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, self.registry_key) as k:
                return json.loads(winreg.QueryValueEx(k, "state")[0])
        except Exception:  # noqa: BLE001
            return {}

    def _registry_write(self, data: dict[str, Any]) -> None:
        if sys.platform != "win32":
            return
        try:
            import winreg

            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, self.registry_key) as k:
                winreg.SetValueEx(k, "state", 0, winreg.REG_SZ, json.dumps(data))
        except Exception:  # noqa: BLE001
            pass

    # ------------------------------------------------------------------ time
    def trusted_now(self) -> tuple[float, bool]:
        """(time to compare expiry against, clock_ok). A local clock below the last seen server time is suspicious."""
        now = time.time()
        if self.last_server_time and now + CLOCK_SLACK_S < self.last_server_time:
            return self.last_server_time, False
        return max(now, self.last_server_time), True

    # ------------------------------------------------------------------ server
    def _post(self, path: str, body: dict[str, Any]) -> Optional[dict[str, Any]]:
        if not self.server:
            self.last_error = "licence server not configured"
            return None
        try:
            r = httpx.post(f"{self.server}{path}", json=body, timeout=self.timeout,
                           headers={"User-Agent": f"MapleFlipHunterFriends/{self.version}"})
            if r.status_code != 200:
                self.last_error = f"server replied {r.status_code}"
                try:
                    self.last_error = r.json().get("error", self.last_error)
                except Exception:  # noqa: BLE001
                    pass
                return None
            data = r.json()
            self.last_error = ""
            return data
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"cannot reach licence server ({exc.__class__.__name__})"
            return None

    def _accept(self, data: dict[str, Any]) -> bool:
        tok = data.get("token")
        payload = verify(tok, self.public_key) if (tok and self.public_key) else None
        if not payload or payload.get("m") != self.machine:
            self.last_error = "server token invalid"
            return False
        self.token = tok
        self.last_server_time = max(self.last_server_time, float(payload.get("iat", 0)), float(data.get("server_time", 0)))
        self.last_contact = time.time()
        self._save_state()
        return True

    def activate(self) -> bool:
        data = self._post("/v1/activate", {"machine": self.machine, "version": self.version})
        return bool(data and self._accept(data))

    def refresh(self) -> bool:
        data = self._post("/v1/status", {"machine": self.machine, "version": self.version})
        return bool(data and self._accept(data))

    def redeem(self, code: str) -> tuple[bool, str]:
        data = self._post("/v1/redeem", {"machine": self.machine, "code": code.strip(), "version": self.version})
        if data and self._accept(data):
            return True, "activated"
        return False, self.last_error or "invalid code"

    # ------------------------------------------------------------------ decision
    def state(self) -> LicenseState:
        payload = verify(self.token, self.public_key) if (self.token and self.public_key) else None
        now, clock_ok = self.trusted_now()
        online = bool(self.last_contact and time.time() - self.last_contact < 120)
        if payload is None:
            return LicenseState("unknown", None, None, self.last_error or "not activated yet", online)
        kind = payload.get("kind", "trial")
        if not clock_ok:
            return LicenseState("locked", payload.get("exp"), 0, "the system clock was moved back — reconnect to continue", online, kind)
        if self.last_contact and time.time() - self.last_contact > OFFLINE_GRACE_S:
            return LicenseState("locked", payload.get("exp"), 0, "no contact with the licence server for over 24 hours", online, kind)
        st = payload.get("st", "expired")
        exp = payload.get("exp")
        if st == "expired" or (exp is not None and now >= float(exp)):
            msg = ("Your 3-day free trial has expired." if kind == "trial" else
                   "This licence was revoked. Please contact the developer." if kind == "revoked" else
                   "Your 14-day activation has expired.")
            return LicenseState("expired", exp, 0, msg, online, kind)
        remaining = (float(exp) - now) if exp is not None else None
        grace = None
        if not online and self.last_contact and self.last_error:
            grace = max(0.0, OFFLINE_GRACE_S - (time.time() - self.last_contact))
        return LicenseState(st, exp, remaining, "ok", online, kind, grace)

    def ensure(self) -> LicenseState:
        """Start-up path: refresh from the server (or activate the first time), then decide."""
        if not (self.token and self.refresh()):
            self.activate()
        return self.state()
