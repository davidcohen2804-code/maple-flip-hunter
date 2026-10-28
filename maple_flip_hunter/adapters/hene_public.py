"""Watch HeneFM's public deal page and read exact prices from the linked public item page.

No private API, cookies, browser impersonation or block bypass. A page reporting zero fresh deals is
an idle source, not proof that the entire market was scanned. Rounded card prices are never buy prices.
"""
from __future__ import annotations

import re
import time
from datetime import datetime, timezone
from statistics import median

from bs4 import BeautifulSoup

from ..collector.normalize import item_key, listing_key, parse_fm_room
from ..models import Comparable, Listing, MarketEvidence, SourceStatus
from .base import BaseAdapter, FetchError, RateLimited


def exact_price(node):
    for el in node.select('[aria-label], [title]'):
        for attr in ('aria-label', 'title'):
            match = re.fullmatch(r'([\d,]+) mesos (?:per item|each)', el.get(attr, ''))
            if match:
                return int(match[1].replace(',', ''))
    return None


def observed_at(node, now):
    stamp = node.select_one('time[datetime]')
    if stamp:
        try:
            result = datetime.fromisoformat(stamp['datetime'].replace('Z', '+00:00')).timestamp()
            return result if result <= now + 60 else None
        except ValueError:
            return None
    stamp = node.select_one('.updated-cell[title]')
    if stamp:
        current = datetime.fromtimestamp(now, timezone.utc)
        try:
            # This is the source's observation minute, never the page download time.
            candidates = [datetime.strptime(f'{year} {stamp["title"]}', '%Y %b %d, %H:%M UTC')
                          .replace(tzinfo=timezone.utc).timestamp() for year in (current.year, current.year - 1)]
            return max(t for t in candidates if t <= now + 60)
        except (ValueError, TypeError):
            return None
    return None


def location(node):
    text = node.get_text(' ', strip=True)
    ch = re.search(r'\bCH\s+(\d+)\b', text)
    room = re.search(r'\bFM\s+(\d+)\b', text)
    return (int(ch[1]) if ch else None, int(room[1]) if room else None)


