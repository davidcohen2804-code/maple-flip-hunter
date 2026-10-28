import asyncio
import time

import httpx
import pytest

from maple_flip_hunter.adapters.base import FetchError, RateLimited
from maple_flip_hunter.adapters.public_market import PublicMarketAdapter, observation_age
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import SourceStatus


def table(age="2 min ago", item_id="2040001", name="Hat Accuracy Scroll: Intermediate", seller="ShopX"):
    headers = ["Item", "Listed price", "Cheapest", "Typical price", "Sold price", "Seller",
               "Channel", "Where", "Times seen", "Sold", "Last seen"]
    values = [f'<a href="/item/{item_id}?hl=en">{name}</a><button>🔔</button>', "8,000", "7,000", "42,000", "40,000",
              seller, "7", "FM 3", "120", "50", age]
    return "<table><tr>" + "".join(f"<th>{x}</th>" for x in headers) + "</tr><tr>" + "".join(f"<td>{x}</td>" for x in values) + "</tr></table>"


def adapter(body, status=200):
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(status, text=body)))
    a = PublicMarketAdapter(client=client)
    a.bucket.rate = 10000
    return a


def test_public_table_real_fields_and_deduplication():
    async def check():
        a = adapter(table())
        try:
            listings = await a.poll()
            assert len(listings) == 1
            l = listings[0]
            assert l.item_name == "Hat Accuracy Scroll: Intermediate"
            assert l.price == 8000 and l.seller == "ShopX" and l.location == "CH 7 · FM 3"
            assert 180 <= l.source_age_s < 185
            ev = await a.fetch_evidence(l)
            assert ev.sold_median == 40000 and ev.sold_count == 50
            assert ev.source_data_age_s is None and ev.sold_recent_24h == 0
            assert await a.poll() == []
        finally:
            await a.client.aclose()
    asyncio.run(check())


def test_old_shop_is_ingested_but_never_alerted():
    async def check():
        a = adapter(table(age="2 h ago"))
        eng = MarketEngine(Settings(min_buy_price=0, max_buy_price=100000), Database(":memory:"), adapters=[a])
        alerts = []
        eng.on("alert", lambda *args: alerts.append(args))
        try:
            listings = await a.poll()
            assert a.health.status == SourceStatus.STALE
            await eng.ingest(listings)
            assert listings[0].source_age_s >= 10800
            assert alerts == []
        finally:
            await a.client.aclose()
            eng.db.close()
    asyncio.run(check())


def test_unmatched_equipment_and_unknown_dates_do_not_become_verified():
    async def check():
        a = adapter(table(item_id="1082000", name="Work Gloves"))
        b = adapter(table(age="unknown"))
        try:
            listing = (await a.poll())[0]
            assert listing.category == "Equip"
            assert (await a.fetch_evidence(listing)).stats_matched is False
            assert await b.poll() == []
            assert b.health.status == SourceStatus.IDLE
        finally:
            await a.client.aclose()
            await b.client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize("status,error", [(403, FetchError), (429, RateLimited), (200, FetchError)])
def test_blocked_or_changed_pages_fail_closed(status, error):
    async def check():
        a = adapter("<html>unavailable</html>", status)
        try:
            with pytest.raises(error):
                await a.poll()
            assert not a._evidence
        finally:
            await a.client.aclose()
    asyncio.run(check())


def test_relative_timestamps_use_older_bound():
    assert observation_age("1 h ago") == 7200
    assert observation_age("Expired 2d ago") is None
    assert observation_age("Just now") == 60


def test_old_listing_reobserved_fresh_alerts_once():
    async def check():
        page = [table(age="2 h ago")]
        client = httpx.AsyncClient(transport=httpx.MockTransport(lambda request: httpx.Response(200, text=page[0])))
        a = PublicMarketAdapter(client=client)
        a.bucket.rate = 10000
        eng = MarketEngine(Settings(min_buy_price=0, max_buy_price=100000), Database(":memory:"), adapters=[a])
        alerts = []
        eng.on("alert", lambda *args: alerts.append(args))
        try:
            await eng.ingest(await a.poll())
            assert alerts == []
            page[0] = table(age="just now")
            result = await eng.ingest(await a.poll())
            assert result and len(alerts) == 1 and not result[0].stale
            await eng.ingest(await a.poll())
            assert len(alerts) == 1
        finally:
            await client.aclose()
            eng.db.close()
    asyncio.run(check())
