"""Physical Scanner tab: start/stop only the FM Watcher process, status pill, live table with green qualifying rows,
history reloaded from the database, internet sources untouched."""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.adapters.fmwatcher import FMWatcherAdapter  # noqa: E402
from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402
from maple_flip_hunter.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import make_evidence  # noqa: E402
from tests.unit.test_fmwatcher import _store, _write  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _fake_watcher(folder):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "watcher.py").write_text("import time\nwhile True:\n    time.sleep(0.2)\n", encoding="utf-8")
    (folder / "launcher.py").write_text(
        "import subprocess, sys, os\nsubprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watcher.py')])\n",
        encoding="utf-8")


def test_physical_tab_start_stop_table_and_history(app, tmp_path, monkeypatch):
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    folder = tmp_path / "FMWatcher"
    _fake_watcher(folder)
    _write(folder / "watcher_data_v2.json", _store([
        ("NoobTrader", "Hat Accuracy Scroll: Intermediate", 8_000, 1, 1, False, "FM3", 7, None, 5),
        ("Pricey", "Pan Lid", 90_000, 1, 1, False, "FM2", 5, None, 9)]))
    db_file = tmp_path / "m.sqlite3"
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000, source_cozy_enabled=False,
                 source_msw_enabled=True, source_fmwatcher_dir=str(folder), source_fmwatcher_autostart=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real = n.play
    n.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
    eng = MarketEngine(s, Database(str(db_file)), n)

    async def evidence(lst):
        return make_evidence(lst, sold_median=42_000, sold_count=40, sales24=6, asks=(43_000, 44_000, 46_000))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    w = MainWindow(eng, s, n)
    w.show(); app.processEvents()
    assert w.tabs.count() == 2 and w.tabs.tabText(1) == "Physical Scanner"
    tab = w.physical
    tab.refresh_status()
    assert tab.status_pill.text() == "OFFLINE" and tab.btn_start.isEnabled() and not tab.btn_stop.isEnabled()
    msw_before = [a for a in eng.adapters if a.name == "msw"]
    # Start Scanning: FM Watcher process starts hidden; internet adapters are the same objects
    tab.start_scanning(); app.processEvents()
    assert w.fmw_launcher.started_by_us and tab.status_pill.text() == "ONLINE" and tab.btn_stop.isEnabled()
    assert [a for a in eng.adapters if a.name == "msw"] == msw_before
    # rows flow through the existing adapter -> engine -> table; the qualifying one is green and rang
    fmw = next(a for a in eng.adapters if isinstance(a, FMWatcherAdapter))
    rows = asyncio.new_event_loop().run_until_complete(fmw.poll())
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest(rows))
    for lst in rows:
        w.on_listing(lst, "new")
    for o in opps:
        w.on_opportunity(o)
    app.processEvents()
    assert tab.table.rowCount() == 2
    r = tab.row_for_key(next(l.listing_key for l in rows if l.seller == "NoobTrader"))
    assert tab.table.item(r, 0).text() == "NoobTrader" and tab.table.item(r, 2).text() == "8,000"
    assert tab.table.item(r, 3).text() == "42,000" and tab.table.item(r, 4).text().startswith("+")
    assert tab.table.item(r, 5).text() == "7" and tab.table.item(r, 6).text() == "FM3"
    from PySide6.QtCore import Qt as _Qt
    assert tab.table.item(r, 1).background().style() != _Qt.NoBrush            # green highlight
    assert played and n.sound_latency_ms                                   # existing alert system fired
    r2 = tab.row_for_key(next(l.listing_key for l in rows if l.seller == "Pricey"))
    assert tab.table.item(r2, 1).background().style() == _Qt.NoBrush          # not qualifying: no highlight
    assert "1 pass your filters" in tab.counter.text()
    # Stop Scanning: only the FM Watcher process stops; the scanner engine and msw keep running
    tab.stop_scanning(); app.processEvents()
    assert not w.fmw_launcher.started_by_us and tab.status_pill.text() == "OFFLINE"
    assert [a for a in eng.adapters if a.name == "msw"] == msw_before
    # history survives a restart of the app
    w.tray.hide(); w.close(); app.processEvents()
    eng.db.close()
    eng2 = MarketEngine(s, Database(str(db_file)), n)
    w2 = MainWindow(eng2, s, n)
    assert w2.physical.table.rowCount() == 2 and w2.physical.row_for_key(rows[0].listing_key) is not None
    assert "pass your filters" in w2.physical.counter.text()
    w2.tray.hide(); w2.close()


def test_physical_tab_error_when_folder_missing(app, tmp_path):
    s = Settings(source_cozy_enabled=False, source_msw_enabled=False, source_fmwatcher_autostart=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    w = MainWindow(MarketEngine(s, Database(":memory:"), n), s, n)
    w.physical.start_scanning()
    assert w.physical.status_pill.text() == "ERROR" and "folder not set" in w.physical.status_text.text()
    w.tray.hide(); w.close()
