"""Engine pipeline tests with a fake adapter (spec §15 cases 4, 7, 8, 11, 13, 15)."""
from __future__ import annotations

import asyncio
import time

import pytest

from maple_flip_hunter.adapters.base import BaseAdapter
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import Level, Listing, MarketEvidence
from maple_flip_hunter.notifications import Notifier
from tests.conftest import make_evidence, make_listing


class FakeAdapter(BaseAdapter):
    name = "cozy"
    label = "fake-cozy"

    def __init__(self, evidence_fn):
        super().__init__()
        self.evidence_fn = evidence_fn

    async def poll(self):
        return []

    async def fetch_evidence(self, listing: Listing, comp_window_s: int = 86400):
        return self.evidence_fn(listing)


def build(settings=None, db=None, evidence_fn=None):
    settings = settings or Settings(min_buy_price=0, max_buy_price=75_000)
    db = db or Database(":memory:")
    notifier = Notifier(enable_audio=False)
    eng = MarketEngine(settings, db, notifier, adapters=[FakeAdapter(evidence_fn or (lambda l: make_evidence(l)))])
    alerts = []
    eng.on("alert", lambda opp, level, title, body: alerts.append((opp, level)))
    return eng, alerts


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_jackpot_flows_end_to_end_and_measures_latency():
    eng, alerts = build()
    lst = make_listing(price=16_000)
    opps = run(eng.ingest([lst]))
    assert opps and opps[0].level == Level.JACKPOT
    assert len(alerts) == 1 and alerts[0][1] == "JACKPOT"
    assert eng.metrics.alerts == 1
    assert eng.metrics.analysis_ms.last is not None and eng.metrics.analysis_ms.last < 2000
    assert 0 <= eng.metrics.source_to_alert_s.last < 10
    assert eng.db.was_alerted(lst.listing_key)


def test_4_stale_listing_is_not_alerted():
    eng, alerts = build()
    lst = make_listing(price=16_000, age_s=3600)
    opps = run(eng.ingest([lst]))
    assert opps and opps[0].stale
    assert alerts == []


def test_7_price_reduction_creates_fresh_alert_same_price_does_not():
    eng, alerts = build()
    a = make_listing(price=100_000, age_s=600)   # over budget -> no alert (observed in an earlier scan)
    run(eng.ingest([a]))
    assert alerts == []
    b = make_listing(price=16_000, age_s=1)      # same seller/item re-listed cheaper in a later scan -> alert
    run(eng.ingest([b]))
    assert len(alerts) == 1
    run(eng.ingest([b]))                   # same row seen again -> no duplicate alert
    assert len(alerts) == 1
    assert eng.metrics.listings_repriced == 1


def test_8_duplicate_from_second_source_is_merged():
    eng, alerts = build()
    a = make_listing(price=16_000, source="cozy")
    b = make_listing(price=16_000, source="msw", item_id=None)
    run(eng.ingest([a, b]))
    assert len(alerts) == 1
    assert eng.metrics.listings_duplicate == 1
    assert eng.db.listing_count() == 2       # both rows stored, one alert


def test_11_rapid_consecutive_updates():
    eng, alerts = build()
    rows = [make_listing(name=f"Scroll {i}", price=p, seller=f"s{i}", item_id=str(i)) for i, p in enumerate(range(10_000, 20_000, 500))]
    t0 = time.perf_counter()
    opps = run(eng.ingest(rows))
    elapsed = time.perf_counter() - t0
    assert len(opps) == len(rows)
    assert len(alerts) == len(rows)
    assert elapsed < 5.0
    assert eng.metrics.analysis_ms.p95 < 2000


def test_13_alert_deduplication_across_restarts(tmp_path):
    path = tmp_path / "m.sqlite3"
    eng, alerts = build(db=Database(path))
    lst = make_listing(price=16_000)
    run(eng.ingest([lst]))
    assert len(alerts) == 1
    eng.db.close()
    eng2, alerts2 = build(db=Database(path))   # fresh in-memory dedupe, same DB
    run(eng2.ingest([lst]))
    assert alerts2 == []
    assert eng2.metrics.duplicate_alerts_suppressed == 0  # it was a 'seen' row, never re-evaluated


