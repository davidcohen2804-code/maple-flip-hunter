"""Load / coverage tests for the collectors: no shop, deal row or candidate may be lost because of
per-cycle limits, pagination or watermarks (ChatGPT review items 1 and 'items per cycle')."""
from __future__ import annotations

import asyncio
import time

import httpx

from maple_flip_hunter.adapters import CozyAdapter, MapleStoryWatchAdapter


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


class FakeCozy:
    """Serves N shops updated at distinct times, each with one unique listing."""

    def __init__(self, n_shops: int, base: float | None = None):
        self.base = base or time.time()
        self.shops = [{"shop": f"shop{i:03d}", "seller": f"shop{i:03d}", "title": "t", "channel": 1 + i % 40,
                       "fm_room": 1 + i % 20, "map_name": "Free Market", "last_at": self.base - i}
                      for i in range(n_shops)]
        self.listing_calls: list[str] = []
        self.page_calls: list[int] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        p = request.url.path
        q = dict(request.url.params)
        if p == "/api/shops":
            page, size = int(q["page"]), int(q["size"])
            rows = sorted(self.shops, key=lambda s: -s["last_at"])[(page - 1) * size: page * size]
            self.page_calls.append(page)
            return httpx.Response(200, json={"rows": rows, "pages": (len(self.shops) + size - 1) // size})
        if p.startswith("/api/shops/"):
            shop = p.split("/")[3]
            self.listing_calls.append(shop)
            s = next(x for x in self.shops if x["shop"] == shop)
            return httpx.Response(200, json={"rows": [{"id": 98, "name": "Earring STR Scroll: Intermediate",
                                                       "at": s["last_at"], "qty": 1, "stock": 1, "channel": s["channel"],
                                                       "fm_room": s["fm_room"], "map_name": "Free Market <1>",
                                                       "price": 10_000 + int(shop[-3:]), "unit_price": 10_000 + int(shop[-3:]),
                                                       "sold": False, "fm": 89_999, "stats": None}], "pages": 1})
        if p == "/api/deals":
            return httpx.Response(200, json={"rows": [], "pages": 1})
        if p == "/api/items":
            return httpx.Response(200, json={"rows": [], "pages": 1})
        return httpx.Response(404)


def _adapter(fake: FakeCozy, max_shops: int) -> CozyAdapter:
    a = CozyAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler), base_url="https://x"), token="t",
                    max_shops_per_cycle=max_shops)
    a.bucket.rate = 10_000
    a._catalog_at = time.time()
    return a


def test_more_changed_shops_than_one_cycle_allows_are_all_fetched():
    fake = FakeCozy(60)
    a = _adapter(fake, max_shops=25)
    a.drain_budget_s = lambda: 0.0
    got = []
    for _ in range(3):
        got += run(a.poll())
    assert len(set(fake.listing_calls)) == 60                 # every shop fetched exactly once
    assert len(fake.listing_calls) == 60
    assert {l.seller for l in got} == {s["shop"] for s in fake.shops}
    assert a.backlog == 0
    # a fourth cycle with no changes fetches nothing
    assert run(a.poll()) == [] and len(fake.listing_calls) == 60


def test_shop_discovery_paginates_when_a_whole_page_is_new():
    fake = FakeCozy(150)
    a = _adapter(fake, max_shops=200)
    got = run(a.poll())
    assert max(fake.page_calls) >= 2
    assert len(got) == 150


def test_backlog_is_reported_and_newest_shops_go_first():
    fake = FakeCozy(40)
    a = _adapter(fake, max_shops=10)
    a.drain_budget_s = lambda: 0.0        # strict per-cycle cap (no time budget) to test the queue
    run(a.poll())
    assert a.backlog == 30
    assert fake.listing_calls == [f"shop{i:03d}" for i in range(10)]   # newest first
    assert "queue" in a.health.detail


