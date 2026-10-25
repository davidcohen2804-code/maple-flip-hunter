"""MarketEngine: runs all source adapters on a background asyncio loop, normalizes and stores
listings, values candidates, classifies opportunities and fires alerts. UI-independent."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Optional

from ..adapters import ClubFMAdapter, CozyAdapter, MapleStoryWatchAdapter
from ..adapters.base import BaseAdapter
from ..config import Settings
from ..db import Database
from ..detection import Detector
from ..metrics import Metrics
from ..models import Comparable, Level, Listing, MarketEvidence, Opportunity, SourceHealth, SourceStatus
from ..pricing import PricingEngine
from .normalize import cross_source_key

log = logging.getLogger(__name__)

Callback = Callable[..., Any]


class MarketEngine:
    PREFILTER_DISCOUNT = 0.15   # evaluate only rows at least this far under the source's own reference

    def __init__(self, settings: Settings, db: Database, notifier: Optional[Any] = None,
                 adapters: Optional[list[BaseAdapter]] = None):
        self.settings = settings
        self.db = db
        self.notifier = notifier
        self.metrics = Metrics()
        self.pricing = PricingEngine(settings)
        self.detector = Detector(settings)
        self.adapters: list[BaseAdapter] = adapters if adapters is not None else self._default_adapters()
        self._callbacks: dict[str, list[Callback]] = {k: [] for k in
                                                      ("listing", "opportunity", "alert", "health", "log")}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._tasks: list[asyncio.Task] = []
        self.running = False
        self.scanner_state = "OFFLINE"
        self.last_data_at: Optional[float] = None
        self._eval_lock: Optional[asyncio.Lock] = None

    # ----------------------------------------------------------- wiring
    def _default_adapters(self) -> list[BaseAdapter]:
        out: list[BaseAdapter] = []
        s = self.settings
        if s.source_cozy_enabled:
            out.append(CozyAdapter(interval_s=s.source_cozy_interval_s, max_shops_per_cycle=s.source_cozy_max_shops_per_cycle,
                                   token=s.source_cozy_token))
        if s.source_msw_enabled:
            out.append(MapleStoryWatchAdapter(interval_s=s.source_msw_interval_s,
                                              max_item_lookups=s.source_msw_max_item_lookups_per_cycle,
                                              candidate_discount=s.source_msw_candidate_discount))
        if s.source_clubfm_enabled:
            out.append(ClubFMAdapter(interval_s=s.source_clubfm_interval_s,
                                     permission_granted=s.source_clubfm_permission_granted,
                                     token=s.source_clubfm_token))
        return out

    def on(self, event: str, cb: Callback) -> None:
        self._callbacks[event].append(cb)

    def _emit(self, event: str, *args: Any) -> None:
        for cb in self._callbacks.get(event, []):
            try:
                cb(*args)
            except Exception:  # noqa: BLE001
                log.exception("callback %s failed", event)

    # --------------------------------------------------------- lifecycle
    def start(self) -> None:
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self._thread_main, name="market-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        if not self.running:
            return
        self.running = False
        loop = self._loop
        if loop:
            for a in self.adapters:
                loop.call_soon_threadsafe(a.stop)
        if self._thread:
            self._thread.join(timeout)
        self.scanner_state = "OFFLINE"
        self._emit("health", self.health())

    def _thread_main(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:  # noqa: BLE001
            log.exception("engine loop crashed")
        finally:
            try:
                self._loop.run_until_complete(asyncio.gather(*(a.aclose() for a in self.adapters), return_exceptions=True))
            except Exception:  # noqa: BLE001
                pass
            self._loop.close()
            self._loop = None

    async def _main(self) -> None:
        self._eval_lock = asyncio.Lock()
        for a in self.adapters:
            a._stop = asyncio.Event()  # fresh event bound to this loop (restart support)
        self._tasks = [asyncio.create_task(a.run(self._make_handler(a))) for a in self.adapters]
        self._tasks.append(asyncio.create_task(self._housekeeping()))
        await asyncio.gather(*self._tasks, return_exceptions=True)

    def _make_handler(self, adapter: BaseAdapter) -> Callable[[list[Listing]], Any]:
        async def handler(listings: list[Listing]) -> None:
            self.metrics.poll_done(adapter.name)
            await self.ingest(listings)
        return handler

    async def _housekeeping(self) -> None:
        last_cleanup = 0.0
        while self.running:
            self._refresh_state()
            for a in self.adapters:
                try:
                    self.db.save_source_status(a.health)
                except Exception:  # noqa: BLE001
                    pass
            self._emit("health", self.health())
            if time.time() - last_cleanup > 3600:
                try:
                    s = self.settings
                    removed = self.db.cleanup(s.retention_listings_days, s.retention_events_days, s.retention_notifications_days)
                    log.info("retention cleanup: %s", removed)
                except Exception:  # noqa: BLE001
                    log.exception("cleanup failed")
                last_cleanup = time.time()
            await asyncio.sleep(2.0)

    # ------------------------------------------------------------ state
    def _refresh_state(self) -> None:
        online = [a for a in self.adapters if a.health.status == SourceStatus.ONLINE]
        any_ok = [a for a in self.adapters if a.health.status in (SourceStatus.ONLINE, SourceStatus.STALE, SourceStatus.RATE_LIMITED)]
        fresh = self.last_data_at is not None and time.time() - self.last_data_at < 180
        if not self.running:
            self.scanner_state = "OFFLINE"
        elif online and fresh:
            self.scanner_state = "LIVE"
        elif any_ok:
            self.scanner_state = "DEGRADED"
        else:
            self.scanner_state = "OFFLINE"

    def health(self) -> list[SourceHealth]:
        return [a.health for a in self.adapters]

    def coverage(self) -> dict[str, Any]:
        cov: dict[str, Any] = {"catalog_items": 0, "items_with_listings": self.db.item_count(),
                               "listings_1h": self.db.listing_count(time.time() - 3600)}
        for a in self.adapters:
            if isinstance(a, CozyAdapter):
                cov["catalog_items"] = max(cov["catalog_items"], len(a.catalog))
            elif isinstance(a, MapleStoryWatchAdapter):
                cov["catalog_items"] = max(cov["catalog_items"], len(a.index))
        return cov

    # ----------------------------------------------------------- ingest
    async def ingest(self, listings: list[Listing]) -> list[Opportunity]:
        """Normalize, store and evaluate a batch of freshly observed rows. Returns the opportunities found."""
        out: list[Opportunity] = []
        now = time.time()
        self.last_data_at = now
        for lst in listings:
            self.metrics.listings_ingested += 1
            self.metrics.source_age_at_ingest.add(lst.source_age_s)
            if lst.sold:
                self.db.record_sale(lst.item_key, lst.price, lst.source_seen_at, lst.source, lst.seller, lst.stats)
                continue
            ck = cross_source_key(lst.item_key, lst.seller, lst.channel, lst.price)
            try:
                kind, _old = self.db.upsert_listing(lst, ck)
            except Exception:  # noqa: BLE001
                log.exception("db upsert failed")
                self.metrics.errors += 1
                continue
            if kind == "seen":
                continue
            if kind == "duplicate":
                self.metrics.listings_duplicate += 1
                continue
            if kind == "new":
                self.metrics.listings_new += 1
            else:
                self.metrics.listings_repriced += 1
            self.metrics.last_new_listing_at = now
            self._emit("listing", lst, kind)
            if not self._prefilter(lst):
                continue
            try:
                opp = await self.evaluate(lst)
            except Exception:  # noqa: BLE001
                log.exception("evaluation failed for %s", lst.listing_key)
                self.metrics.errors += 1
                continue
            if opp:
                out.append(opp)
        self._refresh_state()
        return out

    def _prefilter(self, lst: Listing) -> bool:
        """Cheap gate before fetching evidence: within budget and visibly under the source's own reference
        (or unknown reference -> evaluate anyway when cheap enough to matter)."""
        if lst.price > self.settings.max_buy_price:
            return False
        if lst.source_ref_price:
            return lst.price <= lst.source_ref_price * (1 - self.PREFILTER_DISCOUNT)
        return lst.price >= 1000  # unknown reference: evaluate only non-trivial prices

    # --------------------------------------------------------- evaluate
    async def gather_evidence(self, lst: Listing) -> Optional[MarketEvidence]:
        primary = next((a for a in self.adapters if a.name == lst.source), None)
        ev: Optional[MarketEvidence] = None
        if primary is not None and hasattr(primary, "fetch_evidence"):
            try:
                ev = await primary.fetch_evidence(lst, self.settings.comp_window_s)  # type: ignore[attr-defined]
            except Exception as exc:  # noqa: BLE001
                log.debug("evidence from %s failed: %s", lst.source, exc)
        # secondary source (maplestory.watch index) when the primary has thin sale data
        msw = next((a for a in self.adapters if isinstance(a, MapleStoryWatchAdapter)), None)
        if msw is not None and msw.name != lst.source and msw.index:
            idx = msw.index.get(lst.item_name)
            if idx and idx.get("typical"):
                if ev is None:
                    ev = MarketEvidence(item_key=lst.item_key, item_name=lst.item_name, category=lst.category)
                equip_variant = (lst.category or "").lower() == "equip"
                if (ev.sold_count < PricingEngine.MIN_SOLD_FOR_REFERENCE) and not equip_variant:
                    ev.sold_median = int(idx["typical"])
                    ev.sold_count = int(idx.get("samples") or 0)
                    ev.sold_recent_24h = max(ev.sold_recent_24h, int(idx.get("sales24h") or 0))
                    ev.notes.append("completed-sale median taken from maplestory.watch (14-day sales)")
                else:
                    ev.secondary_reference = ev.secondary_reference or int(idx["typical"])
                    ev.secondary_label = ev.secondary_label or "maplestory.watch typical"
        if ev is None:
            return None
        # local database: competing asks and sales seen by any source
        cutoff = time.time() - self.settings.comp_window_s
        known = {(c.seller or "").lower() + "|" + str(c.unit_price) for c in ev.active_asks}
        for r in self.db.active_asks(lst.item_key, cutoff, lst.listing_key):
            if r["seller"] and r["seller"].lower() == lst.seller.lower() and r["price"] == lst.price:
                continue
            k = (r["seller"] or "").lower() + "|" + str(r["price"])
            if k in known:
                continue
            known.add(k)
            ev.active_asks.append(Comparable(unit_price=r["price"], at=r["source_seen_at"], seller=r["seller"],
                                             channel=r["channel"], fm_room=r["fm_room"], source=r["source"]))
        for r in self.db.sales_for(lst.item_key, time.time() - self.settings.sold_window_s):
            ev.comps.append(Comparable(unit_price=r["unit_price"], at=r["at"], sold=True, seller=r["seller"], source="local"))
        return ev

    async def evaluate(self, lst: Listing) -> Optional[Opportunity]:
        t0 = time.perf_counter()
        ev = await self.gather_evidence(lst)
        valuation = self.pricing.value(lst, ev)
        analysis_ms = (time.perf_counter() - t0) * 1000
        self.metrics.evaluations += 1
        self.metrics.analysis_ms.add(analysis_ms)
        opp = self.detector.classify(lst, valuation, analysis_ms)
        if opp.level == Level.NONE:
            return None
        if opp.level == Level.UNCERTAIN and not self.settings.show_uncertain:
            return None
        self.db.save_opportunity(opp)
        self._emit("opportunity", opp)
        sound = self.detector.should_alert(opp, self.db.was_alerted(opp.key))
        if sound and self.settings.notifications_enabled:
            self._fire_alert(opp, sound)
        elif opp.level in (Level.JACKPOT, Level.GOOD) and not opp.stale:
            self.metrics.duplicate_alerts_suppressed += 1
        return opp

    def _fire_alert(self, opp: Opportunity, level: str) -> None:
        opp.notified_at = time.time()
        self.metrics.alerts += 1
        self.metrics.source_to_alert_s.add(opp.notified_at - opp.listing.source_seen_at)
        self.metrics.ingest_to_alert_ms.add((opp.notified_at - opp.listing.local_first_seen_at) * 1000)
        self.db.save_opportunity(opp)
        title, body = format_alert(opp)
        try:
            if self.notifier is not None:
                self.notifier.alert(opp, level, title, body)
        except Exception:  # noqa: BLE001
            log.exception("notifier failed")
        self.db.add_notification(opp.key, level, title, body, level)
        self._emit("alert", opp, level, title, body)

    # ----------------------------------------------------------- helpers
    def test_alarm(self, level: str = "JACKPOT") -> None:
        if self.notifier is not None:
            self.notifier.play(level)

    def update_settings(self, settings: Settings) -> None:
        self.settings = settings
        self.pricing.s = settings
        self.detector.s = settings
        for a in self.adapters:
            if isinstance(a, CozyAdapter):
                a.interval_s = settings.source_cozy_interval_s
                a.max_shops_per_cycle = settings.source_cozy_max_shops_per_cycle
            elif isinstance(a, MapleStoryWatchAdapter):
                a.interval_s = settings.source_msw_interval_s
                a.max_item_lookups = settings.source_msw_max_item_lookups_per_cycle
                a.candidate_discount = settings.source_msw_candidate_discount
            elif isinstance(a, ClubFMAdapter):
                a.interval_s = settings.source_clubfm_interval_s
                a.permission_granted = settings.source_clubfm_permission_granted


def format_alert(opp: Opportunity) -> tuple[str, str]:
    l, v = opp.listing, opp.valuation
    if opp.level == Level.JACKPOT:
        title = "🚨 JACKPOT — NOOB TRADE DETECTED 🚨"
    elif opp.level == Level.GOOD:
        title = "💰 GOOD FLIP"
    else:
        title = "❔ UNCERTAIN OPPORTUNITY"
    age = l.source_age_s
    body = (f"{l.item_name}\n"
            f"BUY: {l.price:,}   QUICK SELL: {v.quick or 0:,}   NET: {v.net_profit or 0:,}\n"
            f"{l.location} · Shop: {l.seller}\n"
            f"Confidence: {v.confidence_label} · First observed by source: {age:.0f}s ago")
    return title, body
