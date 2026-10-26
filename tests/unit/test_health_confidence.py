"""Scanner Health (statuses, failure history, bounded back-off, per-source restart, FM Watcher crash recovery)
and Deal Confidence (0–100 grades from real evidence, INSUFFICIENT DATA, no HIGH from one seller, sound gate)."""
from __future__ import annotations

import asyncio
import os
import sys
import time

import httpx
import pytest

from maple_flip_hunter.adapters.base import BaseAdapter, FetchError
from maple_flip_hunter.adapters.maplestory_watch import MapleStoryWatchAdapter
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import Comparable, Level, SourceStatus
from maple_flip_hunter.notifications import Notifier, ensure_sounds
from maple_flip_hunter.pricing import PricingEngine
from tests.conftest import load_fixture, make_evidence, make_listing


def run(c):
    return asyncio.new_event_loop().run_until_complete(c)


# ---------------------------------------------------------------- confidence
def test_confidence_from_real_maplestory_watch_fixture():
    def handler(req):
        if req.url.path == "/api/items":
            return httpx.Response(200, json=load_fixture("msw_items.json"))
        return httpx.Response(200, json=load_fixture("msw_item_angel_halo.json"))
    a = MapleStoryWatchAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))
    listings = run(a.poll())
    lst = listings[0]
    ev = run(a.fetch_evidence(lst, 24 * 3600))
    s = Settings(min_buy_price=0, max_buy_price=10_000_000)
    v = PricingEngine(s).value(lst, ev)
    assert v.reference and 0 <= v.confidence_score <= 100 and v.confidence_label in ("HIGH", "MEDIUM", "LOW")
    assert v.sample_count >= 3 and v.unique_sellers >= 1 and v.confidence_reason.startswith(v.confidence_label)
    assert "samples" in v.confidence_reason and "sellers" in v.confidence_reason
    assert v.confidence_score == int(round(v.confidence * 100))


def test_single_seller_never_high_and_insufficient_data(settings):
    lst = make_listing(price=16_000)
    ev = make_evidence(lst, sold_median=120_000, sold_count=2, sales24=1, asks=(124_000, 125_000, 130_000),
                       sold_prices=(118_000, 120_000, 122_000))
    for c in ev.comps + ev.active_asks:
        c.seller = "OnlyOne"
    v = PricingEngine(settings).value(lst, ev)
    assert v.unique_sellers == 1 and v.confidence_label != "HIGH" and "only one seller" in v.confidence_reason
    # many independent sellers, fresh sales → HIGH
    ev2 = make_evidence(lst, sold_median=120_000, sold_count=40, sales24=6, asks=(124_000, 125_000, 130_000))
    for i, c in enumerate(ev2.comps + ev2.active_asks):
        c.seller = f"seller{i}"
    v2 = PricingEngine(settings).value(lst, ev2)
    assert v2.confidence_label == "HIGH" and v2.unique_sellers >= 5 and v2.confidence_score >= 70
    # nothing at all → INSUFFICIENT DATA, never an alert
    from maple_flip_hunter.models import MarketEvidence
    v3 = PricingEngine(settings).value(lst, MarketEvidence(item_key=lst.item_key, item_name=lst.item_name))
    assert v3.confidence_label == "INSUFFICIENT DATA" and v3.reference is None and v3.net_profit is None


def test_sound_gate_by_confidence(tmp_path):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000, good_min_confidence=0.30, jackpot_min_confidence=0.30)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real = n.play
    n.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])

    async def thin(lst):   # one seller, thin sales → LOW/MEDIUM-ish evidence
        ev = make_evidence(lst, sold_median=42_000, sold_count=2, sales24=1, asks=(43_000,), sold_prices=(41_000, 42_000, 43_000))
        for c in ev.comps + ev.active_asks:
            c.seller = "solo"
        return ev
    eng.gather_evidence = thin  # type: ignore[method-assign]
    opps = run(eng.ingest([make_listing(price=8_000, ref=42_000, seller="a", age_s=5)]))
    assert opps and opps[0].valuation.confidence_label in ("LOW", "MEDIUM")
    low = [o for o in opps if o.valuation.confidence_label == "LOW"]
    if low:
        assert not played and low[0].notified_at                        # alert shown, no sound (default MEDIUM)
    s2 = s.model_copy(update={"alert_min_confidence_label": "LOW"})
    eng.update_settings(s2)
    opps2 = run(eng.ingest([make_listing(price=8_000, ref=42_000, seller="b", age_s=5)]))
    assert opps2 and played                                             # LOW allowed → sound
    s3 = s.model_copy(update={"alert_min_confidence_label": "HIGH"})
    eng.update_settings(s3)
    before = len(played)
    run(eng.ingest([make_listing(price=8_000, ref=42_000, seller="c", age_s=5)]))
    assert len(played) == before                                         # below HIGH → silent


# ---------------------------------------------------------------- health
class Flaky(BaseAdapter):
    name = "flaky"
    label = "flaky"
    interval_s = 0.05
    rate = 1000.0
    burst = 1000

    def __init__(self):
        super().__init__(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))))
        self.fail = True
        self.polls_done = 0
        self.sleeps: list[float] = []

    async def poll(self):
        self.polls_done += 1
        if self.fail:
            raise FetchError("boom")
        self.mark_success(1.0, 1)
        return [make_listing(name=f"X{self.polls_done}", price=8_000, ref=42_000, seller=f"s{self.polls_done}", source="flaky", age_s=1)]

    async def _sleep(self, seconds):
        self.sleeps.append(seconds)
        await super()._sleep(min(seconds, 0.05))