def test_15_database_persistence_and_recovery(tmp_path):
    path = tmp_path / "p.sqlite3"
    db = Database(path)
    eng, _ = build(db=db)
    run(eng.ingest([make_listing(price=16_000)]))
    assert db.opportunity_count() == 1
    db.close()
    db2 = Database(path)
    assert db2.opportunity_count() == 1
    assert db2.listing_count() == 1
    assert len(db2.recent_opportunities(time.time() - 60)) == 1
    assert db2.notifications()[0]["level"] == "JACKPOT"
    removed = db2.cleanup(listings_days=0, events_days=0, notifications_days=0)
    assert removed["listings"] == 1


def test_prefilter_skips_rows_near_reference():
    eng, alerts = build()
    assert not eng._prefilter(make_listing(price=110_000, ref=120_000))
    assert eng._prefilter(make_listing(price=60_000, ref=120_000))
    assert not eng._prefilter(make_listing(price=80_000, ref=120_000))  # over budget


def test_sold_rows_are_recorded_as_sales_not_candidates():
    eng, alerts = build()
    sold = make_listing(price=16_000).model_copy(update={"sold": True})
    run(eng.ingest([sold]))
    assert alerts == []
    assert len(eng.db.sales_for(sold.item_key, 0)) == 1


def test_uncertain_hidden_when_disabled():
    s = Settings(show_uncertain=False, min_buy_price=0, max_buy_price=75_000)
    eng, alerts = build(settings=s, evidence_fn=lambda l: MarketEvidence(item_key=l.item_key, item_name=l.item_name))
    opps = run(eng.ingest([make_listing(price=16_000)]))
    assert opps == [] and alerts == []


def test_same_seller_several_units_at_different_prices_in_one_scan_are_independent():
    """ChatGPT review item 2: a shop showing the same item 3 times at 3 prices is 3 listings, not 2 reprices."""
    eng, alerts = build()
    base = make_listing(price=16_000)
    rows = [base.model_copy(update={"price": p, "listing_key": base.listing_key.rsplit("|", 1)[0] + f"|{p}",
                                    "source_seen_at": base.source_seen_at}) for p in (16_000, 17_000, 18_000)]
    run(eng.ingest(rows))
    assert eng.metrics.listings_new == 3 and eng.metrics.listings_repriced == 0
    assert eng.db.listing_count() == 3            # three independent rows, none marked superseded
    assert len(alerts) >= 1                       # the other units count as live competitors (conservative)
    assert eng.db.active_asks(base.item_key, 0).__len__() == 3
    # a LATER scan (new timestamp) with one of them cheaper -> that is a reprice
    later = base.model_copy(update={"price": 15_000, "listing_key": base.listing_key.rsplit("|", 1)[0] + "|15000",
                                    "source_seen_at": base.source_seen_at + 600})
    run(eng.ingest([later]))
    assert eng.metrics.listings_repriced == 1


def test_stale_reference_statistics_cap_confidence():
    from maple_flip_hunter.models import Level

    eng, alerts = build(evidence_fn=lambda l: make_evidence(l, age_s=8 * 3600))
    opps = run(eng.ingest([make_listing(price=16_000)]))
    assert opps[0].valuation.confidence <= 0.5
    assert any("h old" in r for r in opps[0].valuation.risks)
    assert opps[0].level == Level.GOOD            # still alerted, but never a JACKPOT on stale references


# ---------------------------------------------------------------- restart lifecycle (home-test bug: "client has been closed")
class RestartAdapter(BaseAdapter):
    """Own-client adapter whose HTTP goes to an in-memory transport, so a rebuilt client is verifiable."""
    name = "cozy"
    label = "restart-fake"
    interval_s = 0.05

    def __init__(self):
        self.polls = 0
        self.errors: list[str] = []
        super().__init__()

    def _make_client(self):
        import httpx

        def handler(req):
            return httpx.Response(200, json={"ok": True})
        c = httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x")
        return c

    async def poll(self):
        try:
            await self.fetch_json("/ping")
        except Exception as exc:  # noqa: BLE001
            self.errors.append(str(exc))
            raise
        self.polls += 1
        self.mark_success(1.0, 0)
        return []

    async def fetch_evidence(self, listing, comp_window_s=86400):
        return make_evidence(listing)


def _wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.05)
    return False