def test_shop_updated_again_while_queued_is_not_lost():
    fake = FakeCozy(30)
    a = _adapter(fake, max_shops=5)
    a.drain_budget_s = lambda: 0.0
    run(a.poll())                       # shop000..004 fetched, 25 queued
    fake.shops[29]["last_at"] = fake.base + 10   # the oldest queued shop gets a fresh scan
    run(a.poll())                       # it must now be first in line
    assert fake.listing_calls[5] == "shop029"


def test_failing_shop_is_retried_then_dropped():
    fake = FakeCozy(3)
    bad = "shop001"
    orig = fake.handler

    def handler(req):
        if req.url.path.endswith(f"/{bad}/listings"):
            return httpx.Response(500)
        return orig(req)
    a = CozyAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), token="t")
    a.bucket.rate = 10_000
    a.retry_base_delay = 0.001
    a._catalog_at = time.time()
    for _ in range(4):
        run(a.poll())
    assert bad not in a._pending and a.backlog == 0
    assert set(fake.listing_calls) == {"shop000", "shop002"}


def test_deals_feed_paginates_until_watermark():
    pages = {1: [{"id": i, "name": f"Item {i}", "fm": 50_000, "shop": f"s{i}", "channel": 1, "fm_room": 1,
                  "map_name": "Free Market <1>", "at": 2_000_000 - i, "price": 10_000, "cat": "Use"} for i in range(100)],
             2: [{"id": i, "name": f"Item {i}", "fm": 50_000, "shop": f"s{i}", "channel": 1, "fm_room": 1,
                  "map_name": "Free Market <1>", "at": 2_000_000 - i, "price": 10_000, "cat": "Use"} for i in range(100, 130)]}

    def handler(req):
        p = req.url.path
        if p == "/api/deals":
            page = int(dict(req.url.params)["page"])
            return httpx.Response(200, json={"rows": pages.get(page, []), "pages": 2})
        if p == "/api/shops" or p == "/api/items":
            return httpx.Response(200, json={"rows": [], "pages": 1})
        return httpx.Response(404)
    a = CozyAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), token="t")
    a.bucket.rate = 10_000
    a._catalog_at = time.time()
    a._deals_watermark = 0
    got = run(a.poll())
    assert len(got) == 130


def test_msw_unfetched_candidates_remain_candidates():
    items = [{"name": f"Item {i}", "typical": 100_000, "lowest": 50_000, "samples": 10, "seen": 1_000_000 + i}
             for i in range(50)]
    fetched = []

    def handler(req):
        if req.url.path == "/api/items":
            return httpx.Response(200, json={"at": 1_000_000, "items": items})
        if req.url.path == "/api/item":
            name = dict(req.url.params)["name"]
            fetched.append(name)
            return httpx.Response(200, json={"name": name, "listings": [
                {"seller": "x", "map": "Free Market <1>", "channel": 1, "bundle": 1, "price": 50_000, "unit": 50_000,
                 "soldOut": False, "closed": False, "firstSeen": 1_000_000, "seen": 1_000_000}]})
        return httpx.Response(404)
    a = MapleStoryWatchAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"),
                               max_item_lookups=20, permission_granted=True)
    a.bucket.rate = 10_000
    got = run(a.poll()) ; assert a.backlog == 30 and len(got) == 20
    got += run(a.poll()); got += run(a.poll())
    assert a.backlog == 0 and len(set(fetched)) == 50 and len(got) == 50


def test_extreme_burst_1200_changed_shops_nothing_lost():
    """12 pages of changed shops in one cycle (far above the ~360/hour observed) with a small per-cycle cap."""
    fake = FakeCozy(1200)
    a = _adapter(fake, max_shops=25)
    a.drain_budget_s = lambda: 0.0         # strict cap -> many cycles needed
    a._shops_watermark = 0                 # the fake spreads scans over 20 min; ignore the 5-min start-up window
    got = []
    for _ in range(80):
        got += run(a.poll())
        if a.backlog == 0 and len(got) >= 1200:
            break
    assert max(fake.page_calls) == 12
    assert len(set(fake.listing_calls)) == 1200 and len(fake.listing_calls) == 1200
    assert {l.seller for l in got} == {s["shop"] for s in fake.shops}
    assert a.backlog == 0


