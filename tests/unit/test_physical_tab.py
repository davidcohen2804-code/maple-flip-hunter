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


def _settle(app, tab, timeout=10.0):
    """Start/Stop run on a worker thread: pump events until the tab reports done."""
    t0 = time.time()
    while time.time() - t0 < timeout and getattr(tab, "_busy", False):
        app.processEvents()
        time.sleep(0.02)
    for _ in range(5):
        app.processEvents()
        time.sleep(0.02)
    tab.refresh_status()


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
    assert w.tabs.count() == 3 and w.tabs.tabText(1) == "Physical Scanner"
    tab = w.physical
    tab.refresh_status()
    assert tab.status_pill.text() == "OFFLINE" and tab.btn_start.isEnabled() and not tab.btn_stop.isEnabled()
    msw_before = [a for a in eng.adapters if a.name == "msw"]
    # Start Scanning: FM Watcher process starts hidden; internet adapters are the same objects
    tab.start_scanning(); _settle(app, tab)
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
    assert tab.table.rowCount() == 0 and tab._flush_timer.isActive()      # batched, not one repaint per row
    tab.flush(); app.processEvents()
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
    tab.stop_scanning(); _settle(app, tab)
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


def test_start_stop_start_no_orphans_no_duplicates_no_autorestart_and_valuations(app, tmp_path, monkeypatch):
    import psutil

    from maple_flip_hunter.adapters.fmwatcher import FMWatcherAdapter

    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    folder = tmp_path / "FMWatcher"
    _fake_watcher(folder)
    _write(folder / "watcher_data_v2.json", _store([
        ("Rich", "Pink Elephant Slippers", 39_999, 1, 1, False, "FM1", 35, None, 5),          # above the 10k band
        ("NoobTrader", "Hat Accuracy Scroll: Intermediate", 8_000, 1, 1, False, "FM3", 7, None, 5),
        ("Nobody", "Strange Unknown Thing", 5_000, 1, 1, False, "FM2", 2, None, 5)]))
    s = Settings(source_cozy_enabled=False, source_msw_enabled=False, source_fmwatcher_dir=str(folder),
                 source_fmwatcher_autostart=True)                                              # defaults: band 2,500–10,000
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real = n.play
    n.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
    eng = MarketEngine(s, Database(":memory:"), n)

    async def evidence(lst):
        if "Unknown" in lst.item_name:
            return None
        ref = 45_000 if "Slippers" in lst.item_name else 42_000
        return make_evidence(lst, sold_median=ref, sold_count=40, sales24=6, asks=(int(ref * 1.03), int(ref * 1.05), int(ref * 1.1)))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    w = MainWindow(eng, s, n)
    w.show(); app.processEvents()
    tab = w.physical
    # Start → process + ingestion
    tab.start_scanning(); _settle(app, tab)
    pid1 = w.fmw_launcher.proc.pid
    t0 = time.time()
    while time.time() - t0 < 5 and not psutil.Process(pid1).children():
        time.sleep(0.1)
    kids1 = [c.pid for c in psutil.Process(pid1).children(recursive=True)]
    assert kids1 and tab.status_pill.text() == "ONLINE"
    # Stop → tree gone, ingestion paused, OFFLINE, and the autostart hook must NOT bring it back
    tab.stop_scanning(); _settle(app, tab)
    time.sleep(0.5)
    assert all(not psutil.pid_exists(p) or psutil.Process(p).status() == psutil.STATUS_ZOMBIE for p in [pid1] + kids1)
    assert not w.fmw_launcher.external_pids()
    assert tab.status_pill.text() == "OFFLINE" and tab.btn_start.isEnabled() and not tab.btn_stop.isEnabled()
    w.autostart_fmwatcher(); app.processEvents()
    assert w.fmw_launcher.proc is None and tab.status_pill.text() == "OFFLINE"
    fmw = next(a for a in eng.adapters if isinstance(a, FMWatcherAdapter))
    assert fmw.paused and asyncio.new_event_loop().run_until_complete(fmw.poll()) == []
    # Start again → exactly one process tree, ingestion resumes
    tab.start_scanning(); _settle(app, tab)
    pid2 = w.fmw_launcher.proc.pid
    assert pid2 != pid1 and not fmw.paused and tab.status_pill.text() == "ONLINE"
    t0 = time.time()
    while time.time() - t0 < 5 and len(w.fmw_launcher.external_pids()) < 1:
        time.sleep(0.1)
    trees = {p for p in w.fmw_launcher.external_pids()}
    assert all(psutil.Process(p).ppid() == pid2 for p in trees)            # only children of OUR launcher
    # new shop data → valuations for EVERY row (even above the band), alert only for the qualifying one
    rows = asyncio.new_event_loop().run_until_complete(fmw.poll())
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest(rows))
    for lst in rows:
        w.on_listing(lst, "new")
    for o in opps:
        w.on_opportunity(o)
    tab.flush(); app.processEvents()
    r_rich = tab.row_for_key(next(l.listing_key for l in rows if l.seller == "Rich"))
    assert tab.table.item(r_rich, 3).text() == "45,000" and tab.table.item(r_rich, 4).text().startswith("+")
    r_unk = tab.row_for_key(next(l.listing_key for l in rows if l.seller == "Nobody"))
    assert "no price data" in tab.table.item(r_unk, 3).text()
    r_noob = tab.row_for_key(next(l.listing_key for l in rows if l.seller == "NoobTrader"))
    assert tab.table.item(r_noob, 3).text() == "42,000"
    assert played and eng.metrics.alerts == 1                             # the 8,000 deal rang; 39,999 is outside the band
    assert "1 pass your filters" in tab.counter.text()
    tab.stop_scanning(); _settle(app, tab)
    w.tray.hide(); w.close()


