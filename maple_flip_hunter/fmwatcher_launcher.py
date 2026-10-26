"""Starts Club FM's FM Watcher in the background (no console window) when Maple Flip Hunter starts, and stops it
again on quit — only if we started it. FM Watcher keeps reading the game screen exactly as when started from
start.bat: it captures the screen with mss, it never needs its own window.

What start.bat does (download/source/start.bat): find a Python 3 (findpy.bat), then `python launcher.py`, which
self-updates from maplefm.gg and runs watcher.py. We do the same, with a hidden console and the output in
%APPDATA%\\MapleFlipHunter\\logs\\fmwatcher_launch.log."""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

log = logging.getLogger(__name__)

CREATE_NO_WINDOW = 0x08000000
CREATE_NEW_PROCESS_GROUP = 0x00000200


def find_python() -> Optional[str]:
    """Same search order as FM Watcher's findpy.bat."""
    local = os.environ.get("LOCALAPPDATA", "")
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    for p in [rf"{local}\Programs\Python\Python312\python.exe", rf"{local}\Programs\Python\Python313\python.exe",
              rf"{local}\Programs\Python\Python311\python.exe", rf"{pf}\Python312\python.exe",
              rf"{pf}\Python313\python.exe", rf"{pf}\Python311\python.exe", r"C:\Python312\python.exe"]:
        if local and os.path.isfile(p):
            return p
    try:
        out = subprocess.run(["py", "-3", "-c", "import sys;print(sys.executable)"], capture_output=True, text=True,
                             timeout=15, creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:  # noqa: BLE001
        pass
    for name in ("python3", "python"):
        p = shutil.which(name)
        if p and "WindowsApps" not in p:
            return p
    return None


class FMWatcherLauncher:
    def __init__(self, folder: str, log_dir: Optional[Path] = None) -> None:
        self.folder = Path(folder).expanduser() if folder else None
        self.log_dir = log_dir
        self.proc: Optional[subprocess.Popen] = None
        self.started_by_us = False
        self.error = ""
        self._log_fh = None

    # ----------------------------------------------------------------- state
    def external_pids(self) -> list[int]:
        """FM Watcher processes we did not start: a python running launcher.py / watcher.py from the folder."""
        if self.folder is None:
            return []
        try:
            import psutil
        except Exception:  # noqa: BLE001
            return []
        want = str(self.folder).lower().rstrip("\\/")
        pids = []
        for p in psutil.process_iter(["pid", "name"]):            # name only: cheap; cmdline/cwd only for pythons
            try:
                if "python" not in (p.info.get("name") or "").lower():
                    continue
                cmd = " ".join(p.cmdline()).lower()
                if not ("launcher.py" in cmd or "watcher.py" in cmd):
                    continue
                if want in cmd:
                    if self.proc is None or p.pid != self.proc.pid:
                        pids.append(p.pid)
                    continue
                try:
                    cwd = (p.cwd() or "").lower().rstrip("\\/")
                except Exception:  # noqa: BLE001
                    cwd = ""
                if cwd == want and (self.proc is None or p.pid != self.proc.pid):
                    pids.append(p.pid)
            except Exception:  # noqa: BLE001
                continue
        return pids

    def our_process_alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def status(self) -> tuple[str, str]:
        """('running' | 'stopped' | 'error', human text) for the UI."""
        if self.our_process_alive():
            return "running", f"FM Watcher running (started by Maple Flip Hunter, pid {self.proc.pid})"
        ext = self.external_pids()
        if ext:
            return "running", f"FM Watcher running (started separately, pid {ext[0]})"
        if self.error:
            return "error", f"FM Watcher not running — {self.error}"
        return "stopped", "FM Watcher not running"

    # ----------------------------------------------------------------- start / stop
    def start(self) -> tuple[bool, str]:
        """Start FM Watcher hidden. Returns (ok, message). Never starts a second copy."""
        self.error = ""
        if self.folder is None:
            self.error = "folder not set (Settings → Sources → FM Watcher folder)"
            return False, self.error
        if not self.folder.is_dir():
            self.error = f"folder not found: {self.folder}"
            return False, self.error
        launcher = self.folder / "launcher.py"
        watcher = self.folder / "watcher.py"
        if not launcher.exists() and not watcher.exists():
            self.error = f"launcher.py / watcher.py not found in {self.folder} (unzip FMWatcher.zip there)"
            return False, self.error
        if self.our_process_alive():
            return True, "already running (ours)"
        ext = self.external_pids()
        if ext:
            return True, f"already running (pid {ext[0]}); not starting a second copy"
        py = find_python()
        if not py:
            self.error = "Python not found — run FM Watcher's install.bat once"
            return False, self.error
        script = launcher if launcher.exists() else watcher
        try:
            if self.log_dir is not None:
                self.log_dir.mkdir(parents=True, exist_ok=True)
                self._log_fh = open(self.log_dir / "fmwatcher_launch.log", "a", encoding="utf-8", errors="replace")
                self._log_fh.write(f"\n--- {time.strftime('%Y-%m-%d %H:%M:%S')} start {script}\n")
                self._log_fh.flush()
            flags = (CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP) if sys.platform == "win32" else 0
            self.proc = subprocess.Popen([py, str(script)], cwd=str(self.folder), stdin=subprocess.DEVNULL,
                                         stdout=self._log_fh or subprocess.DEVNULL, stderr=subprocess.STDOUT,
                                         creationflags=flags)
        except Exception as exc:  # noqa: BLE001
            self.error = f"could not start FM Watcher: {exc}"
            log.exception("fmwatcher launcher: start failed")
            return False, self.error
        time.sleep(0.5)
        if self.proc.poll() is not None:
            self.error = f"FM Watcher exited immediately (code {self.proc.returncode}); see logs\\fmwatcher_launch.log"
            return False, self.error
        self.started_by_us = True
        log.info("fmwatcher launcher: started %s hidden (pid %d, python %s)", script.name, self.proc.pid, py)
        return True, f"started hidden (pid {self.proc.pid})"

    @staticmethod
    def _kill_tree(pid: int) -> None:
        """Kill launcher.py FIRST (it restarts watcher.py whenever the child exits), then every descendant."""
        import psutil

        try:
            root = psutil.Process(pid)
        except psutil.NoSuchProcess:
            return
        kids = root.children(recursive=True)
        for p in [root] + kids:
            try:
                p.kill()
            except Exception:  # noqa: BLE001
                pass
        psutil.wait_procs([root] + kids, timeout=5)

    def stop(self, force_external: bool = False) -> None:
        """Stop FM Watcher and its children. By default only a watcher WE started; `force_external=True`
        (the Stop Scanning button) also stops one started separately from the same folder."""
        pids: list[int] = []
        if self.started_by_us and self.proc is not None:
            pids.append(self.proc.pid)
        if force_external:
            pids += self.external_pids()
        for pid in pids:
            self._kill_tree(pid)
        # orphan sweep: anything from this folder that survived (e.g. a watcher re-spawned mid-kill)
        if pids or force_external:
            for pid in self.external_pids():
                self._kill_tree(pid)
        if pids:
            log.info("fmwatcher launcher: stopped (pids %s)", pids)
        self.started_by_us = False
        self.proc = None
        if self._log_fh:
            try:
                self._log_fh.close()
            except Exception:  # noqa: BLE001
                pass
            self._log_fh = None
