"""Club FM (maplefm.gg) adapter — DISABLED until written permission is granted.

Club FM's Terms of Use (2026-10-21, §2) require written permission before collecting the price tables or
data files by automated means. This adapter therefore refuses to run unless the user records that
permission in Settings (`source_clubfm_permission_granted = True`, ideally with the token or contact
they received). The site says they "usually say yes" — see docs/ACCESS_REQUESTS.md for the request text.

Observed format (one manual browser read on 2026-10-25, for schema research only):
  GET https://api.maplefm.gg/data/snap            public snapshot, Cache-Control max-age=30
      {v: 2, at: <ms>, T: <ms of newest row>, n: <rows>, cols: {col: [...]}, words: {col: [...]},
       kinds: {col: 'w'|'v'|'t'}, D: {list: [...per item...], sales: [{"@":idx}], active: [{"@":idx}], latest, who}}
      columns: i item, s seller, p price, q qty, u unit price, r room ("FM2"), c channel (0 = unknown),
               t seconds before T, who scanner, st "up"/"old", sold, so (sold out), chat (from smega), xj job...
      kind 'w' = index into words[col]; 'v' = value; 't' = seconds before T.
  GET https://api.maplefm.gg/data/live?after=<ms> incremental feed, gated (401/403 without a session/token)
Measured: newest row 326 s older than the snapshot timestamp; the gated live feed "updates every minute".
"""
from __future__ import annotations

import logging
import time
from typing import Any, Optional

from ..collector.normalize import item_key, listing_key, parse_fm_room
from ..models import Comparable, Listing, MarketEvidence, SourceStatus
from .base import BaseAdapter, FetchError

log = logging.getLogger(__name__)