class HenePublicAdapter(BaseAdapter):
    name = 'hene_public'
    label = 'HeneFM (public fresh deals)'
    base_url = 'https://henefm.com'
    interval_s = 30.0
    rate = 0.2
    burst = 1
    verify_supported = False
    verify_limit_note = 'Seen on a public page; not independently confirmed in game'

    def __init__(self, *args, max_age_s=900, max_buy_price=10000, min_profit=15000, **kwargs):
        super().__init__(*args, **kwargs)
        self.max_age_s = min(900, max_age_s)
        self.max_buy_price = max_buy_price
        self.min_profit = min_profit
        self._seen = {}
        self._evidence = {}
        self._item_cache = {}
        self._cursor = 0

    async def _html(self, path, params=None):
        await self.bucket.acquire()
        started = time.perf_counter()
        response = await self.client.get(self.base_url + path, params=params)
        self.health.requests += 1
        self.health.last_latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code in (401, 403):
            self._set_status(SourceStatus.AUTH_REQUIRED, f'HTTP {response.status_code}; public page refused')
            raise FetchError(self.health.detail)
        if response.status_code == 429:
            try:
                delay = max(60, float(response.headers.get('Retry-After', '60')))
            except ValueError:
                delay = 300
            self.health.rate_limited_until = time.time() + delay
            raise RateLimited(delay)
        response.raise_for_status()
        return BeautifulSoup(response.text, 'html.parser')

    def stale_after_s(self):
        return self.max_age_s

    def _item(self, soup, item_id, name):
        now = time.time()
        category = {'1': 'Equip', '2': 'Use', '3': 'Setup', '4': 'Etc', '5': 'Cash'}.get(item_id[0])
        key = item_key(name, category, None)
        sales = []
        for row in soup.select('.recent-sales .sales-list li'):
            price, at = exact_price(row), observed_at(row, now)
            seller = row.select_one('a.seller-link')
            if price and at and now - 7 * 86400 <= at <= now:
                ch, room = location(row)
                sales.append(Comparable(unit_price=price, at=at, sold=True,
                                        seller=seller.get_text(strip=True) if seller else None,
                                        channel=ch, fm_room=room, source=self.name))
        listings = []
        for row in soup.select('table tbody tr'):
            cells = row.find_all('td', recursive=False)
            if len(cells) != 4:
                continue
            seller = cells[0].select_one('a.seller-link')
            price, at = exact_price(cells[1]), observed_at(cells[3], now)
            ch, room = location(cells[0])
            stock = cells[2].get_text(strip=True).replace(',', '')
            if not seller or not price or not at or not stock.isdigit() or int(stock) <= 0 or not ch:
                continue
            seller = seller.get_text(strip=True)
            if not seller:
                continue
            listings.append(Listing(
                listing_key=listing_key(self.name, key, seller, ch, price), source=self.name,
                item_name=name, item_key=key, source_item_id=item_id, category=category,
                seller=seller, channel=ch, fm_room=room, map_name=f'FM {room}' if room else None,
                price=price, qty=int(stock), stock=int(stock), source_seen_at=at,
                source_ref_price=int(median([c.unit_price for c in sales])) if sales else None,
                source_ref_basis='median of individually dated public sale observations' if sales else None,
                source_url=self.base_url + '/items/' + item_id))
        return listings, sales

    async def poll(self):
        soup = await self._html('/flips/deals', {'max_age': max(1, self.max_age_s // 60),
                                               'budget': self.max_buy_price, 'min_profit': self.min_profit,
                                               'sort': 'fresh', 'sold': 'hide'})
        cards = soup.select('.deal-list .deal-row')
        if not cards:
            if not soup.select_one('.craft-results-bar'):
                raise FetchError('Public deal page layout unavailable; nothing accepted')
            self.mark_success(None, 0)
            self._set_status(SourceStatus.IDLE, 'Connected; no deals inside the 15-minute age/budget/profit filters')
            return []
        out, fresh_ages = [], []
        start = self._cursor % len(cards)
        batch = (cards[start:] + cards[:start])[:6]
        self._cursor = (start + len(batch)) % len(cards)
        for card in batch:  # bounded requests, one per 5 s; rotate so later cards cannot starve
            anchor = card.select_one('.deal-name a[href]')
            match = re.fullmatch(r'/items/(\d+)(?:\?[^#]*)?', anchor['href']) if anchor else None
            seller = card.select_one('.deal-where a.seller-link')
            if not match or not seller:
                continue
            path = anchor['href']
            cached = self._item_cache.get(path)
            if not cached or time.time() - cached[0] >= 30:
                parsed = self._item(await self._html(path), match[1], anchor.get_text(strip=True))
                self._item_cache[path] = (time.time(), parsed)
            listings, sales = self._item_cache[path][1]
            expected_seller = seller.get_text(strip=True)
            expected_channel, expected_room = location(card.select_one('.deal-where'))
            for listing in listings:
                if (listing.seller != expected_seller or listing.channel != expected_channel
                        or listing.fm_room != expected_room or listing.source_age_s > self.max_age_s):
                    continue
                asks = [Comparable(unit_price=l.price, at=l.source_seen_at, seller=l.seller,
                                   channel=l.channel, fm_room=l.fm_room, source=self.name)
                        for l in listings if l.listing_key != listing.listing_key]
                prices = [c.unit_price for c in sales]
                self._evidence[listing.listing_key] = MarketEvidence(
                    item_key=listing.item_key, item_name=listing.item_name, category=listing.category,
                    comps=sales, active_asks=asks, sold_median=int(median(prices)) if prices else None,
                    sold_count=len(prices), sold_recent_24h=sum(c.at >= time.time() - 86400 for c in sales),
                    source_data_age_s=max(0, time.time() - max(c.at for c in sales)) if sales else None,
                    stats_matched=False if listing.category == 'Equip' else None,
                    notes=['Public deal page coverage only; sale observations inferred by the source; availability unverified'])
                fresh_ages.append(listing.source_age_s)
                if listing.source_seen_at > self._seen.get(listing.listing_key, 0):
                    out.append(listing)
                    self._seen[listing.listing_key] = listing.source_seen_at
        cutoff = time.time() - 86400
        self._seen = {k: t for k, t in self._seen.items() if t >= cutoff}
        self._evidence = {k: ev for k, ev in self._evidence.items() if k in self._seen}
        self._item_cache = {k: v for k, v in self._item_cache.items() if time.time() - v[0] < 300}
        self.mark_success(min(fresh_ages) if fresh_ages else None, len(out))
        if not fresh_ages:
            self._set_status(SourceStatus.IDLE, 'Connected; candidates did not have exact, fresh shop rows')
        else:
            self.health.detail = f'Public deal watch · {len(out)} new observations · partial market coverage'
        return out

    async def fetch_evidence(self, listing, comp_window_s=86400):
        return self._evidence.get(listing.listing_key)
