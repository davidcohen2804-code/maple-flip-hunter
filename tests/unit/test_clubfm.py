"""Club FM adapter: decoding of the columnar snapshot and the permission gate (adapter never polls without it)."""
from __future__ import annotations

import asyncio

import time

import httpx

from maple_flip_hunter.adapters.clubfm import ClubFMAdapter, decode_snapshot
from maple_flip_hunter.models import SourceStatus

T_MS = 1_792_951_340_000
SNAP = {
    "v": 2, "at": T_MS + 326_000, "T": T_MS, "n": 3,
    "cols": {"i": [0, 0, 1], "s": [0, 1, 2], "p": [159999, 149999, 999], "q": [1, 1, 1], "u": [159999, 149999, 999],
             "r": [0, 0, 1], "c": [19, 19, 0], "t": [363, 382, 218], "st": [0, 1, 1], "sold": [0, 1, 0], "chat": [0, 0, 0]},
    "words": {"i": ["Frameless Glasses", "Spear Attack Scroll: Greater"], "s": ["Purpo", "Full", "Tsubomi"],
              "r": ["FM2", "FM"], "st": ["old", "up"]},
    "kinds": {"i": "w", "s": "w", "p": "v", "q": "v", "u": "v", "r": "w", "c": "v", "t": "t", "st": "w", "sold": "v", "chat": "v"},
    "D": {"active": [{"@": 0}, {"@": 2}], "sales": [{"@": 1}], "latest": T_MS},
}


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _adapter(**kw):
    def handler(req):
        if req.url.path == "/data/snap":
            return httpx.Response(200, json=SNAP)
        return httpx.Response(404)
    a = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), **kw)
    a.bucket.rate = 1000
    return a


def test_decode_snapshot_words_and_times():
    rows = decode_snapshot(SNAP)
    assert rows[0]["i"] == "Frameless Glasses" and rows[0]["s"] == "Purpo" and rows[0]["r"] == "FM2"
    assert abs(rows[0]["t"] - (T_MS / 1000 - 363)) < 1e-6
    assert rows[2]["st"] == "up"


def test_without_permission_the_adapter_never_requests_anything():
    calls = []

    def handler(req):
        calls.append(req.url.path)
        return httpx.Response(200, json=SNAP)
    a = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))
    assert a.health.status == SourceStatus.UNSUPPORTED
    assert run(a.poll()) == []
    assert calls == []


def test_with_permission_active_rows_and_sales_are_emitted():
    a = _adapter(permission_granted=True, token="abc")
    assert a.client.headers["Authorization"] == "Bearer abc"
    out = run(a.poll())
    assert {(l.item_name, l.price, l.sold) for l in out} == {("Frameless Glasses", 159999, False),
                                                              ("Frameless Glasses", 149999, True),
                                                              ("Spear Attack Scroll: Greater", 999, False)}
    g = next(l for l in out if l.price == 159999)
    assert g.channel == 19 and g.fm_room == 2 and g.seller == "Purpo"
    assert a.health.status in (SourceStatus.ONLINE, SourceStatus.STALE)
    assert run(a.poll()) == []                       # same snapshot -> nothing re-emitted
    ev = run(a.fetch_evidence(g))
    assert ev.sold_median == 149999 and ev.sold_count == 1
    assert all(not (c.seller == "Purpo" and c.unit_price == 159999) for c in ev.active_asks)


def test_live_feed_used_when_token_works_and_snapshot_when_refused():
    calls = []
    LIVE = dict(SNAP); LIVE = {**SNAP, "D": {}}        # live payload: no index lists, rows flagged "up" are active

    def handler(req):
        calls.append(req.url.path)
        if req.url.path == "/data/live":
            return httpx.Response(200, json=LIVE) if req.headers.get("Authorization") == "Bearer good" else httpx.Response(403)
        if req.url.path == "/data/snap":
            return httpx.Response(200, json=SNAP)
        return httpx.Response(404)
    good = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"),
                         permission_granted=True, token="good")
    good.bucket.rate = 1000
    out = run(good.poll())
    assert good.live_used and calls[-1] == "/data/live"
    assert {l.price for l in out if not l.sold} == {999}          # only the "up" row is an active ask in a live payload
    calls.clear()
    bad = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"),
                        permission_granted=True, token="bad")
    bad.bucket.rate = 1000
    bad.retry_base_delay = 0.001
    out = run(bad.poll())
    assert calls[-1] == "/data/snap" and not bad.live_used and len(out) == 3
    run(bad.poll())
    assert "/data/live" not in calls[calls.index("/data/snap") + 1:]   # refused gate is not retried every cycle


def test_clubfm_evidence_feeds_rows_from_other_sources_and_quick_flips_gate():
    """A maplestory.watch row with thin sales gets Club FM's completed sales; QUICK FLIPS ONLY then judges it."""
    import asyncio

    from maple_flip_hunter.collector.engine import MarketEngine
    from maple_flip_hunter.config import Settings
    from maple_flip_hunter.db import Database
    from maple_flip_hunter.models import Level
    from maple_flip_hunter.notifications import Notifier
    from tests.conftest import make_listing

    T = int(time.time() * 1000)
    n_sold, n_ask = 20, 6
    n = n_sold + n_ask
    prices = [40_000] * n_sold + [41_000 + 1_000 * i for i in range(n_ask)]
    snap = {"v": 2, "at": T, "T": T, "n": n,
            "cols": {"i": [0] * n, "s": list(range(n)), "p": prices, "q": [1] * n, "u": prices,
                     "r": [0] * n, "c": [5] * n, "t": [30] * n, "st": [0] * n_sold + [1] * n_ask,
                     "sold": [1] * n_sold + [0] * n_ask},
            "words": {"i": ["Hat Accuracy Scroll: Intermediate"], "s": [f"s{i}" for i in range(n)], "r": ["FM3"], "st": ["old", "up"]},
            "kinds": {"i": "w", "s": "w", "p": "v", "q": "v", "u": "v", "r": "w", "c": "v", "t": "t", "st": "w", "sold": "v"},
            "D": {"active": [{"@": i} for i in range(n_sold, n)], "sales": [{"@": i} for i in range(n_sold)]}}

    def handler(req):
        return httpx.Response(200, json=snap if req.url.path == "/data/snap" else {})
    cf = ClubFMAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), permission_granted=True)
    cf.bucket.rate = 1000
    run(cf.poll())
    s = Settings(quick_flips_only=True)
    alerts = []
    eng = MarketEngine(s, Database(":memory:"), Notifier(enable_audio=False), adapters=[cf])
    eng.on("alert", lambda o, *_: alerts.append(o))
    lst = make_listing(name="Hat Accuracy Scroll: Intermediate", price=9_000, ref=40_000, source="msw", item_id=None, age_s=5, seller="newbie")
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest([lst]))
    assert opps and opps[0].valuation.reference == 40_000
    assert any("20 completed sales" in e for e in opps[0].valuation.explanation), opps[0].valuation.explanation
    assert opps[0].level in (Level.JACKPOT, Level.GOOD), opps[0].reasons
    assert len(alerts) == 1