def decode_snapshot(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """Turn the columnar snapshot into row dicts with absolute unix-second timestamps."""
    cols = snap.get("cols") or {}
    words = snap.get("words") or {}
    kinds = snap.get("kinds") or {}
    t_ref = float(snap.get("T") or snap.get("at") or 0) / 1000.0
    n = int(snap.get("n") or 0)
    rows: list[dict[str, Any]] = []
    for idx in range(n):
        row: dict[str, Any] = {}
        for k, col in cols.items():
            if idx >= len(col):
                continue
            v = col[idx]
            kind = kinds.get(k)
            if kind == "w":
                w = words.get(k) or []
                row[k] = w[v] if isinstance(v, int) and 0 <= v < len(w) else v
            elif kind == "t":
                row[k] = t_ref - float(v or 0)
            else:
                row[k] = v
        rows.append(row)
    return rows


class ClubFMAdapter(BaseAdapter):
    name = "clubfm"
    label = "Club FM (maplefm.gg)"
    base_url = "https://api.maplefm.gg"
    interval_s = 60.0
    rate = 1.0
    burst = 2

    def __init__(self, *args: Any, permission_granted: bool = False, token: str = "", base_url: str = "",
                 live_interval_s: float = 60.0, live_backoff_s: float = 30.0, **kwargs: Any):
        if base_url:
            self.base_url = base_url.rstrip("/")
        super().__init__(*args, **kwargs)
        self.permission_granted = permission_granted
        self.live_interval_s = max(5.0, float(live_interval_s))
        self.live_backoff_s = max(5.0, float(live_backoff_s))
        self._live_failures = 0
        self.token = token.strip()
        if self.token:
            self.set_header("Authorization", f"Bearer {self.token}")
        self._seen_keys: dict[str, float] = {}
        self._last_T: float = 0.0
        self._rows_cache: list[dict[str, Any]] = []
        self._live_blocked_until = 0.0
        self.live_used = False
        if not permission_granted:
            self._set_status(SourceStatus.UNSUPPORTED, "permission from Club FM not recorded (Terms of Use §2)")

    def _row_to_listing(self, r: dict[str, Any]) -> Optional[Listing]:
        price = r.get("u") or r.get("p")
        try:
            price = int(round(float(price)))
        except (TypeError, ValueError):
            return None
        if price <= 0:
            return None
        name = str(r.get("i") or "")
        seller = str(r.get("s") or "?")
        ch = int(r.get("c") or 0) or None
        room = parse_fm_room(str(r.get("r") or ""))
        ik = item_key(name, None, None)       # the snapshot carries no equipment stats
        at = float(r.get("t") or time.time())
        return Listing(
            listing_key=listing_key(self.name, ik, seller, ch, price), source=self.name, item_name=name,
            item_key=ik, source_item_id=name, seller=seller, shop_title=str(r.get("st") or "") or None,
            channel=ch, fm_room=room, map_name=str(r.get("r") or "") or None, price=price,
            qty=int(r.get("q") or 1), sold=bool(r.get("sold")), source_seen_at=at,
            source_url=f"https://maplefm.gg/#item={name}",
        )

    async def _fetch_live(self) -> Optional[dict[str, Any]]:
        """Incremental feed (needs the token Club FM issued). Returns None when not permitted / not available,
        in which case the public snapshot is used. Never retried against a 401/403 within the same cycle."""
        if not self.token or self._live_blocked_until > time.time():
            return None
        params = {"after": int(self._last_T * 1000)} if self._last_T else None
        try:
            data = await self.fetch_json("/data/live", params)
        except FetchError as exc:
            self._live_backoff(f"live feed refused/failed ({exc})")
            return None
        if not isinstance(data, dict) or "cols" not in data:
            self._live_backoff("live feed has an unexpected shape")
            return None
        self._live_failures = 0
        self.live_used = True
        return data

    def _live_backoff(self, why: str) -> None:
        """Exponential back-off for the live feed (their rule: start at 30 s, one connection): 30, 60, 120 … ≤ 600 s.
        The public snapshot keeps the source alive meanwhile."""
        delay = min(600.0, self.live_backoff_s * (2 ** self._live_failures))
        self._live_failures += 1
        self._live_blocked_until = time.time() + delay
        self.live_used = False
        log.warning("clubfm: %s; using the public snapshot, live retry in %.0f s", why, delay)
        self.health.detail = f"{why}; snapshot mode, live retry in {delay:.0f} s"

    def next_delay(self) -> float:
        """Live mode polls faster (their feed updates every minute); snapshot mode respects the 60 s limit."""
        return self.live_interval_s if self.live_used else self.interval_s

    def online_detail(self) -> str:
        return f"{self.mode} mode"

    verify_supported = True
    verify_limit_note = "Club FM snapshot is 5–9 min behind real time; 'ACTIVE' means seen in the latest snapshot"

    async def verify(self, listing: Listing) -> Optional[tuple[str, float, str]]:
        if not self.permission_granted or not self._rows_cache:
            return None
        seller, name = (listing.seller or "").lower(), (listing.item_name or "").lower()
        mine = [r for r in self._rows_cache if str(r.get("s") or "").lower() == seller and str(r.get("i") or "").lower() == name
                and (listing.channel is None or not r.get("c") or int(r.get("c")) == listing.channel)]
        if not mine:
            return None
        same = [r for r in mine if int(round(float(r.get("u") or r.get("p") or 0))) == listing.price]
        if not same:
            r = max(mine, key=lambda x: float(x.get("t") or 0))
            return "REMOVED", float(r.get("t") or time.time()), "seller now lists it at another price"
        r = max(same, key=lambda x: float(x.get("t") or 0))
        at = float(r.get("t") or time.time())
        if r.get("sold"):
            return "SOLD", at, "snapshot marks the slot sold"
        if str(r.get("st") or "") == "old":
            return "REMOVED", at, "snapshot marks the listing as no longer up"
        return "ACTIVE", at, "up in the latest snapshot"

    @property
    def mode(self) -> str:
        return "live" if self.live_used else "snapshot"

    async def poll(self) -> list[Listing]:
        if not self.permission_granted:
            self._set_status(SourceStatus.UNSUPPORTED, "permission from Club FM not recorded (Terms of Use §2)")
            return []
        snap = await self._fetch_live()
        if snap is None:
            snap = await self.fetch_json("/data/snap")
        if not snap:
            raise FetchError("empty snapshot")
        rows = decode_snapshot(snap)
        self._rows_cache = rows
        T = float(snap.get("T") or 0) / 1000.0
        now = time.time()
        out: list[Listing] = []
        D = snap.get("D") or {}
        active = {d.get("@") for d in D.get("active") or [] if isinstance(d, dict)}
        sales = {d.get("@") for d in D.get("sales") or [] if isinstance(d, dict)}
        for idx, r in enumerate(rows):
            is_sale = idx in sales or bool(r.get("sold"))
            if active and idx not in active and not is_sale:
                continue
            if not active and str(r.get("st") or "") != "up" and not is_sale:
                continue   # live payloads without an index list: rows flagged "up" are the active asks
            lst = self._row_to_listing(r)
            if not lst:
                continue
            lst.sold = is_sale
            k = lst.listing_key + ("|sold" if lst.sold else "")
            if k in self._seen_keys:
                continue
            self._seen_keys[k] = now
            out.append(lst)
        self._last_T = T
        if len(self._seen_keys) > 100_000:
            cutoff = now - 6 * 3600
            self._seen_keys = {k: t for k, t in self._seen_keys.items() if t > cutoff}
        self.mark_success(max(0.0, now - T) if T else None, len(out))
        return out

    async def fetch_evidence(self, listing: Listing, comp_window_s: int = 24 * 3600) -> Optional[MarketEvidence]:
        if not self.permission_granted or not self._rows_cache:
            return None
        name_l = listing.item_name.lower()
        rows = [r for r in self._rows_cache if str(r.get("i") or "").lower() == name_l]
        if not rows:
            return None
        ev = MarketEvidence(item_key=listing.item_key, item_name=listing.item_name, category=listing.category,
                            source_data_age_s=max(0.0, time.time() - self._last_T) if self._last_T else None)
        cutoff = time.time() - comp_window_s
        sold_prices = []
        for r in rows:
            try:
                p = int(round(float(r.get("u") or r.get("p") or 0)))
            except (TypeError, ValueError):
                continue
            if p <= 0:
                continue
            at = float(r.get("t") or 0)
            sold = bool(r.get("sold"))
            c = Comparable(unit_price=p, at=at, sold=sold, seller=str(r.get("s") or ""), channel=int(r.get("c") or 0) or None,
                           fm_room=parse_fm_room(str(r.get("r") or "")), source=self.name)
            ev.comps.append(c)
            if sold:
                sold_prices.append(p)
                if at >= cutoff:
                    ev.sold_recent_24h += 1
            elif str(r.get("st") or "") == "up" and at >= cutoff and not (
                c.seller.lower() == listing.seller.lower() and p == listing.price):
                ev.active_asks.append(c)
        if sold_prices:
            sold_prices.sort()
            ev.sold_median = sold_prices[len(sold_prices) // 2]
            ev.sold_count = len(sold_prices)
        asks = sorted(c.unit_price for c in ev.active_asks)
        if asks:
            ev.ask_low, ev.ask_count, ev.ask_median = asks[0], len(asks), asks[len(asks) // 2]
        return ev
