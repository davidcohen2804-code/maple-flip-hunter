"""Availability verification: alert first (no network before the sound), background check afterwards with
alerted deals first; ACTIVE / UNVERIFIED / REMOVED / SOLD; reprice & re-appearance; sources that cannot verify;
load: 500 opportunities never delay discovery polls."""
from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from maple_flip_hunter.adapters.base import BaseAdapter
from maple_flip_hunter.adapters.maplestory_watch import MapleStoryWatchAdapter
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import Level, Listing
from maple_flip_hunter.notifications import Notifier, ensure_sounds
from tests.conftest import make_evidence, make_listing


class FakeSource(BaseAdapter):
    """Synthetic source: poll() hands out whatever is in .batch; verify() answers from .answers (None = can't tell)."""
    name = "fake"
    label = "fake"
    interval_s = 0.05
    rate = 1000.0
    burst = 1000
    verify_supported = True
    verify_limit_note = "fake limit"

    def __init__(self, **kw):
        super().__init__(client=httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404))), **kw)
        self.batch: list[Listing] = []
        self.answers: dict[str, tuple[str, float, str] | None] = {}
        self.verify_calls: list[tuple[float, str]] = []
        self.poll_times: list[float] = []
        self.verify_delay = 0.0

    async def poll(self):
        self.poll_times.append(time.time())
        b, self.batch = self.batch, []
        return b

    async def verify(self, listing):
        self.verify_calls.append((time.time(), listing.listing_key))
        if self.verify_delay:
            await asyncio.sleep(self.verify_delay)
        return self.answers.get(listing.listing_key)


def _engine(tmp_path, adapters, **kw):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000, **kw)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n, adapters=adapters)

    async def evidence(lst):
        r = lst.source_ref_price
        return make_evidence(lst, sold_median=r, sold_count=40, sales24=6, asks=(int(r * 1.03), int(r * 1.05), int(r * 1.1)))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    return eng, n


def _wait(cond, timeout=5.0):
    t0 = time.time()
    while time.time() - t0 < timeout:
        if cond():
            return True
        time.sleep(0.02)
    return False


def test_alert_fires_before_any_verification_and_statuses_follow(tmp_path):
    src = FakeSource()
    eng, n = _engine(tmp_path, [src])
    events = []
    eng.on("availability", lambda o, st, note, ch: events.append((o.key, st, note)))
    l_sold = make_listing(name="A", price=8_000, ref=42_000, seller="s1", source="fake", age_s=3)
    l_gone = make_listing(name="B", price=8_000, ref=42_000, seller="s2", source="fake", age_s=3)
    l_ok = make_listing(name="C", price=8_000, ref=42_000, seller="s3", source="fake", age_s=3)
    l_unk = make_listing(name="D", price=8_000, ref=42_000, seller="s4", source="fake", age_s=3)
    now = time.time()
    src.answers = {l_sold.listing_key: ("SOLD", now, "slot sold"), l_gone.listing_key: ("REMOVED", now, "closed"),
                   l_ok.listing_key: ("ACTIVE", now, "seen"), l_unk.listing_key: None}
    src.batch = [l_sold, l_gone, l_ok, l_unk]
    eng.start()
    try:
        assert _wait(lambda: n.last_play_at is not None and len(events) >= 4, 8), events
        # 1) the sound was handed to the backend BEFORE the first verification request
        assert src.verify_calls and n.last_play_at <= src.verify_calls[0][0]
        assert eng.metrics.alerts == 4
        st = {k: (s, note) for k, s, note in events}
        assert st[l_sold.listing_key][0] == "SOLD" and st[l_gone.listing_key][0] == "REMOVED"
        assert st[l_ok.listing_key][0] == "ACTIVE"
        assert st[l_unk.listing_key][0] == "UNVERIFIED" and "fake limit" in st[l_unk.listing_key][1]
        opp = eng._opps[l_sold.listing_key]
        assert opp.gone and opp.last_verified_at == pytest.approx(now, abs=1)
        row = eng.db.availability(l_sold.listing_key)
        assert row["status"] == "SOLD" and row["source"] == "fake"
        # 2) alerted rows were checked before the un-alerted one (priority 0 before 1)
        unalerted = make_listing(name="E", price=8_000, ref=42_000, seller="s5", source="fake", age_s=3, category="Equip", stats=None)
    finally:
        eng.stop()
    # the DB still holds every listing: nothing deleted
    assert eng.db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 4


