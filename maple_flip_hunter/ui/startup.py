"""Optional 'run at Windows startup' via the per-user Run registry key (no admin rights)."""
from __future__ import annotations

import logging
import sys

log = logging.getLogger(__name__)
KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
NAME = "MapleFlipHunter"


def set_run_on_startup(enabled: bool) -> bool:
    if sys.platform != "win32":
        return False
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY, 0, winreg.KEY_SET_VALUE) as k:
            if enabled:
                exe = sys.executable
                cmd = f'"{exe}"' if getattr(sys, "frozen", False) else f'"{exe}" -m maple_flip_hunter'
                winreg.SetValueEx(k, NAME, 0, winreg.REG_SZ, cmd)
            else:
                try:
                    winreg.DeleteValue(k, NAME)
                except FileNotFoundError:
                    pass
        return True
    except OSError as exc:
        log.warning("startup registration failed: %s", exc)
        return False
