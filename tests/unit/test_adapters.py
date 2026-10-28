"""Adapter parsing against recorded real responses, plus failure / rate-limit behaviour (cases 9, 10)."""
from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest

from maple_flip_hunter.adapters import CozyAdapter, MapleStoryWatchAdapter
from maple_flip_hunter.adapters.base import FetchError, RateLimited
from maple_flip_hunter.models import SourceStatus
from tests.conftest import load_fixture


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _client(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://example.test")


def cozy_handler(request: httpx.Request) -> httpx.Response:
    p = request.url.path
    if p == "/api/shops":
        data = load_fixture("cozy_shops.json")
        data["pages"] = 1  # this fixture contains one page; do not repeat it 8,310 times
        return httpx.Response(200, json=data)
    if p.startswith("/api/shops/") and p.endswith("/listings"):
        return httpx.Response(200, json=load_fixture("cozy_shop_listings.json"))
    if p == "/api/deals":
        data = load_fixture("cozy_deals.json")
        data["pages"] = 1
        return httpx.Response(200, json=data)
    if p == "/api/items":
        return httpx.Response(200, json={"rows": [{"id": 98, "name": "Earring STR Scroll: Intermediate", "cat": "Use", "sub": "Scroll"},
                                                  {"id": 571, "name": "Gephart", "cat": "Equip", "sub": "Dagger"}], "pages": 1})
    if p == "/api/items/98":
        return httpx.Response(200, json=load_fixture("cozy_item_98.json"))
    return httpx.Response(404)


def test_cozy_poll_parses_shops_and_deals():
    a = CozyAdapter(client=_client(cozy_handler), token="t")
    a._shops_watermark = 0
    a._deals_watermark = 0
    listings = run(a.poll())
    assert a.health.status in (SourceStatus.ONLINE, SourceStatus.STALE)
    names = {l.item_name for l in listings}
    assert "Earring STR Scroll: Intermediate" in names and "Gephart" in names
    g = next(l for l in listings if l.item_name == "Gephart")
    assert g.channel == 17 and g.fm_room == 3 and g.price == 23221 and g.stats == {"watk": 57, "slots": 7}
    assert g.item_key == "gephart#watk=57,slots=7"
    sold = [l for l in listings if l.sold]
    assert sold and sold[0].price == 69999
    # second poll with the same data must not re-emit
    assert run(a.poll()) == []


def test_cozy_evidence_filters_competitors_and_sales():
    a = CozyAdapter(client=_client(cozy_handler), token="t")
    from tests.conftest import make_listing

    lst = make_listing(name="Earring STR Scroll: Intermediate", price=70_000, seller="Kahiel", channel=10, item_id="98", ref=89_999)
    lst.source_seen_at = time.time()
    ev = run(a.fetch_evidence(lst, comp_window_s=10 ** 9))
    assert ev.sold_median == 89_999 and ev.sold_count == 307
    assert ev.secondary_reference == 92_999
    assert all(not (c.seller == "Kahiel" and c.unit_price == 70_000) for c in ev.active_asks)
    assert any(c.sold and c.unit_price == 69_999 for c in ev.comps)
    assert lst.icon_url.endswith("/api/icons/2040301.png?v=3")


def test_msw_poll_and_evidence():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/items":
            return httpx.Response(200, json=load_fixture("msw_items.json"))
        if request.url.path == "/api/item":
            return httpx.Response(200, json=load_fixture("msw_item_angel_halo.json"))
        return httpx.Response(404)

    a = MapleStoryWatchAdapter(client=_client(handler), permission_granted=True)
    listings = run(a.poll())
    assert len(a.index) == 3
    assert len(listings) == 1                      # only Angel Halo is >=20% under typical and not sold out
    l = listings[0]
    assert l.item_name == "Angel Halo" and l.price == 120_000 and l.fm_room == 10 and l.channel == 30
    assert abs(l.source_first_seen_at - (1792926255 + (time.time() - 1792936312.0))) < 5
    ev = run(a.fetch_evidence(l))
    assert ev.sold_median == 185_000 and ev.sold_count == 398 and ev.sold_recent_24h == 132
    assert len(ev.active_asks) == 1                # excludes the candidate itself and the sold-out row


def test_9_failed_api_request_marks_source_unavailable_without_crashing():
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(500)

    a = CozyAdapter(client=_client(handler), token="t")
    a.bucket.rate = 1000
    a.retry_base_delay = 0.01
    with pytest.raises(FetchError):
        run(a.fetch_json("/api/shops", retries=2))
    assert calls["n"] == 3
    a.mark_failure(FetchError("boom"))
    assert a.health.status == SourceStatus.UNAVAILABLE and a.health.errors >= 1

    # run() loop must swallow the failure and keep going
    async def go():
        a.interval_s = 0.01
        task = asyncio.ensure_future(a.run(lambda ls: None))
        await asyncio.sleep(0.2)
        a.stop()
        await asyncio.wait_for(task, 2)
    run(go())
    assert a.health.status == SourceStatus.UNAVAILABLE


def test_10_rate_limited_source_backs_off():
    def handler(request):
        return httpx.Response(429, headers={"Retry-After": "7"})

    a = MapleStoryWatchAdapter(client=_client(handler), permission_granted=True)
    with pytest.raises(RateLimited) as e:
        run(a.fetch_json("/api/items"))
    assert e.value.retry_after == 7
    assert a.health.status == SourceStatus.RATE_LIMITED
    assert a.health.rate_limited_until and a.health.rate_limited_until > time.time() + 5
    with pytest.raises(RateLimited):              # no new request while the back-off is active
        run(a.fetch_json("/api/items"))


def test_token_bucket_limits_rate():
    from maple_flip_hunter.adapters.base import TokenBucket

    async def go():
        b = TokenBucket(rate=50, capacity=2)
        t0 = time.perf_counter()
        for _ in range(6):
            await b.acquire()
        return time.perf_counter() - t0
    assert run(go()) >= 0.07     # 4 tokens beyond the burst at 50/s


def test_msw_and_cozy_never_request_without_permission_or_token():
    """maplestory.watch Terms 2026-10-27 / cozy 403: without recorded permission (msw) or a token (cozy) the
    adapters report why and make zero HTTP requests — nothing is retried, nothing is circumvented."""
    import asyncio
    import httpx
    from maple_flip_hunter.adapters import CozyAdapter, MapleStoryWatchAdapter

    calls = []

    def handler(req):
        calls.append(str(req.url))
        return httpx.Response(200, json={"items": [], "shops": []})

    a = MapleStoryWatchAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))
    assert a.health.status.value == "UNSUPPORTED" and "permission" in a.health.detail
    assert asyncio.new_event_loop().run_until_complete(a.poll()) == []
    from tests.conftest import make_listing
    assert asyncio.new_event_loop().run_until_complete(a.verify(make_listing(name="Zard", price=1, ref=10))) is None
    c = CozyAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://x"))
    assert c.health.status.value == "AUTH REQUIRED"
    assert asyncio.new_event_loop().run_until_complete(c.poll()) == []
    assert calls == []
    # with permission recorded but robots.txt still disallowing /api/, msw still does not read the API
    def robots_handler(req):
        calls.append(str(req.url))
        if req.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /api/\n")
        return httpx.Response(200, json={"items": []})
    b = MapleStoryWatchAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(robots_handler), base_url="https://x"), permission_granted=True)
    assert asyncio.new_event_loop().run_until_complete(b.poll()) == [] and b.health.status.value == "UNSUPPORTED"
    assert calls == ["https://x/robots.txt"]
