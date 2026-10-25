"""cozy.markets adapter (primary source).

Verified endpoints (2026-10-25, see docs/SOURCE_AUDIT.md):
  GET /api/shops?page=&size=(<=100)&sort=last_at&dir=desc        shops, newest scan first (age seen: 5-30 s)
  GET /api/shops/{shop}/listings?page=&size=                     every row of a shop (all items, not only deals)
  GET /api/deals?page=&size=&kind=under-fm&seen=1h&sort=at&dir=desc   rows >=20% under the site's reference
  GET /api/items?page=&size=&sort=&dir=&q=&cat=                  item catalog (1,377 items)
  GET /api/items/{id}                                            reference stats + recent sightings (chart)
  GET /api/items/{id}/sightings?page=&size=&since=&stats=watk:56,slots:7   comparables, filterable by stats
  GET /api/overview                                              KPIs
Responses carry `Cache-Control: max-age=15, s-maxage=5` -> polling faster than ~5 s is pointless.
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional
from urllib.parse import quote

from ..collector.normalize import item_key, listing_key, parse_fm_room
from ..models import Comparable, Listing, MarketEvidence
from .base import BaseAdapter, FetchError

log = logging.getLogger(__name__)

STATS_FILTER_KEYS = ("watk", "matk", "slots", "str", "dex", "int", "luk", "wdef", "mdef", "acc", "avoid", "speed", "jump")


class CozyAdapter(BaseAdapter):
    name = "cozy"
    label = "cozy.markets"
    base_url = "https://cozy.markets"
    interval_s = 10.0
    rate = 3.0
    burst = 6
    PAGE = 100

    def __init__(self, *args: Any, max_shops_per_cycle: int = 25, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.max_shops_per_cycle = max_shops_per_cycle
        self._shops_watermark: float = time.time() - 300  # start with the last 5 minutes
        self._shop_seen_at: dict[str, float] = {}
        self._pending: dict[str, dict[str, Any]] = {}
        self.backlog = 0
        self._deals_watermark: float = time.time() - 300
        self._seen_keys: dict[str, float] = {}
        self._catalog: dict[int, dict[str, Any]] = {}
        self._catalog_at: float = 0.0
        self._detail_cache: dict[str, tuple[float, Any]] = {}
        self.catalog_refresh_s = 1800.0
        self.detail_ttl_s = 300.0

    # ------------------------------------------------------------ parsing
    @staticmethod
    def _as_int(v: Any) -> Optional[int]:
        try:
            return int(round(float(v))) if v is not None else None
        except (TypeError, ValueError):
            return None

    def _row_to_listing(self, row: dict[str, Any], shop: str, shop_title: Optional[str],
                        channel: Optional[int], fm_room: Optional[int], map_name: Optional[str]) -> Optional[Listing]:
        price = self._as_int(row.get("unit_price") or row.get("price"))
        if not price or price <= 0:
            return None
        name = row.get("name") or ""
        item_id = row.get("id")
        cat = row.get("cat") or (self._catalog.get(int(item_id), {}).get("cat") if item_id is not None else None)
        stats = row.get("stats") or None
        ik = item_key(name, cat, stats)
        ch = self._as_int(channel if channel is not None else row.get("channel"))
        room = parse_fm_room(map_name or row.get("map_name"), fm_room if fm_room is not None else row.get("fm_room"))
        at = float(row.get("at") or time.time())
        return Listing(
            listing_key=listing_key(self.name, ik, shop, ch, price),
            source=self.name,
            item_name=name,
            item_key=ik,
            source_item_id=str(item_id) if item_id is not None else None,
            category=cat,
            subcategory=(self._catalog.get(int(item_id), {}).get("sub") if item_id is not None else None),
            seller=shop,
            shop_title=shop_title or row.get("shop_title"),
            channel=ch,
            fm_room=room,
            map_name=map_name or row.get("map_name"),
            price=price,
            qty=self._as_int(row.get("qty")) or 1,
            stock=self._as_int(row.get("stock")),
            stats=stats,
            sold=bool(row.get("sold")),
            confirmed=bool(row.get("confirmed", True)),
            source_seen_at=at,
            source_ref_price=self._as_int(row.get("fm")),
            source_ref_basis=row.get("fm_basis"),
            source_url=f"{self.base_url}/items/{item_id}" if item_id is not None else self.base_url,
            icon_url=None,
        )

    # ------------------------------------------------------------- polling
    MAX_SHOP_PAGES = 5      # 500 shops per cycle is far above the observed ~360 shop updates / hour
    MAX_DEAL_PAGES = 5

    async def _discover_changed_shops(self) -> tuple[int, float]:
        """Walk /api/shops (newest scan first) until we reach shops we have already processed.
        Every changed shop goes into the persistent pending queue - nothing is dropped when more
        shops changed than one cycle can fetch. Returns (rows seen, newest last_at)."""
        seen_rows, newest, page = 0, 0.0, 1
        while page <= self.MAX_SHOP_PAGES:
            data = await self.fetch_json("/api/shops", {"page": page, "size": self.PAGE, "sort": "last_at", "dir": "desc"})
            rows = (data or {}).get("rows") or []
            if not rows:
                break
            seen_rows += len(rows)
            reached_known = False
            for s in rows:
                shop = s.get("shop") or s.get("seller")
                last_at = float(s.get("last_at") or 0)
                newest = max(newest, last_at)
                if not shop:
                    continue
                if last_at <= self._shop_seen_at.get(shop, 0.0):
                    reached_known = True
                    continue
                if last_at < self._shops_watermark - 2 and shop not in self._pending:
                    reached_known = True  # older than anything we care about (start-up window)
                    continue
                prev = self._pending.get(shop)
                if prev is None or last_at > prev["last_at"]:
                    self._pending[shop] = {"last_at": last_at, "title": s.get("title"), "channel": s.get("channel"),
                                           "fm_room": s.get("fm_room"), "map_name": s.get("map_name"),
                                           "fails": (prev or {}).get("fails", 0)}
            oldest_on_page = min(float(s.get("last_at") or 0) for s in rows)
            # keep paginating only while the whole page is newer than our watermark (all rows unseen)
            if reached_known or oldest_on_page <= self._shops_watermark or page >= int((data or {}).get("pages") or 1):
                break
            page += 1
        if newest:
            self._shops_watermark = max(self._shops_watermark, newest)
        return seen_rows, newest

    async def _drain_pending(self, new: dict[str, Listing]) -> int:
        """Fetch rows for up to max_shops_per_cycle queued shops (newest first); the rest stay queued."""
        order = sorted(self._pending.items(), key=lambda kv: -kv[1]["last_at"])
        done = 0
        for shop, meta in order[: self.max_shops_per_cycle]:
            prev = self._shop_seen_at.get(shop, 0.0)
            try:
                data = await self.fetch_json(f"/api/shops/{quote(shop, safe='')}/listings",
                                             {"page": 1, "size": self.PAGE})
            except FetchError as exc:
                meta["fails"] = meta.get("fails", 0) + 1
                log.debug("shop %s listings failed: %s (attempt %d)", shop, exc, meta["fails"])
                if meta["fails"] >= 3:
                    self._pending.pop(shop, None)   # give up on a shop that keeps failing (e.g. renamed)
                    self._shop_seen_at[shop] = meta["last_at"]
                    log.warning("cozy: giving up on shop %s after 3 failed listing fetches", shop)
                continue
            self._shop_seen_at[shop] = meta["last_at"]
            self._pending.pop(shop, None)
            done += 1
            for row in (data or {}).get("rows") or []:
                if float(row.get("at") or 0) <= prev - 2:
                    continue
                lst = self._row_to_listing(row, shop, meta.get("title"), meta.get("channel"), meta.get("fm_room"),
                                           row.get("map_name") or meta.get("map_name"))
                if lst:
                    new[lst.listing_key + ("|sold" if lst.sold else "")] = lst
        return done

    async def _poll_deals(self, new: dict[str, Listing]) -> float:
        """Under-reference feed, paginated until rows older than the watermark appear."""
        newest, page, floor = 0.0, 1, self._deals_watermark
        while page <= self.MAX_DEAL_PAGES:
            deals = await self.fetch_json("/api/deals", {"page": page, "size": self.PAGE, "kind": "under-fm",
                                                          "seen": "1h", "sort": "at", "dir": "desc"})
            rows = (deals or {}).get("rows") or []
            if not rows:
                break
            reached_old = False
            for row in rows:
                at = float(row.get("at") or 0)
                newest = max(newest, at)
                if at <= floor - 2:
                    reached_old = True
                    continue
                lst = self._row_to_listing(row, row.get("shop") or "?", row.get("shop_title"), row.get("channel"),
                                           row.get("fm_room"), row.get("map_name"))
                if lst:
                    new.setdefault(lst.listing_key + ("|sold" if lst.sold else ""), lst)
            if reached_old or page >= int((deals or {}).get("pages") or 1):
                break
            page += 1
        if newest:
            self._deals_watermark = max(self._deals_watermark, newest)
        return newest

    async def poll(self) -> list[Listing]:
        """One incremental cycle: queue every changed shop, fetch rows for as many as allowed, and read
        the under-FM deals feed. Shops that did not fit stay queued for the next cycle (never dropped)."""
        if time.time() - self._catalog_at > self.catalog_refresh_s:
            try:
                await self.refresh_catalog()
            except FetchError as exc:
                log.warning("cozy catalog refresh failed: %s", exc)

        new: dict[str, Listing] = {}
        now = time.time()
        _, newest_shop = await self._discover_changed_shops()
        await self._drain_pending(new)
        newest_deal = await self._poll_deals(new)
        newest = max(newest_shop, newest_deal)
        self.backlog = len(self._pending)
        if self.backlog > self.max_shops_per_cycle * 2:
            log.warning("cozy shop backlog %d (max %d per cycle) - consider a shorter interval", self.backlog,
                        self.max_shops_per_cycle)

        # drop keys we already emitted (same row re-scanned at the same price)
        out = []
        for k, lst in new.items():
            if k in self._seen_keys:
                continue
            self._seen_keys[k] = now
            out.append(lst)
        if len(self._seen_keys) > 50_000:
            cutoff = now - 6 * 3600
            self._seen_keys = {k: t for k, t in self._seen_keys.items() if t > cutoff}

        self.health.detail = f"queue {self.backlog}" if self.backlog else ""
        self.mark_success((now - newest) if newest else None, len(out))
        if self.backlog:
            self.health.detail = f"shop queue {self.backlog}"
        return out

    # ------------------------------------------------------------- catalog
    async def refresh_catalog(self) -> int:
        page, total = 1, 0
        while True:
            data = await self.fetch_json("/api/items", {"page": page, "size": self.PAGE})
            rows = (data or {}).get("rows") or []
            for r in rows:
                try:
                    self._catalog[int(r["id"])] = r
                except (KeyError, TypeError, ValueError):
                    continue
            total += len(rows)
            pages = int((data or {}).get("pages") or 1)
            if page >= pages or not rows:
                break
            page += 1
        self._catalog_at = time.time()
        return total

    @property
    def catalog(self) -> dict[int, dict[str, Any]]:
        return self._catalog

    def icon_url(self, icon_key: Optional[str]) -> Optional[str]:
        return f"{self.base_url}/api/icons/{icon_key}.png?v=3" if icon_key else None

    # ------------------------------------------------------------ evidence
    async def _cached(self, key: str, path: str, params: Optional[dict[str, Any]] = None) -> Any:
        hit = self._detail_cache.get(key)
        if hit and time.time() - hit[0] < self.detail_ttl_s:
            return hit[1]
        data = await self.fetch_json(path, params)
        self._detail_cache[key] = (time.time(), data)
        if len(self._detail_cache) > 2000:
            oldest = sorted(self._detail_cache.items(), key=lambda kv: kv[1][0])[:500]
            for k, _ in oldest:
                self._detail_cache.pop(k, None)
        return data

    async def fetch_evidence(self, listing: Listing, comp_window_s: int = 24 * 3600) -> Optional[MarketEvidence]:
        """Item reference stats + comparables. For equipment with known stats, comps are
        filtered server-side to the same stat variant."""
        if not listing.source_item_id:
            return None
        iid = listing.source_item_id
        detail = await self._cached(f"item:{iid}", f"/api/items/{iid}")
        if not detail:
            return None
        st = detail.get("stats") or {}
        info = detail.get("info") or {}
        ev = MarketEvidence(
            item_key=listing.item_key,
            item_name=listing.item_name,
            category=listing.category or info.get("type"),
            sold_median=self._as_int(st.get("sold_median")),
            sold_count=int(st.get("sold") or 0),
            ask_median=self._as_int(st.get("median")),
            ask_p25=self._as_int(st.get("p25")),
            ask_low=self._as_int(st.get("low")),
            ask_count=int(st.get("count") or 0),
            secondary_reference=self._as_int(detail.get("meow")),
            secondary_label="meowdb reference" if detail.get("meow") else None,
            source_data_age_s=max(0.0, time.time() - float(detail.get("fm_at") or time.time())),
        )
        if listing.icon_url is None and info.get("iconKey"):
            listing.icon_url = self.icon_url(str(info["iconKey"]))

        is_equip = (ev.category or "").lower() == "equip"
        comps_rows: list[dict[str, Any]] = []
        if is_equip and listing.stats:
            stats_q = ",".join(f"{k}:{int(listing.stats[k])}" for k in STATS_FILTER_KEYS
                               if listing.stats.get(k) is not None)
            try:
                sg = await self._cached(f"sight:{iid}:{stats_q}", f"/api/items/{iid}/sightings",
                                        {"page": 1, "size": self.PAGE, "stats": stats_q,
                                         "since": int(time.time() - 7 * 24 * 3600)})
                comps_rows = (sg or {}).get("rows") or []
                ev.stats_matched = True
                ev.notes.append(f"comparables filtered to same stats ({stats_q})")
                # the item-level sold median mixes all stat variants -> do not trust it for equipment
                same_sold = [self._as_int(r.get("unit_price")) for r in comps_rows if r.get("sold")]
                same_sold = [p for p in same_sold if p]
                if same_sold:
                    same_sold.sort()
                    ev.sold_median = same_sold[len(same_sold) // 2]
                    ev.sold_count = len(same_sold)
                else:
                    ev.sold_median = None
                    ev.sold_count = 0
                ev.notes.append("equipment: item-wide sold median replaced by same-variant sales")
            except FetchError as exc:
                ev.notes.append(f"stats-filtered comps unavailable: {exc}")
                comps_rows = detail.get("chart") or []
        elif is_equip:
            ev.stats_matched = False
            ev.notes.append("equipment stats unknown for this listing - variants cannot be compared")
            comps_rows = detail.get("chart") or []
        else:
            comps_rows = detail.get("chart") or []

        cutoff = time.time() - comp_window_s
        for r in comps_rows:
            p = self._as_int(r.get("unit_price") or r.get("price"))
            at = float(r.get("at") or 0)
            if not p:
                continue
            c = Comparable(unit_price=p, at=at, sold=bool(r.get("sold")), seller=r.get("shop"),
                           channel=self._as_int(r.get("channel")),
                           fm_room=parse_fm_room(r.get("map_name") or r.get("map"), r.get("fm_room")),
                           stats=r.get("stats") or None, source=self.name)
            ev.comps.append(c)
            if not c.sold and at >= cutoff and not (
                c.seller and listing.seller and c.seller.lower() == listing.seller.lower()
                and c.unit_price == listing.price
            ):
                ev.active_asks.append(c)
        ev.sold_recent_24h = sum(1 for c in ev.comps if c.sold and c.at >= cutoff)
        return ev