def test_start_stop_start_rebuilds_closed_clients_and_keeps_polling():
    a = RestartAdapter()
    eng = MarketEngine(Settings(), Database(":memory:"), Notifier(enable_audio=False), adapters=[a])
    eng.start()
    assert _wait(lambda: a.polls >= 2)
    eng.stop()
    assert a.closed and a.client.is_closed            # stop really closed the client
    polls_before = a.polls
    eng.start()                                       # second start must not reuse the closed client
    assert _wait(lambda: a.polls >= polls_before + 2)
    assert not a.client.is_closed
    assert not any("closed" in e for e in a.errors), a.errors
    assert a.health.status.value == "ONLINE"
    eng.stop()
    assert not eng.running and eng.scanner_state == "OFFLINE"


def test_settings_change_between_runs_rebuilds_adapters_from_settings():
    s = Settings(source_hene_public_enabled=False, source_public_market_enabled=False, source_cozy_enabled=False, source_msw_enabled=False, source_clubfm_enabled=False)
    eng = MarketEngine(s, Database(":memory:"), Notifier(enable_audio=False))   # adapters come from settings
    assert eng.adapters == []
    eng.start(); eng.stop()
    s2 = s.model_copy(update={"source_clubfm_enabled": True, "source_clubfm_permission_granted": False})
    eng.update_settings(s2)
    eng.start()
    assert [a.name for a in eng.adapters] == ["clubfm"]   # rebuilt from the new settings
    assert eng.adapters[0].health.status.value in ("UNSUPPORTED", "STARTING")
    eng.stop()
    assert eng.adapters[0].closed


def test_stop_is_idempotent_and_blocked_source_does_not_stop_others():
    import httpx

    class Blocked(RestartAdapter):
        name = "blocked"

        def _make_client(self):
            return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(403)), base_url="https://x")

    ok, bad = RestartAdapter(), Blocked()
    bad.retry_base_delay = 0.001
    eng = MarketEngine(Settings(), Database(":memory:"), Notifier(enable_audio=False), adapters=[ok, bad])
    eng.start()
    assert _wait(lambda: ok.polls >= 3 and bad.health.status.value == "AUTH REQUIRED")
    eng.last_data_at = time.time()
    eng._refresh_state()
    assert eng.scanner_state == "LIVE"
    eng.stop(); eng.stop()
    assert ok.closed and bad.closed


def test_stop_interrupts_a_long_poll_within_seconds():
    class SlowAdapter(RestartAdapter):
        async def poll(self):
            self.polls += 1
            await asyncio.sleep(60)           # a poll stuck in a long loop / back-off
            return []
    a = SlowAdapter()
    eng = MarketEngine(Settings(), Database(":memory:"), Notifier(enable_audio=False), adapters=[a])
    eng.start()
    assert _wait(lambda: a.polls >= 1)
    t0 = time.time()
    eng.stop()
    assert time.time() - t0 < 5.0
    assert a.closed and a.client.is_closed


def test_stop_during_an_in_flight_http_request_closes_client_and_returns_fast():
    import httpx

    class HangingAdapter(RestartAdapter):
        def _make_client(self):
            async def handler(req):
                await asyncio.sleep(30)           # the server never answers in time
                return httpx.Response(200, json={})
            return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x",
                                     timeout=httpx.Timeout(60.0))
    a = HangingAdapter()
    eng = MarketEngine(Settings(), Database(":memory:"), Notifier(enable_audio=False), adapters=[a])
    eng.start()
    time.sleep(0.5)                                   # request is now in flight
    t0 = time.time()
    eng.stop()
    assert time.time() - t0 < 5.0, "stop must not wait for the hanging request"
    assert a.closed and a.client.is_closed
    assert not eng._thread.is_alive()
    eng.start()                                       # and a fresh client is used afterwards
    assert not a.client.is_closed
    eng.stop()


def test_cross_source_duplicate_without_channel_is_merged_and_fresher_row_wins_nothing_twice():
    eng, alerts = build()
    a = make_listing(price=16_000, source="cozy", channel=24)
    b = make_listing(price=16_000, source="clubfm", item_id=None, channel=None)   # Club FM did not read the channel
    run(eng.ingest([a, b]))
    assert len(alerts) == 1 and eng.metrics.listings_duplicate == 1
