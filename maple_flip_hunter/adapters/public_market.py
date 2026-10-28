"""Read the public HTML market table, without a login, API key or browser impersonation.

Coverage is limited to rows the site renders on /fm. Relative ages are rounded conservatively;
fetching an old page never makes its listings new. Unknown layouts fail closed.
"""
from __future__ import annotations

import re
import time
from html.parser import HTMLParser
from typing import Optional

from ..collector.normalize import item_key, listing_key, parse_fm_room
from ..models import Listing, MarketEvidence, SourceStatus
from .base import BaseAdapter, FetchError, RateLimited


class MarketTable(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.rows = []
        self.row = None
        self.cell = None
        self.anchor = None

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == "tr":
            self.row = []
        elif tag in ("td", "th") and self.row is not None:
            self.cell = {"text": [], "name": [], "href": ""}
        elif tag == "a" and self.cell is not None and re.match(r"^/item/\d+", attrs.get("href", "")):
            self.anchor = True
            self.cell["href"] = attrs["href"]

    def handle_data(self, data):
        if self.cell is not None:
            self.cell["text"].append(data)
            if self.anchor:
                self.cell["name"].append(data)

    def handle_endtag(self, tag):
        if tag == "a":
            self.anchor = None
        elif tag in ("td", "th") and self.cell is not None:
            for key in ("text", "name"):
                self.cell[key] = " ".join(" ".join(self.cell[key]).split())
            self.row.append(self.cell)
            self.cell = None
        elif tag == "tr" and self.row is not None:
            if self.row:
                self.rows.append(self.row)
            self.row = None


def integer(text: str) -> Optional[int]:
    match = re.fullmatch(r"\s*([\d,]+)(?:\s*[▼▲])?\s*", text)
    return int(match[1].replace(",", "")) if match else None


def observation_age(text: str) -> Optional[float]:
    text = text.strip().lower()
    if text in ("just now", "now"):
        return 60.0
    match = re.fullmatch(r"(\d+)\s*(s|sec|secs|seconds?|m|min|mins|minutes?|h|hr|hrs|hours?|d|days?)\s+ago", text)
    if not match:
        return None
    unit = match[2][0]
    # "1 h ago" is between one and two hours old; use the older bound.
    return (int(match[1]) + 1) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[unit]


class PublicMarketAdapter(BaseAdapter):
    name = "fmc_public"
    label = "Free Market Classic (public page)"
    base_url = "https://freemarketclassic.com"
    interval_s = 60.0
    rate = 1 / 30
    burst = 1
    verify_supported = False
    verify_limit_note = "Public table snapshot only; no independent availability check"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._evidence = {}
        self._seen = {}
        self._row_count = 0

    def online_detail(self):
        return f"public HTML · {self._row_count} visible rows · partial market coverage"

    async def poll(self):
        await self.bucket.acquire()
        started = time.perf_counter()
        response = await self.client.get(self.base_url + "/fm", params={"hl": "en"})
        self.health.requests += 1
        self.health.last_latency_ms = (time.perf_counter() - started) * 1000
        if response.status_code in (401, 403):
            self._set_status(SourceStatus.AUTH_REQUIRED, f"HTTP {response.status_code}; public access refused")
            raise FetchError(self.health.detail)
        if response.status_code == 429:
            try:
                delay = max(60.0, float(response.headers.get("Retry-After", "60")))
            except ValueError:
                delay = 300.0
            self.health.rate_limited_until = time.time() + delay
            raise RateLimited(delay)
        response.raise_for_status()
        parser = MarketTable()
        parser.feed(response.text)
        expected = ["Item", "Listed price", "Cheapest", "Typical price", "Sold price", "Seller",
                    "Channel", "Where", "Times seen", "Sold", "Last seen"]
        header = next((i for i, row in enumerate(parser.rows) if [c["text"] for c in row] == expected), None)
        if header is None:
            raise FetchError("Public market table layout unavailable; no listings accepted")
        now = time.time()
        listings, evidence, ages, observed_ages = [], {}, [], {}
        for row in parser.rows[header + 1:]:
            if len(row) != len(expected):
                continue
            cells = [c["text"] for c in row]
            match = re.match(r"^/item/(\d+)", row[0]["href"])
            name, price, age = row[0]["name"], integer(cells[1]), observation_age(cells[10])
            seller = cells[5].strip()
            if not match or not name or not price or age is None or not seller or seller in ("—", "?", "-"):
                continue
            item_id = match[1]
            category = {"1": "Equip", "2": "Use", "3": "Setup", "4": "Etc", "5": "Cash"}.get(item_id[0])
            key = item_key(name, category, None)
            channel = integer(cells[6])
            sold_price, sold_count = integer(cells[4]), integer(cells[9]) or 0
            typical = integer(cells[3])
            listing = Listing(listing_key=listing_key(self.name, key, seller, channel, price), source=self.name,
                              item_name=name, item_key=key, source_item_id=item_id, category=category,
                              seller=seller, channel=channel, fm_room=parse_fm_room(cells[7]), map_name=cells[7],
                              price=price, source_seen_at=now - age, source_ref_price=sold_price or typical,
                              source_ref_basis="public table, 14-day sales" if sold_price else "public asking reference",
                              source_url=self.base_url + row[0]["href"])
            # The page has no date for its aggregate, nor a 24-hour sale count. Never invent either.
            evidence[listing.listing_key] = MarketEvidence(
                item_key=key, item_name=name, category=category,
                sold_median=sold_price if sold_count else None, sold_count=sold_count if sold_price else 0,
                secondary_reference=typical, secondary_label="public typical price (basis unverified)",
                stats_matched=False if category == "Equip" else None,
                notes=["Public HTML table; partial market coverage; aggregate age and 24-hour sales unknown"])
            ages.append(age)
            observed_ages[listing.listing_key] = age
            if listing.listing_key not in self._seen or age < self._seen[listing.listing_key]:
                listings.append(listing)
        self._evidence = evidence
        self._seen = observed_ages
        self._row_count = len(evidence)
        self.mark_success(min(ages) if ages else None, len(listings))
        if not ages:
            self._set_status(SourceStatus.IDLE, "Public table available; no usable dated shop rows")
        elif self.health.status == SourceStatus.STALE:
            self.health.detail += " · " + self.online_detail()
        return listings

    async def fetch_evidence(self, listing, comp_window_s=86400):
        return self._evidence.get(listing.listing_key)
