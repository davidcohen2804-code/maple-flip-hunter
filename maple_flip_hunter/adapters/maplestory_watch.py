"""maplestory.watch adapter (secondary source).

Verified endpoints (2026-10-25):
  GET /api/items                 whole market index (~290 KB JSON, Cache-Control max-age=203)
                                 items[]: name, typical, range[lo,hi], dayAgo, samples, prices, lowest,
                                 listed, sold, units, volume, seen24h, sales24h, seen (unix)
  GET /api/item?name=<name>      listings[]: seller, title, map ("Free Market <10>"), channel, bundle,
                                 price, unit, soldOut, closed, firstSeen, seen   (max-age=300)
The bulk index has no per-listing rows, so we only open item pages whose `lowest`
is far enough under `typical` (candidate discount) and whose `seen` moved since the last poll.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..collector.normalize import item_key, listing_key, parse_fm_room
from ..models import Comparable, Listing, MarketEvidence
from .base import BaseAdapter, FetchError

log = logging.getLogger(__name__)


class MapleStoryWatchAdapter(BaseAdapter):
    name = "msw"
    label = "maplestory.watch"
    base_url = "https://maplestory.watch"
    interval_s = 180.0
    rate = 1.5
    burst = 3

    def __init__(self, *args: Any, max_item_lookups: int = 40, candidate_discount: float = 0.20, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.max_item_lookups = max_item_lookups
        self.candidate_discount = candidate_discount
        self.index: dict[str, dict[str, Any]] = {}     # name -> index row
        self.index_at: float = 0.0
        self._item_seen: dict[str, float] = {}
        self._seen_keys: dict[str, float] = {}
        self._detail_cache: dict[str, tuple[float, Any]] = {}

    @staticmethod
    def _as_int(v: Any) -> Optional[int]:
        try:
            return int(round(float(v))) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _listing_from(self, name: str, row: dict[str, Any], ref: Optional[int]) -> Optional[Listing]:
        unit = self._as_int(row.get("unit") or row.get("price"))
        if not unit or unit <= 0 or row.get("soldOut") or row.get("closed"):
            return None
        seller = row.get("seller") or "?"
        ch = self._as_int(row.get("channel"))
        ik = item_key(name, None, None)  # msw has no category/stat data
        return Listing(
            listing_key=listing_key(self.name, ik, seller, ch, unit),
            source=self.name,
            item_name=name,
            item_key=ik,
            source_item_id=name,
            category=None,
            seller=seller,
            shop_title=row.get("title"),
            channel=ch,
            fm_room=parse_fm_room(row.get("map")),
            map_name=row.get("map"),
            price=unit,
            qty=self._as_int(row.get("bundle")) or 1,
            source_seen_at=float(row.get("seen") or time.time()),
            source_first_seen_at=float(row["firstSeen"]) if row.get("firstSeen") else None,
            source_ref_price=ref,
            source_ref_basis="sold-median-14d",
            source_url=f"{self.base_url}/market",
        )

    async def refresh_index(self) -> int:
        data = await self.fetch_json("/api/items")
        items = (data or {}).get("items") or []
        self.index = {it["name"]: it for it in items if it.get("name")}
        self.index_at = float((data or {}).get("at") or time.time())
        return len(self.index)

    async def poll(self) -> list[Listing]:
        now = time.time()
        await self.refresh_index()
        newest = self.index_at
        candidates = []
        for name, it in self.index.items():
            typ = self._as_int(it.get("typical"))
            low = self._as_int(it.get("lowest"))
            seen = float(it.get("seen") or 0)
            if not typ or not low or low <= 0:
                continue
            if low <= typ * (1 - self.candidate_discount) and seen > self._item_seen.get(name, 0):
                candidates.append((seen, name))
        candidates.sort(reverse=True)
        out: list[Listing] = []
        for seen, name in candidates[: self.max_item_lookups]:
            try:
                data = await self.fetch_json("/api/item", {"name": name})
            except FetchError as exc:
                log.debug("msw item %s failed: %s", name, exc)
                continue
            self._item_seen[name] = seen
            ref = self._as_int(self.index[name].get("typical"))
            for row in (data or {}).get("listings") or []:
                lst = self._listing_from(name, row, ref)
                if not lst or lst.listing_key in self._seen_keys:
                    continue
                if lst.price > (ref or 0) * (1 - self.candidate_discount):
                    continue  # not a candidate row; the pricing engine only needs cheap rows from this source
                self._seen_keys[lst.listing_key] = now
                newest = max(newest, lst.source_seen_at)
                out.append(lst)
        if len(self._seen_keys) > 50_000:
            cutoff = now - 6 * 3600
            self._seen_keys = {k: t for k, t in self._seen_keys.items() if t > cutoff}
        self.mark_success(max(0.0, now - newest), len(out))
        return out

    async def fetch_evidence(self, listing: Listing, comp_window_s: int = 24 * 3600) -> Optional[MarketEvidence]:
        name = listing.item_name
        idx = self.index.get(name)
        if idx is None:
            # case-insensitive fallback
            for k, v in self.index.items():
                if k.lower() == name.lower():
                    idx, name = v, k
                    break
        if idx is None:
            return None
        hit = self._detail_cache.get(name)
        if hit and time.time() - hit[0] < 300:
            data = hit[1]
        else:
            try:
                data = await self.fetch_json("/api/item", {"name": name})
            except FetchError:
                data = None
            self._detail_cache[name] = (time.time(), data)
        rng = idx.get("range") or [None, None]
        ev = MarketEvidence(
            item_key=listing.item_key, item_name=name, category=listing.category,
            sold_median=self._as_int(idx.get("typical")), sold_count=int(idx.get("samples") or 0),
            sold_recent_24h=int(idx.get("sales24h") or 0),
            ask_low=self._as_int(idx.get("lowest")), ask_count=int(idx.get("listed") or 0),
            ask_p25=self._as_int(rng[0]) if rng else None,
            source_data_age_s=max(0.0, time.time() - self.index_at),
        )
        cutoff = time.time() - comp_window_s
        for row in (data or {}).get("listings") or []:
            p = self._as_int(row.get("unit") or row.get("price"))
            at = float(row.get("seen") or 0)
            if not p:
                continue
            c = Comparable(unit_price=p, at=at, sold=bool(row.get("soldOut")), seller=row.get("seller"),
                           channel=self._as_int(row.get("channel")), fm_room=parse_fm_room(row.get("map")),
                           source=self.name)
            ev.comps.append(c)
            same = (c.seller or "").lower() == listing.seller.lower() and c.unit_price == listing.price
            if not c.sold and not row.get("closed") and at >= cutoff and not same:
                ev.active_asks.append(c)
        return ev
