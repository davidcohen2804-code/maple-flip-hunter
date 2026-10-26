"""FM Watcher auto-start: hidden process, no duplicates, stop only what we started, clear errors."""
from __future__ import annotations

import os
import subprocess
import sys
import time

import psutil

from maple_flip_hunter.fmwatcher_launcher import CREATE_NO_WINDOW, FMWatcherLauncher, find_python


def _fake_watcher(folder):
    folder.mkdir(parents=True, exist_ok=True)
    # mimics launcher.py: runs watcher.py as a child, like the real one
    (folder / "watcher.py").write_text("import time\nwhile True:\n    time.sleep(0.2)\n", encoding="utf-8")
    (folder / "launcher.py").write_text(
        "import subprocess, sys, os\nsubprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watcher.py')])\n",
        encoding="utf-8")


def test_start_hidden_no_duplicate_and_stop_only_ours(tmp_path, monkeypatch):
    folder = tmp_path / "FMWatcher" / "FMWatcher"
    _fake_watcher(folder)
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    calls = []
    real_popen = subprocess.Popen

    def spy(*a, **k):
        calls.append(k)
        return real_popen(*a, **k)
    monkeypatch.setattr(subprocess, "Popen", spy)
    la = FMWatcherLauncher(str(folder), tmp_path / "logs")
    ok, msg = la.start()
    assert ok and "started hidden" in msg and la.started_by_us and la.our_process_alive()
    assert calls[0]["cwd"] == str(folder) and calls[0]["stdin"] == subprocess.DEVNULL
    if sys.platform == "win32":
        assert calls[0]["creationflags"] & CREATE_NO_WINDOW
    assert (tmp_path / "logs" / "fmwatcher_launch.log").exists()
    state, text = la.status()
    assert state == "running" and "started by Maple Flip Hunter" in text
    # second start: no second copy
    ok2, msg2 = la.start()
    assert ok2 and "already running" in msg2 and len(calls) == 1
    # a second launcher instance (e.g. after Settings) sees the external process and does not start another
    lb = FMWatcherLauncher(str(folder), tmp_path / "logs")
    t0 = time.time()
    while time.time() - t0 < 5 and not lb.external_pids():
        time.sleep(0.1)                         # child watcher.py appears shortly after launcher.py
    assert lb.external_pids()
    ok3, msg3 = lb.start()
    assert ok3 and "already running" in msg3 and len(calls) == 1 and not lb.started_by_us
    lb.stop()                                   # must NOT kill a process it did not start
    assert la.our_process_alive()
    # ours: stop kills the whole tree (launcher + watcher child)
    pid = la.proc.pid
    kids = [c.pid for c in psutil.Process(pid).children(recursive=True)]
    la.stop()
    time.sleep(0.3)
    assert not psutil.pid_exists(pid) or psutil.Process(pid).status() == psutil.STATUS_ZOMBIE
    for k in kids:
        assert not psutil.pid_exists(k) or psutil.Process(k).status() == psutil.STATUS_ZOMBIE
    assert la.status()[0] == "stopped"


def test_clear_errors(tmp_path, monkeypatch):
    la = FMWatcherLauncher("", tmp_path)
    assert la.start() == (False, "folder not set (Settings → Sources → FM Watcher folder)")
    la = FMWatcherLauncher(str(tmp_path / "nope"), tmp_path)
    ok, msg = la.start()
    assert not ok and "folder not found" in msg and la.status()[0] == "error"
    empty = tmp_path / "empty"
    empty.mkdir()
    ok, msg = FMWatcherLauncher(str(empty), tmp_path).start()
    assert not ok and "launcher.py / watcher.py not found" in msg
    folder = tmp_path / "w"
    _fake_watcher(folder)
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: None)
    ok, msg = FMWatcherLauncher(str(folder), tmp_path).start()
    assert not ok and "Python not found" in msg
    # a watcher that crashes at once is reported, not silently 'started'
    (folder / "launcher.py").write_text("import sys\nsys.exit(3)\n", encoding="utf-8")
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    ok, msg = FMWatcherLauncher(str(folder), tmp_path).start()
    assert not ok and "exited immediately (code 3)" in msg


def test_find_python_returns_an_interpreter():
    p = find_python()
    assert p is None or os.path.exists(p)