def test_history_rows_get_valuations_after_restart_and_new_scan_alerts(app, tmp_path, monkeypatch):
    """Regression for 'scanner stopped' in Market value while ONLINE: rows loaded from history must never show a
    scanner-state text; they are re-valued once the engine runs, valuations persist, and a fresh scan after
    Stop → Start still prices and alerts."""
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    folder = tmp_path / "FMWatcher"
    _fake_watcher(folder)
    db_file = tmp_path / "m.sqlite3"
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000, source_cozy_enabled=False,
                 source_msw_enabled=False, source_fmwatcher_dir=str(folder), source_fmwatcher_autostart=False)

    def evidence_factory(eng):
        async def evidence(lst):
            if "Unknown" in lst.item_name:
                return None
            return make_evidence(lst, sold_median=42_000, sold_count=40, sales24=6, asks=(43_000, 44_000, 46_000))
        eng.gather_evidence = evidence  # type: ignore[method-assign]

    # --- session 1: rows arrive while the engine is NOT running (e.g. imported history) ---
    _write(folder / "watcher_data_v2.json", _store([
        ("OldShop", "Pink Elephant Slippers", 39_999, 1, 1, False, "FM1", 35, None, 600),
        ("Nobody", "Strange Unknown Thing", 5_000, 1, 1, False, "FM2", 2, None, 600)]))
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(str(db_file)), n)
    evidence_factory(eng)
    w = MainWindow(eng, s, n)
    w.show(); app.processEvents()
    from maple_flip_hunter.adapters.fmwatcher import FMWatcherAdapter
    fmw = next(a for a in eng.adapters if isinstance(a, FMWatcherAdapter))
    rows = asyncio.new_event_loop().run_until_complete(fmw.poll())
    for lst in rows:                      # stored + shown, but not valued (engine idle)
        eng.db.upsert_listing(lst, "x")
        w.on_listing(lst, "new")
    w.physical.flush(); app.processEvents()
    r_old = w.physical.row_for_key(rows[0].listing_key)
    txt = w.physical.table.item(r_old, 3).text()
    assert "scanner stopped" not in txt and "not valued yet" in txt
    w.tray.hide(); w.close(); eng.db.close()

    # --- session 2: restart; history loads; engine starts → backfill values the old rows ---
    n2 = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real = n2.play
    n2.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
    eng2 = MarketEngine(s, Database(str(db_file)), n2)
    evidence_factory(eng2)
    w2 = MainWindow(eng2, s, n2)
    w2.show(); app.processEvents()
    assert w2.physical.table.rowCount() == 2
    r_old = w2.physical.row_for_key(rows[0].listing_key)
    assert "scanner stopped" not in w2.physical.table.item(r_old, 3).text()
    eng2.start()
    try:
        assert w2.physical.backfill_valuations() == 2
        t0 = time.time()
        while time.time() - t0 < 10 and len(w2.physical.vals) < 2:
            app.processEvents(); time.sleep(0.02)
        w2.physical.flush(); app.processEvents()
        assert w2.physical.table.item(r_old, 3).text() == "42,000" and w2.physical.table.item(r_old, 4).text().startswith("-")   # 39,999 buy vs 42,000: a loss, shown
        r_unk = w2.physical.row_for_key(rows[1].listing_key)
        assert "no price data" in w2.physical.table.item(r_unk, 3).text()
        assert not played                                          # 10-minute-old rows: no alert on backfill
        assert eng2.db.valuations_for_source("fmwatcher")           # persisted
        # --- Stop → Start → a NEW shop scan → pricing → alert ---
        w2.physical.stop_scanning(); _settle(app, w2.physical)
        fmw2 = next(a for a in eng2.adapters if isinstance(a, FMWatcherAdapter))
        assert fmw2.paused
        w2.physical.start_scanning(); _settle(app, w2.physical)
        assert not fmw2.paused
        time.sleep(0.05)
        _write(folder / "watcher_data_v2.json", _store([
            ("OldShop", "Pink Elephant Slippers", 39_999, 1, 1, False, "FM1", 35, None, 700),
            ("NoobTrader", "Hat Accuracy Scroll: Intermediate", 8_000, 1, 1, False, "FM3", 7, None, 3)]))
        t0 = time.time()
        while time.time() - t0 < 15 and eng2.metrics.alerts < 1:
            app.processEvents(); time.sleep(0.05)
        t0 = time.time()
        while time.time() - t0 < 5 and not any("noobtrader" in k for k in w2.physical.listings):
            app.processEvents(); time.sleep(0.02)          # queued bridge signals reach the tab
        w2.physical.flush(); app.processEvents()
        assert eng2.metrics.alerts == 1 and played[-1] in ("melody", "jackpot", "bell")
        key = next(k for k in w2.physical.listings if "noobtrader" in k)
        r_new = w2.physical.row_for_key(key)
        assert w2.physical.table.item(r_new, 3).text() == "42,000" and "1 pass your filters" in w2.physical.counter.text()
        w2.physical.stop_scanning(); _settle(app, w2.physical)
    finally:
        eng2.stop()
    # --- session 3: valuations survive another restart (no 'not valued yet') ---
    eng3 = MarketEngine(s, Database(str(db_file)), n2)
    w3 = MainWindow(eng3, s, n2)
    assert w3.physical.table.item(w3.physical.row_for_key(rows[0].listing_key), 3).text() == "42,000"
    w3.tray.hide(); w3.close(); w2.tray.hide(); w2.close()
