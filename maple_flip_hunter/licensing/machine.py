"""Stable machine identity used to bind a trial to a PC (so reinstalling or deleting files does not reset it).
Built from Windows' MachineGuid + the system drive's volume serial + the Windows install id, hashed; on other
platforms from the machine-id file. The raw values never leave the PC — only the hash is sent."""
from __future__ import annotations

import hashlib
import os
import platform
import sys


def _win_parts() -> list[str]:
    parts = []
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography", 0,
                            winreg.KEY_READ | winreg.KEY_WOW64_64KEY) as k:
            parts.append(str(winreg.QueryValueEx(k, "MachineGuid")[0]))
    except Exception:  # noqa: BLE001
        pass
    try:
        import ctypes

        serial = ctypes.c_uint(0)
        drive = os.environ.get("SystemDrive", "C:") + "\\"
        if ctypes.windll.kernel32.GetVolumeInformationW(drive, None, 0, ctypes.byref(serial), None, None, None, 0):
            parts.append(f"{serial.value:08x}")
    except Exception:  # noqa: BLE001
        pass
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion") as k:
            parts.append(str(winreg.QueryValueEx(k, "InstallDate")[0]))
    except Exception:  # noqa: BLE001
        pass
    return parts


def machine_id() -> str:
    parts: list[str] = []
    if sys.platform == "win32":
        parts = _win_parts()
    else:
        for p in ("/etc/machine-id", "/var/lib/dbus/machine-id"):
            try:
                parts.append(open(p, encoding="utf-8").read().strip())
                break
            except OSError:
                continue
    if not parts:
        parts = [platform.node(), platform.machine()]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