def test_failures_become_error_with_bounded_backoff_then_recover(tmp_path):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000)
    f = Flaky()
    eng = MarketEngine(s, Database(":memory:"), Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path)), adapters=[f])

    async def evidence(lst):
        return make_evidence(lst, sold_median=42_000, sold_count=40, sales24=6, asks=(43_000, 44_000, 46_000))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    eng.start()
    try:
        t0 = time.time()
        while time.time() - t0 < 5 and f.health.consecutive_failures < 4:
            time.sleep(0.02)
        assert f.health.status == SourceStatus.ERROR and f.health.errors >= 3
        assert len(f.health.recent_errors) >= 3 and "boom" in f.health.recent_errors[-1][1]
        assert max(f.sleeps) <= BaseAdapter.MAX_BACKOFF_S and any(x > f.interval_s for x in f.sleeps)   # back-off grows, bounded
        f.fail = False
        t0 = time.time()
        while time.time() - t0 < 5 and f.health.status != SourceStatus.ONLINE:
            time.sleep(0.02)
        assert f.health.status == SourceStatus.ONLINE and f.health.consecutive_failures == 0
        assert eng.metrics.listings_ingested >= 1
        assert "flaky" in eng.diagnostics_text() and "boom" in eng.diagnostics_text()
    finally:
        eng.stop()


def test_restart_one_source_leaves_the_others_alone(tmp_path):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000)
    a, b = Flaky(), Flaky()
    a.name, b.name = "src_a", "src_b"
    a.fail = b.fail = False
    eng = MarketEngine(s, Database(":memory:"), Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path)), adapters=[a, b])
    eng.gather_evidence = lambda lst: asyncio.sleep(0, result=None)  # type: ignore[method-assign]
    eng.start()
    try:
        time.sleep(0.4)
        client_b = b.client
        polls_b = b.polls_done
        assert eng.restart_source("src_a")
        time.sleep(0.6)
        assert a.health.restarts == 1 and a.health.status in (SourceStatus.ONLINE, SourceStatus.STARTING)
        assert b.client is client_b and b.health.restarts == 0 and b.polls_done > polls_b     # untouched, still polling
        assert not eng.restart_source("nope")
    finally:
        eng.stop()


def test_fmwatcher_crash_is_detected_and_restarted_with_a_cap(tmp_path, monkeypatch):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from maple_flip_hunter.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr("maple_flip_hunter.fmwatcher_launcher.find_python", lambda: sys.executable)
    folder = tmp_path / "FMWatcher"
    folder.mkdir()
    (folder / "launcher.py").write_text("import time\ntime.sleep(1.0)\n", encoding="utf-8")   # exits by itself = crash
    s = Settings(physical_features_enabled=True, source_cozy_enabled=False, source_msw_enabled=False, source_fmwatcher_dir=str(folder), source_fmwatcher_autostart=False)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    w = MainWindow(MarketEngine(s, Database(":memory:"), n), s, n)
    w.show()
    assert w.fmw_launcher.start()[0]
    restarts = 0
    t0 = time.time()
    while time.time() - t0 < 20:
        app.processEvents()
        w._watch_fmwatcher_process()
        restarts = len(getattr(w, "_fmw_restarts", []))
        if not w.fmw_launcher.started_by_us and restarts >= 3:
            break
        time.sleep(0.05)
    assert restarts == 3                                                   # restarted three times …
    time.sleep(0.5); w._watch_fmwatcher_process()
    assert not w.fmw_launcher.started_by_us                                # … then gave up (no restart loop)
    a = next(x for x in w.engine.adapters if x.name == "fmwatcher")
    assert a.health.status == SourceStatus.ERROR and "keeps exiting" in a.health.detail
    assert any("exited" in m for _, m in a.health.recent_errors)
    # the user's Stop is respected: no watching/restarting after it
    w.physical.stopped_by_user = True
    w._watch_fmwatcher_process()
    w.tray.hide(); w.close()


def test_health_dialog_lists_sources_and_copies_diagnostics(tmp_path):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    pytest.importorskip("PySide6.QtWidgets")
    from PySide6.QtWidgets import QApplication

    from maple_flip_hunter.ui.health_dialog import HealthDialog
    from maple_flip_hunter.ui.main_window import MainWindow
    app = QApplication.instance() or QApplication([])
    s = Settings(source_cozy_enabled=False, source_msw_enabled=True)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n)
    w = MainWindow(eng, s, n)
    msw = eng.adapters[0]
    msw.mark_failure(FetchError("HTTP 503"))
    dlg = HealthDialog(w)
    assert dlg.table.rowCount() == len(eng.adapters)
    assert dlg.table.item(0, 1).text() == "UNAVAILABLE" and "HTTP 503" in dlg.log.toPlainText()
    dlg.copy_diag()
    assert "HTTP 503" in QApplication.clipboard().text() and "[msw]" in QApplication.clipboard().text()
    dlg.accept(); w.tray.hide(); w.close()