def test_safety_valve_keeps_watermark_so_next_cycle_resumes():
    fake = FakeCozy(400)
    a = _adapter(fake, max_shops=1000)
    a._shops_watermark = 0
    a.MAX_SHOP_PAGES = 2                   # pretend the valve is tiny
    got = run(a.poll())                    # only 200 newest shops discovered this cycle
    assert len(got) == 200 and a.backlog == 0 and a._resume_page == 3
    for _ in range(6):                     # watermark was NOT advanced -> the walk resumes until complete
        got += run(a.poll())
        if a._resume_page is None:
            break
    assert len(got) == 400 and a._resume_page is None
    assert {l.seller for l in got} == {s["shop"] for s in fake.shops}


def test_time_budget_drains_more_than_the_minimum_when_backlogged():
    fake = FakeCozy(100)
    a = _adapter(fake, max_shops=5)
    a.interval_s = 10                      # 8 s budget: with an in-memory transport all 100 fit in one cycle
    got = run(a.poll())
    assert len(got) == 100 and a.backlog == 0


def test_shop_with_many_rows_is_read_across_all_pages():
    base = time.time()
    n_rows = 250

    def handler(req):
        p = req.url.path
        q = dict(req.url.params)
        if p == "/api/shops":
            return httpx.Response(200, json={"rows": [{"shop": "Big", "seller": "Big", "title": "t", "channel": 1,
                                                       "fm_room": 1, "map_name": "Free Market", "last_at": base}], "pages": 1})
        if p == "/api/shops/Big/listings":
            page, size = int(q["page"]), int(q["size"])
            rows = [{"id": 98, "name": f"Item {i}", "at": base - i * 0.01, "qty": 1, "stock": 1, "channel": 1, "fm_room": 1,
                     "map_name": "Free Market <1>", "price": 1000 + i, "unit_price": 1000 + i, "sold": False, "fm": 50_000,
                     "stats": None} for i in range(n_rows)][(page - 1) * size: page * size]
            return httpx.Response(200, json={"rows": rows, "pages": (n_rows + size - 1) // size})
        if p in ("/api/deals", "/api/items"):
            return httpx.Response(200, json={"rows": [], "pages": 1})
        return httpx.Response(404)
    a = CozyAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), token="t")
    a.bucket.rate = 10_000
    a._catalog_at = time.time()
    got = run(a.poll())
    assert len(got) == n_rows                                  # 3 pages, every row ingested


def test_deals_safety_valve_keeps_watermark():
    rows_all = [{"id": i, "name": f"Item {i}", "fm": 50_000, "shop": f"s{i}", "channel": 1, "fm_room": 1,
                 "map_name": "Free Market <1>", "at": 2_000_000 - i, "price": 10_000, "cat": "Use"} for i in range(300)]

    def handler(req):
        p = req.url.path
        if p == "/api/deals":
            page = int(dict(req.url.params)["page"])
            return httpx.Response(200, json={"rows": rows_all[(page - 1) * 100: page * 100], "pages": 3})
        if p in ("/api/shops", "/api/items"):
            return httpx.Response(200, json={"rows": [], "pages": 1})
        return httpx.Response(404)
    a = CozyAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"), token="t")
    a.bucket.rate = 10_000
    a._catalog_at = time.time()
    a._deals_watermark = 0
    a.MAX_DEAL_PAGES = 2
    got = run(a.poll())
    assert len(got) == 200 and a._deals_watermark == 0        # valve hit -> watermark untouched
    a.MAX_DEAL_PAGES = 50
    got += run(a.poll())
    assert len(got) == 300                                     # the remaining 100 arrive next cycle, no duplicates
