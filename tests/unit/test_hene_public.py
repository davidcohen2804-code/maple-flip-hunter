import asyncio
import time
from datetime import datetime, timezone

import httpx
import pytest

from maple_flip_hunter.adapters.base import FetchError
from maple_flip_hunter.adapters.hene_public import HenePublicAdapter
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import SourceStatus


CARD = '''<div class="craft-results-bar">1 deal</div><ul class="deal-list"><li class="deal-row">
<span class="deal-name"><a href="/items/2040301">Earring STR Scroll: Intermediate</a></span>
<div class="deal-where"><a class="seller-link">ShopX</a> CH 7 · FM 3</div></li></ul>'''


def item(age=120, exact=True, stock=1, seller='ShopX'):
    stamp = datetime.fromtimestamp(time.time() - age, timezone.utc).isoformat()
    price = '<span aria-label="8,000 mesos per item">8K</span>' if exact else '<strong>8K</strong>'
    sales = ''.join(f'<li><a class="seller-link">seller{i}</a><time datetime="{stamp}"></time>'
                    '<strong title="40,000 mesos each">40K</strong></li>' for i in range(8))
    return f'''<section class="recent-sales"><ul class="sales-list">{sales}</ul></section><table><tbody>
    <tr><td><a class="seller-link">{seller}</a> CH 7 · FM 3</td><td>{price}</td><td>{stock}</td>
    <td><time datetime="{stamp}"></time></td></tr>
    <tr><td><a class="seller-link">Other</a> CH 8 · FM 4</td><td><span aria-label="45,000 mesos per item">45K</span></td>
    <td>1</td><td><time datetime="{stamp}"></time></td></tr></tbody></table>'''


def adapter(page=None, feed=CARD, status=200):
    seen = []
    def response(request):
        seen.append(request)
        return httpx.Response(status, text=feed if request.url.path == '/flips/deals' else (page or item()))
    a = HenePublicAdapter(client=httpx.AsyncClient(transport=httpx.MockTransport(response)), max_buy_price=100000,
                          min_profit=5000)
    a.bucket.rate = 100000
    return a, seen


def test_feed_exact_shop_price_sales_profit_and_single_alert():
    async def check():
        a, requests = adapter()
        db = Database(':memory:')
        eng = MarketEngine(Settings(min_buy_price=0, max_buy_price=100000, min_net_profit=5000), db, adapters=[a])
        alerts = []
        eng.on('alert', lambda *args: alerts.append(args))
        try:
            rows = await a.poll()
            assert len(rows) == 1 and rows[0].price == 8000 and rows[0].location == 'CH 7 · FM 3'
            assert requests[0].url.params['max_age'] == '15'
            ev = await a.fetch_evidence(rows[0])
            assert ev.sold_median == 40000 and ev.sold_count == 8
            assert all(c.seller != 'ShopX' for c in ev.active_asks)
            await eng.ingest(rows)
            assert len(alerts) == 1 and alerts[0][0].valuation.net_profit > 25000
            assert await a.poll() == []
            # A restart / initial page containing the same row must not generate a second alert.
            a._seen.clear()
            await eng.ingest(await a.poll())
            assert len(alerts) == 1
        finally:
            await a.client.aclose()
            db.close()
    asyncio.run(check())


@pytest.mark.parametrize('kwargs', [{'age': 1200}, {'exact': False}, {'stock': 0}, {'seller': 'Different'}])
def test_stale_rounded_sold_or_wrong_shop_never_alerts(kwargs):
    async def check():
        a, _ = adapter(item(**kwargs))
        try:
            assert await a.poll() == []
            assert a.health.status == SourceStatus.IDLE
        finally:
            await a.client.aclose()
    asyncio.run(check())


def test_connected_empty_feed_is_waiting_not_error():
    async def check():
        a, _ = adapter(feed='<div class="craft-results-bar">0 deals</div>')
        db = Database(':memory:')
        eng = MarketEngine(Settings(), db, adapters=[a])
        try:
            assert await a.poll() == []
            eng.running = True
            eng._refresh_state()
            assert eng.scanner_state == 'WAITING'
            assert a.health.requests == 1
        finally:
            eng.running = False
            await a.client.aclose()
            db.close()
    asyncio.run(check())


def test_more_than_six_candidates_are_rotated_without_starvation():
    async def check():
        feed = '<div class="craft-results-bar">7 deals</div><ul class="deal-list">' + ''.join(
            f'<li class="deal-row"><span class="deal-name"><a href="/items/{2040301+i}">Scroll {i}</a></span>'
            '<div class="deal-where"><a class="seller-link">ShopX</a> CH 7 · FM 3</div></li>' for i in range(7)) + '</ul>'
        a, _ = adapter(feed=feed)
        try:
            first = await a.poll()
            second = await a.poll()
            assert len(first) == 6 and len(second) == 1
            assert len({r.listing_key for r in first + second}) == 7
        finally:
            await a.client.aclose()
    asyncio.run(check())


@pytest.mark.parametrize('status', [200, 403])
def test_changed_layout_or_access_refusal_fails_closed(status):
    async def check():
        a, _ = adapter(feed='<h1>Unavailable</h1>', status=status)
        try:
            with pytest.raises(FetchError):
                await a.poll()
        finally:
            await a.client.aclose()
    asyncio.run(check())
