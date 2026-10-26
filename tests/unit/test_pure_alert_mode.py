"""Distribution mode (physical features OFF, the default): the program is the Market Scanner only — no FM Watcher
auto-start, no Physical Scanner / Free Spot Detector tabs, no screen capture, no local-scan adapter — and the game
keeps running untouched while deals are found and alerted."""
from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtGui import QScreen  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.models import Level  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402
from maple_flip_hunter.ui.alert_popup import AlertPopup  # noqa: E402
from maple_flip_hunter.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import make_evidence, make_listing  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_default_build_has_no_physical_features(app, tmp_path, monkeypatch):
    assert Settings().physical_features_enabled is False and Settings().source_fmwatcher_autostart is False
    s = Settings(source_cozy_enabled=False, source_msw_enabled=False, source_fmwatcher_dir=str(tmp_path))  # folder set, but OFF
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n)
    assert not any(a.name == "fmwatcher" for a in eng.adapters)          # no local-scan adapter
    popen_calls = []
    monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen_calls.append(a) or (_ for _ in ()).throw(RuntimeError("no processes")))
    grabs = []
    monkeypatch.setattr(QScreen, "grabWindow", lambda *a, **k: grabs.append(a) or None)
    w = MainWindow(eng, s, n)
    w.show()
    end = time.time() + 2.5                                               # past the 1.5 s auto-start timer
    while time.time() < end:
        app.processEvents(); time.sleep(0.02)
    assert w.physical is None and w.spots is None
    assert not isinstance(w.centralWidget(), __import__("PySide6.QtWidgets", fromlist=["QTabWidget"]).QTabWidget)
    assert popen_calls == [] and grabs == []                             # nothing launched, nothing captured
    assert not w.fmw_launcher.started_by_us
    assert "FM Watcher" not in w.stat_labels["sources"].text()
    w.tray.hide(); w.close()


def test_game_keeps_running_while_alerts_work_without_interaction(app, tmp_path, monkeypatch):
    """A stand-in 'game' process runs; the alert system finds and alerts a deal; the game process is untouched:
    still alive, no signals sent, no child processes created by the app, no screen capture, no memory access."""
    import psutil

    game = subprocess.Popen([sys.executable, "-c", "import time\nwhile True: time.sleep(0.1)"])
    try:
        s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000, source_cozy_enabled=False, source_msw_enabled=False)
        n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
        played = []
        real = n.play
        n.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
        eng = MarketEngine(s, Database(":memory:"), n, adapters=[])

        async def evidence(lst):
            return make_evidence(lst, sold_median=42_000, sold_count=40, sales24=6, asks=(43_000, 44_000, 46_000))
        eng.gather_evidence = evidence  # type: ignore[method-assign]
        w = MainWindow(eng, s, n)
        w.show(); app.processEvents()
        me = psutil.Process()
        children_before = {c.pid for c in me.children(recursive=True)}
        popen_calls = []
        monkeypatch.setattr(subprocess, "Popen", lambda *a, **k: popen_calls.append(a) or (_ for _ in ()).throw(RuntimeError("no")))
        grabs = []
        monkeypatch.setattr(QScreen, "grabWindow", lambda *a, **k: grabs.append(a) or None)
        for old in list(AlertPopup._open):
            old.close()
        opps = asyncio.new_event_loop().run_until_complete(eng.ingest([make_listing(price=8_000, ref=42_000, seller="NoobTrader", age_s=5)]))
        app.processEvents()
        assert opps and opps[0].level in (Level.JACKPOT, Level.GOOD) and played and eng.metrics.alerts == 1
        end = time.time() + 1.0
        while time.time() < end:
            app.processEvents(); time.sleep(0.02)
        assert game.poll() is None                                        # the game is still running
        assert psutil.Process(game.pid).status() not in (psutil.STATUS_ZOMBIE, psutil.STATUS_DEAD)
        assert {c.pid for c in me.children(recursive=True)} - {game.pid} == children_before - {game.pid}
        assert popen_calls == [] and grabs == []
        w.tray.hide(); w.close()
    finally:
        game.kill()
