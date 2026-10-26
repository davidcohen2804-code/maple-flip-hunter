"""UI responsiveness: thousands of rows + FM Watcher active must not stall the GUI thread; the stats tick must not
wait on the engine's database lock; Start/Stop stay responsive under load; the watchdog detects/logs stalls."""
from __future__ import annotations

import asyncio
import os
import sys
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
from PySide6.QtCore import QTimer  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.models import Level, Valuation  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402
from maple_flip_hunter.ui.main_window import MainWindow  # noqa: E402
from tests.conftest import make_evidence, make_listing  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _pump(app, seconds):
    """Run the event loop for `seconds` and return the worst gap between 20 ms timer ticks (UI stall)."""
    worst = [0.0]
    last = [time.perf_counter()]

    def tick():
        now = time.perf_counter()
        worst[0] = max(worst[0], (now - last[0]) * 1000 - 20)
        last[0] = now
    t = QTimer()
    t.timeout.connect(tick)
    t.start(20)
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents()
        time.sleep(0.002)
    t.stop()
    return worst[0]


def test_thousands_of_rows_do_not_stall_the_ui(app, tmp_path):
    s = Settings(physical_features_enabled=True, min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000,
                 source_cozy_enabled=False, source_msw_enabled=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])
    w = MainWindow(eng, s, n)
    w.show(); app.processEvents()
    rows = [make_listing(name=f"Item {i % 300}", price=5_000 + i, seller=f"shop{i % 400}", source="fmwatcher", age_s=i % 3000)
            for i in range(5_000)]
    val = Valuation(reference=40_000, quick=38_000, net_profit=30_000, confidence=0.9, confidence_label="HIGH", liquidity="HIGH")
    t0 = time.perf_counter()
    for lst in rows:                                   # what 5,000 bridge signals do on the GUI thread
        w.on_listing(lst, "new")
        w.physical.on_valued(lst, val, Level.NONE)
    queued_ms = (time.perf_counter() - t0) * 1000
    assert queued_ms < 1_500, f"queueing 5,000 rows took {queued_ms:.0f} ms"
    t0 = time.perf_counter()
    w.physical.flush(); app.processEvents()
    flush_ms = (time.perf_counter() - t0) * 1000
    assert w.physical.table.rowCount() == 2_000           # capped, newest kept
    assert flush_ms < 6_000, f"one batched flush of 5,000 rows took {flush_ms:.0f} ms"
    # with 2,000 rows on screen the periodic refresh must stay cheap
    t0 = time.perf_counter()
    w.physical.refresh_ages(); w.model.refresh_ages(); w._refresh_stats()
    tick_ms = (time.perf_counter() - t0) * 1000
    assert tick_ms < 300, f"stats tick with 2,000 rows took {tick_ms:.0f} ms"
    worst = _pump(app, 1.5)
    assert worst < 300, f"worst UI stall while idle with 2,000 rows: {worst:.0f} ms"
    w.tray.hide(); w.close()


def test_stats_tick_never_waits_on_the_engine_db_lock(app, tmp_path):
    """A long ingest transaction holds the DB lock on the engine thread; the UI tick must still return at once."""
    s = Settings(source_cozy_enabled=False, source_msw_enabled=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    db = Database(":memory:")
    eng = MarketEngine(s, db, n, adapters=[])
    eng.running = True
    eng._coverage_cache = {"catalog_items": 1, "items_with_listings": 2, "listings_1h": 3}
    w = MainWindow(eng, s, n)
    w.show(); app.processEvents()
    released = threading.Event()

    def hold_lock():
        with db._lock:                                   # like a big ingest batch
            released.wait(3)
    t = threading.Thread(target=hold_lock); t.start()
    time.sleep(0.05)
    t0 = time.perf_counter()
    w._refresh_stats()
    dt = (time.perf_counter() - t0) * 1000
    released.set(); t.join()
    assert dt < 200, f"UI tick blocked on the DB lock for {dt:.0f} ms"
    assert "2" in w.stat_labels["coverage"].text()
    w.tray.hide(); w.close()


def test_start_stop_buttons_respond_during_load(app, tmp_path, monkeypatch):
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    folder = tmp_path / "FMWatcher"
    folder.mkdir()
    (folder / "watcher.py").write_text("import time\nwhile True:\n    time.sleep(0.2)\n", encoding="utf-8")
    (folder / "launcher.py").write_text(
        "import subprocess, sys, os\nsubprocess.call([sys.executable, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'watcher.py')])\n",
        encoding="utf-8")
    s = Settings(physical_features_enabled=True, source_cozy_enabled=False, source_msw_enabled=False, source_fmwatcher_dir=str(folder), source_fmwatcher_autostart=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n)
    w = MainWindow(eng, s, n)
    w.show(); app.processEvents()
    val = Valuation(reference=40_000, quick=38_000, net_profit=30_000, confidence=0.9, confidence_label="HIGH", liquidity="HIGH")
    for i in range(3_000):                               # load arriving while the user clicks
        lst = make_listing(name=f"Item {i}", price=6_000, seller=f"s{i}", source="fmwatcher", age_s=5)
        w.on_listing(lst, "new"); w.physical.on_valued(lst, val, Level.NONE)
    t0 = time.perf_counter()
    w.physical.btn_start.click()                         # must return at once: the work is on a thread
    click_ms = (time.perf_counter() - t0) * 1000
    assert click_ms < 300, f"Start click blocked the UI for {click_ms:.0f} ms"
    assert "starting" in w.physical.status_text.text()
    t1 = time.time()
    while time.time() - t1 < 15 and getattr(w.physical, "_busy", False):
        app.processEvents(); time.sleep(0.02)
    assert w.fmw_launcher.started_by_us
    w.physical.refresh_status()
    assert w.physical.status_pill.text() == "ONLINE"
    w.physical.flush(); app.processEvents()
    t0 = time.perf_counter()
    w.physical.btn_stop.click()
    click_ms = (time.perf_counter() - t0) * 1000
    assert click_ms < 300, f"Stop click blocked the UI for {click_ms:.0f} ms"
    t1 = time.time()
    while time.time() - t1 < 15 and getattr(w.physical, "_busy", False):
        app.processEvents(); time.sleep(0.02)
    assert not w.fmw_launcher.started_by_us
    w.physical.refresh_status()
    assert w.physical.status_pill.text() == "OFFLINE"
    w.tray.hide(); w.close()


def test_watchdog_detects_and_logs_a_stall(app, tmp_path, caplog):
    import logging

    s = Settings(source_cozy_enabled=False, source_msw_enabled=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])
    w = MainWindow(eng, s, n)
    w.show()
    _pump(app, 0.5)
    before = eng.metrics.ui_stalls
    with caplog.at_level(logging.WARNING, logger="maple_flip_hunter.ui.watchdog"):
        w.watchdog.mark("synthetic sleep")
        time.sleep(0.6)                                  # block the GUI thread
        _pump(app, 0.5)
    assert eng.metrics.ui_stalls > before and w.watchdog.worst_ms > 300
    assert any("UI_STALL" in r.message and "synthetic sleep" in r.message for r in caplog.records)
    assert eng.metrics.snapshot()["ui_stalls"] == eng.metrics.ui_stalls
    w.tray.hide(); w.close()