def test_reprice_and_reappearance_update_availability(tmp_path):
    src = FakeSource()
    eng, n = _engine(tmp_path, [src])
    events = []
    eng.on("availability", lambda o, st, note, ch: events.append((o.key, st, note)))
    loop = asyncio.new_event_loop()
    first = make_listing(name="A", price=8_000, ref=42_000, seller="s1", source="fake", age_s=200)
    opps = loop.run_until_complete(eng.ingest([first]))
    assert opps and opps[0].availability == "UNVERIFIED"
    # seen again by the source later -> ACTIVE as of that observation
    again = make_listing(name="A", price=8_000, ref=42_000, seller="s1", source="fake", age_s=5)
    loop.run_until_complete(eng.ingest([again]))
    assert events[-1][1] == "ACTIVE" and eng._opps[first.listing_key].availability == "ACTIVE"
    # the seller changes the price (older scan gap satisfied) -> old opportunity REMOVED, new one evaluated
    repriced = make_listing(name="A", price=9_000, ref=42_000, seller="s1", source="fake", age_s=0)
    repriced.source_seen_at = time.time() + 200      # clearly a later scan
    loop.run_until_complete(eng.ingest([repriced]))
    old = eng._opps[first.listing_key]
    assert old.availability == "REMOVED" and "9,000" in old.availability_note
    assert eng.metrics.verified_gone == 1


def test_msw_verify_uses_explicit_flags_only():
    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"listings": [
            {"seller": "Bob", "channel": 3, "unit": 8_000, "price": 8_000, "soldOut": True, "seen": 1000},
            {"seller": "Ann", "channel": 3, "unit": 8_000, "price": 8_000, "closed": True, "seen": 1000},
            {"seller": "Cid", "channel": 3, "unit": 8_000, "price": 8_000, "seen": 1500},
            {"seller": "Dan", "channel": 3, "unit": 9_500, "price": 9_500, "seen": 1600},
        ]})
    a = MapleStoryWatchAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), permission_granted=True)
    run = lambda c: asyncio.new_event_loop().run_until_complete(c)  # noqa: E731
    mk = lambda seller: make_listing(name="Zard", price=8_000, seller=seller, channel=3, source="msw")  # noqa: E731
    assert run(a.verify(mk("Bob")))[0] == "SOLD"
    assert run(a.verify(mk("Ann")))[0] == "REMOVED"
    st, at, note = run(a.verify(mk("Cid")))
    assert st == "ACTIVE" and at == 1500
    st, _, note = run(a.verify(mk("Dan")))
    assert st == "REMOVED" and "9,500" in note
    assert run(a.verify(mk("Nobody"))) is None            # absent from the page: not 'sold', just unknown
    assert len(calls) == 1                                 # one request served all five (60 s cache)
    assert a.verify_bucket is not a.bucket                 # its own budget, not the discovery one


def test_sources_that_cannot_verify_report_the_limitation(tmp_path):
    from maple_flip_hunter.adapters.cozy import CozyAdapter
    assert CozyAdapter.verify_supported is False
    src = FakeSource()
    src.verify_supported = False
    src.verify_limit_note = "cannot confirm"
    eng, n = _engine(tmp_path, [src])
    events = []
    eng.on("availability", lambda o, st, note, ch: events.append((st, note)))
    src.batch = [make_listing(name="A", price=8_000, ref=42_000, seller="s1", source="fake", age_s=3)]
    eng.start()
    try:
        assert _wait(lambda: len(events) >= 1, 8)
        assert events[0] == ("UNVERIFIED", "cannot confirm") and not src.verify_calls
    finally:
        eng.stop()


def test_load_500_opportunities_do_not_delay_discovery_polls(tmp_path):
    """500 rows alert; the verifier (slow: 20 ms per answer) must not slow the source's polling cadence or the
    ingest->sound latency."""
    src = FakeSource()
    src.verify_delay = 0.02
    eng, n = _engine(tmp_path, [src])
    rows = [make_listing(name=f"Item{i}", price=8_000, ref=42_000, seller=f"s{i}", source="fake", age_s=3) for i in range(500)]
    now = time.time()
    src.answers = {r.listing_key: ("ACTIVE", now, "seen") for r in rows}
    src.batch = rows
    eng.start()
    try:
        assert _wait(lambda: eng.metrics.alerts >= 500, 60)
        t_alerts = time.time()
        assert _wait(lambda: eng.metrics.verifications >= 100, 30)
        # discovery kept polling every ~50 ms while 100+ verifications ran
        gaps = [b - a for a, b in zip(src.poll_times, src.poll_times[1:]) if a > t_alerts]
        assert gaps and max(gaps) < 1.6, max(gaps)      # the poll loop itself has a 1 s floor
        lat = n.sound_latency_ms
        assert len(lat) == 500 and sum(lat) / len(lat) < 2_000
        # a new deal arriving while the verifier is busy still alerts immediately
        late = make_listing(name="Late", price=8_000, ref=42_000, seller="late", source="fake", age_s=3)
        t0 = time.time()
        src.batch = [late]
        assert _wait(lambda: eng.metrics.alerts >= 501, 10)
        assert time.time() - t0 < 3.0
    finally:
        eng.stop()
