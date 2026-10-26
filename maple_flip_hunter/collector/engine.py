"""MarketEngine: runs all source adapters on a background asyncio loop, normalizes and stores
listings, values candidates, classifies opportunities and fires alerts. UI-independent."""
from __future__ import annotations

import asyncio
import logging
import threading
import time
from typing import Any, Callable, Optional

from ..adapters import ClubFMAdapter, CozyAdapter, FMWatcherAdapter, MapleStoryWatchAdapter
from ..adapters.base import BaseAdapter
from ..config import Settings, tier_for_profit
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
        self._adapters_from_settings = adapters is None
        self.adapters: list[BaseAdapter] = adapters if adapters is not None else self._default_adapters()
        self._callbacks: dict[str, list[Callback]] = {k: [] for k in
                                                      ("listing", "opportunity", "alert", "health", "log", "availability", "valued")}
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._thread: Optional[threading.Thread] = None
        self._tasks: list[asyncio.Task] = []
        self.running = False
        self.scanner_state = "OFFLINE"
        self.last_data_at: Optional[float] = None
        self._eval_lock: Optional[asyncio.Lock] = None
        self._coverage_cache: Optional[dict[str, Any]] = None
        self._revalue_later: list[Listing] = []
        # availability verification: its own queue + task, fed AFTER alerts; never on the alert path
        self._verify_q: Optional[asyncio.PriorityQueue] = None
        self._verify_seq = 0
        self._verify_next: dict[str, float] = {}        # listing_key -> earliest next check
        self._opps: dict[str, Opportunity] = {}          # live opportunities by key (for re-checks / re-appearance)
        self.verify_min_interval_s = 120.0
        self.verify_max_age_s = 2 * 3600                 # stop re-checking rows older than this

    # ----------------------------------------------------------- wiring
    def _default_adapters(self) -> list[BaseAdapter]:
        out: list[BaseAdapter] = []
        s = self.settings
        if s.source_cozy_enabled:
            out.append(CozyAdapter(interval_s=s.source_cozy_interval_s, max_shops_per_cycle=s.source_cozy_max_shops_per_cycle,
                                   token=s.source_cozy_token))
        if s.physical_features_enabled and s.source_fmwatcher_enabled and s.source_fmwatcher_dir.strip():
            out.append(FMWatcherAdapter(interval_s=s.source_fmwatcher_interval_s, directory=s.source_fmwatcher_dir.strip(),
                                        max_age_s=s.source_fmwatcher_max_age_s))
        if s.source_msw_enabled:
            out.append(MapleStoryWatchAdapter(interval_s=s.source_msw_interval_s,
                                              max_item_lookups=s.source_msw_max_item_lookups_per_cycle,
                                              candidate_discount=s.source_msw_candidate_discount))
        if s.source_clubfm_enabled:
            out.append(ClubFMAdapter(interval_s=s.source_clubfm_interval_s,
                                     permission_granted=s.source_clubfm_permission_granted,
                                     token=s.source_clubfm_token, base_url=s.source_clubfm_base_url,
                                     live_interval_s=s.source_clubfm_live_interval_s,
                                     live_backoff_s=s.source_clubfm_live_backoff_s))
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
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(5.0)            # a previous stop() is still winding down
        if self._adapters_from_settings:
            # rebuild from the CURRENT settings (tokens, enabled sources, permission flags) with fresh clients
            self.adapters = self._default_adapters()
        for a in self.adapters:
            a.reset()
        self.last_data_at = None
        self.scanner_state = "OFFLINE"
        self.running = True
        self._loop = asyncio.new_event_loop()   # created here so stop() can always reach it, even immediately
        self._thread = threading.Thread(target=self._thread_main, name="market-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 10.0) -> None:
        """Stop polling, cancel in-flight requests, close HTTP clients. Safe to call twice; start() may follow."""
        if not self.running:
            return
        self.running = False
        loop = self._loop
        if loop and not loop.is_closed():
            try:
                loop.call_soon_threadsafe(self._request_shutdown)
            except RuntimeError:
                pass
        if self._thread:
            self._thread.join(timeout)
            if self._thread.is_alive():
                log.warning("engine thread did not finish within %.0fs", timeout)
        self.scanner_state = "OFFLINE"
        self._emit("health", self.health())

    def _request_shutdown(self) -> None:
        """Runs on the engine loop: ask adapters to stop, then cancel whatever is still running after a short grace
        period (a poll in the middle of a long lookup loop or a back-off sleep must not delay Stop)."""
        for a in self.adapters:
            a.stop()

        def _cancel() -> None:
            for t in self._tasks:
                if not t.done():
                    t.cancel()
        self._loop.call_later(1.5, _cancel)

    def _thread_main(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_until_complete(self._main())
        except Exception:  # noqa: BLE001
            log.exception("engine loop crashed")
        finally:
            try:
                pending = [t for t in asyncio.all_tasks(self._loop) if not t.done()]
                for t in pending:
                    t.cancel()
                if pending:
                    self._loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            except Exception:  # noqa: BLE001
                pass
            self._loop.close()

    async def _main(self) -> None:
        self._eval_lock = asyncio.Lock()
        for a in self.adapters:
            a._stop = asyncio.Event()  # fresh event bound to this loop (restart support)
        self._tasks = []
        for a in self.adapters:
            t = asyncio.create_task(a.run(self._make_handler(a)))
            t._mfh_source = a.name  # type: ignore[attr-defined]
            self._tasks.append(t)
        self._tasks.append(asyncio.create_task(self._housekeeping()))
        self._verify_q = asyncio.PriorityQueue()
        self._tasks.append(asyncio.create_task(self._verify_loop()))
        try:
            await asyncio.wait_for(asyncio.gather(*self._tasks, return_exceptions=True), timeout=None)
        finally:
            # orderly shutdown: cancel anything still pending (a request mid-flight), then close clients
            for t in self._tasks:
                if not t.done():
                    t.cancel()
            await asyncio.gather(*self._tasks, return_exceptions=True)
            await asyncio.gather(*(a.aclose() for a in self.adapters), return_exceptions=True)
            self._tasks = []

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
            try:
                self._coverage_cache = self._compute_coverage()
            except Exception:  # noqa: BLE001
                pass
            if self._revalue_later:
                msw = next((a for a in self.adapters if isinstance(a, MapleStoryWatchAdapter)), None)
                if msw is None or msw.index:
                    todo, self._revalue_later = self._revalue_later[:200], self._revalue_later[200:]
                    log.info("re-valuing %d own scans now that the market index is available", len(todo))
                    for lst in todo:
                        try:
                            await self.evaluate(lst)
                        except Exception:  # noqa: BLE001
                            log.exception("re-value failed for %s", lst.listing_key)
            if time.time() - last_cleanup > 3600:
                try:
                    s = self.settings
                    removed = self.db.cleanup(s.retention_listings_days, s.retention_events_days, s.retention_notifications_days)
                    log.info("retention cleanup: %s", removed)
                except Exception:  # noqa: BLE001
                    log.exception("cleanup failed")
                last_cleanup = time.time()
            for _ in range(20):
                if not self.running:
                    return
                await asyncio.sleep(0.1)

    # ------------------------------------------------------------ state
    def _refresh_state(self) -> None:
        online = [a for a in self.adapters if a.health.status in (SourceStatus.ONLINE, SourceStatus.IDLE)]
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

    def restart_source(self, name: str) -> bool:
        """Restart ONE source (fresh client, STARTING, failure counters reset) without touching the others."""
        a = self._adapter_for(name)
        if a is None or not self.running or self._loop is None:
            return False
        a.health.restarts += 1
        a.health.stopped_by_user = False
        log.info("SOURCE_RESTART %s (#%d)", name, a.health.restarts)

        async def do() -> None:
            for t in list(self._tasks):
                if getattr(t, "_mfh_source", None) == name and not t.done():
                    t.cancel()
                    try:
                        await t
                    except (asyncio.CancelledError, Exception):  # noqa: BLE001
                        pass
                    self._tasks.remove(t)
            try:
                await a.aclose()
            except Exception:  # noqa: BLE001
                pass
            a.reset()
            a._stop = asyncio.Event()
            t = asyncio.create_task(a.run(self._make_handler(a)))
            t._mfh_source = name  # type: ignore[attr-defined]
            self._tasks.append(t)
        asyncio.run_coroutine_threadsafe(do(), self._loop)
        return True

    def diagnostics_text(self) -> str:
        """Copy-able diagnostics: per-source health, recent errors, metrics, UI lag."""
        lines = [f"Maple Flip Hunter diagnostics {time.strftime('%Y-%m-%d %H:%M:%S')} scanner={self.scanner_state}"]
        for h in self.health():
            lines.append(f"[{h.name}] {h.status.value} detail={h.detail!r} last_success={h.last_success_at and time.strftime('%H:%M:%S', time.localtime(h.last_success_at))} "
                         f"latency_ms={h.last_latency_ms} rows={h.listings_received} polls={h.polls} errors={h.errors} "
                         f"consecutive={h.consecutive_failures} restarts={h.restarts} newest_age_s={h.newest_data_age_s}")
            for at, msg in h.recent_errors[-5:]:
                lines.append(f"    {time.strftime('%H:%M:%S', time.localtime(at))} {msg}")
        m = self.metrics.snapshot()
        lines.append("metrics: " + ", ".join(f"{k}={v}" for k, v in m.items() if not isinstance(v, dict)))
        return "\n".join(lines)

    def coverage(self) -> dict[str, Any]:
        """Cheap for the UI: while the scanner runs, the housekeeping task refreshes this every 2 s on the engine
        thread (DB counts never run on the UI thread, which used to stall behind large ingest transactions)."""
        if self.running and self._coverage_cache is not None:
            return self._coverage_cache
        return self._compute_coverage()

    def _compute_coverage(self) -> dict[str, Any]:
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
                self._reappeared(lst)
                continue
            if kind == "duplicate":
                self.metrics.listings_duplicate += 1
                continue
            if kind == "new":
                self.metrics.listings_new += 1
            else:
                self.metrics.listings_repriced += 1
                old = self._opps.get(self.db.last_repriced_from or "")
                if old is not None and not old.gone:
                    self._set_availability(old, "REMOVED", lst.source_seen_at, f"seller changed the price to {lst.price:,}", lst.source)
            self.metrics.last_new_listing_at = now
            self._emit("listing", lst, kind)
            if lst.source == "fmwatcher":
                if lst.price < 1000:
                    continue                     # your own scans: value every meaningful row (band gates alerts only)
            elif not self._prefilter(lst):
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
        if lst.price > self.settings.max_buy_price or lst.price < self.settings.min_buy_price:
            return False   # stored and counted, but outside the alert range -> no evidence fetch
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
        # Club FM (when permitted): completed sales + live asks for rows that came from another source
        cf = next((a for a in self.adapters if isinstance(a, ClubFMAdapter)), None)
        if cf is not None and cf.name != lst.source and cf.permission_granted:
            try:
                cev = await cf.fetch_evidence(lst, self.settings.comp_window_s)
            except Exception:  # noqa: BLE001
                cev = None
            if cev is not None:
                if ev is None:
                    ev = cev
                else:
                    if ev.sold_count < PricingEngine.MIN_SOLD_FOR_REFERENCE and cev.sold_median:
                        ev.sold_median, ev.sold_count = cev.sold_median, cev.sold_count
                        ev.notes.append("completed-sale median taken from Club FM")
                    ev.sold_recent_24h = max(ev.sold_recent_24h, cev.sold_recent_24h)
                    ev.comps.extend(cev.comps)
                    ev.active_asks.extend(cev.active_asks)
                    if cev.source_data_age_s is not None and (ev.source_data_age_s is None or cev.source_data_age_s < ev.source_data_age_s):
                        ev.source_data_age_s = cev.source_data_age_s
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
        self._emit("valued", lst, valuation, opp.level)        # every evaluated row (the Physical tab shows all)
        if lst.source == "fmwatcher":
            try:
                self.db.save_valuation(lst.listing_key, valuation, opp.level.value)   # survives restarts
            except Exception:  # noqa: BLE001
                log.exception("valuation save failed")
            msw = next((a for a in self.adapters if isinstance(a, MapleStoryWatchAdapter)), None)
            if valuation.reference is None and msw is not None and not msw.index:
                self._revalue_later.append(lst)       # own scan arrived before the market index: price it again later
        if opp.level == Level.NONE:
            return None
        if opp.level == Level.UNCERTAIN and not self.settings.show_uncertain:
            return None
        self.db.save_opportunity(opp)
        self._opps[opp.key] = opp
        self._emit("opportunity", opp)
        sound = self.detector.should_alert(opp, self.db.was_alerted(opp.key))
        if sound and self.settings.notifications_enabled:
            self._fire_alert(opp, sound)
        elif opp.level in (Level.JACKPOT, Level.GOOD) and not opp.stale:
            self.metrics.duplicate_alerts_suppressed += 1
            log.info("AUDIO_SUPPRESSED reason=%s key=%s", "notifications disabled" if sound else "already alerted (dedupe)", opp.key)
        return opp

    def _fire_alert(self, opp: Opportunity, level: str) -> None:
        opp.notified_at = time.time()
        self.metrics.alerts += 1
        self.metrics.source_to_alert_s.add(opp.notified_at - opp.listing.source_seen_at)
        self.metrics.ingest_to_alert_ms.add((opp.notified_at - opp.listing.local_first_seen_at) * 1000)
        self.db.save_opportunity(opp)
        title, body = format_alert(opp)
        sound = self.sound_for(opp)
        log.info("ALERT_CREATED level=%s item=%r buy=%s net=%s sound=%s key=%s", level, opp.listing.item_name,
                 opp.listing.price, opp.valuation.net_profit, sound, opp.key)
        try:
            if self.notifier is not None:
                self.notifier.alert(opp, level, title, body, sound=sound)
        except Exception:  # noqa: BLE001
            log.exception("notifier failed")
        self.db.add_notification(opp.key, level, title, body, sound or "silent")
        self._emit("alert", opp, level, title, body)
        self.queue_verification(opp, priority=0)       # AFTER the alert: background availability check first in line

    def sound_for(self, opp: Opportunity) -> Optional[str]:
        """Sound id for this alert, chosen by the computed net profit (quick-sale net; the figure shown in the
        table). None = alert without sound (tier disabled, or profit outside every tier)."""
        order = {"INSUFFICIENT DATA": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}
        if order.get(opp.valuation.confidence_label, 0) < order.get(self.settings.alert_min_confidence_label, 2):
            log.info("AUDIO_SUPPRESSED reason=confidence %s below %s key=%s", opp.valuation.confidence_label,
                     self.settings.alert_min_confidence_label, opp.key)
            return None
        tier = tier_for_profit(self.settings.sound_tiers, opp.valuation.net_profit)
        if tier is None or not tier.enabled:
            return None
        return tier.sound

    def revalue(self, listings: list[Listing]) -> bool:
        """Re-run pricing for stored rows (e.g. own scans loaded from history that never got a valuation).
        Runs on the engine loop; goes through the normal evaluate() path, so alerts obey the usual rules
        (old rows are stale → no alert). Returns False when the scanner is not running."""
        if not self.running or self._loop is None or not listings:
            return False

        async def do() -> None:
            for lst in listings:
                try:
                    await self.evaluate(lst)
                except Exception:  # noqa: BLE001
                    log.exception("revalue failed for %s", lst.listing_key)
        asyncio.run_coroutine_threadsafe(do(), self._loop)
        return True

    # ----------------------------------------------------------- availability
    def queue_verification(self, opp: Opportunity, priority: int = 1, delay_s: float = 0.0) -> None:
        """Enqueue a background availability check. Priority 0 = alerted deals, 1 = other opportunities."""
        if self._verify_q is None or opp.listing.source == "test":
            return
        self._verify_seq += 1
        try:
            self._verify_q.put_nowait((priority, time.time() + delay_s, self._verify_seq, opp.key))
        except Exception:  # noqa: BLE001
            pass

    def _adapter_for(self, source: str) -> Optional[BaseAdapter]:
        return next((a for a in self.adapters if a.name == source), None)

    def _set_availability(self, opp: Opportunity, status: str, observed_at: float, note: str, source: str) -> None:
        changed = status != opp.availability
        opp.availability, opp.last_verified_at, opp.availability_note = status, observed_at, note
        try:
            self.db.save_availability(opp.key, status, observed_at, time.time(), note, source)
            self.db.save_opportunity(opp)
        except Exception:  # noqa: BLE001
            log.exception("availability save failed")
        self.metrics.verifications += 1
        if status in ("REMOVED", "SOLD"):
            self.metrics.verified_gone += 1
        log.info("AVAILABILITY %s key=%s via=%s note=%s", status, opp.key, source, note)
        self._emit("availability", opp, status, note, changed)

    def _reappeared(self, lst: Listing) -> None:
        """A source saw the same listing again (same key, same price): it is ACTIVE as of that observation."""
        opp = self._opps.get(lst.listing_key)
        if opp is None:
            return
        if opp.last_verified_at is None or lst.source_seen_at > opp.last_verified_at:
            self._set_availability(opp, "ACTIVE", lst.source_seen_at, "seen again by the source", lst.source)

    async def _verify_one(self, opp: Opportunity) -> None:
        src = self._adapter_for(opp.listing.source)
        now = time.time()
        if src is None or not src.verify_supported:
            note = src.verify_limit_note if src else "source not running"
            if opp.availability == "UNVERIFIED" and opp.availability_note != note:
                self._set_availability(opp, "UNVERIFIED", opp.last_verified_at or 0.0, note, opp.listing.source)
            return
        try:
            res = await asyncio.wait_for(src.verify(opp.listing), timeout=20)
        except Exception as exc:  # noqa: BLE001
            log.info("AVAILABILITY check failed key=%s: %s", opp.key, exc)
            res = None
        if res is None:
            if opp.availability == "UNVERIFIED":
                self._set_availability(opp, "UNVERIFIED", 0.0, f"no current confirmation ({src.verify_limit_note})", src.name)
            return
        status, at, note = res
        if status == "ACTIVE" and opp.last_verified_at and at <= opp.last_verified_at and opp.availability == "ACTIVE":
            return                                       # nothing newer than what we already knew
        self._set_availability(opp, status, at, note, src.name)
        if status == "ACTIVE" and now - opp.listing.source_seen_at < self.verify_max_age_s:
            self.queue_verification(opp, priority=1 if opp.notified_at is None else 0, delay_s=self.verify_min_interval_s)

    async def _verify_loop(self) -> None:
        """Drains the availability queue in the background. Runs beside the adapters; uses only the adapters'
        verify budgets, so discovery polls are never delayed by it."""
        assert self._verify_q is not None
        while self.running:
            try:
                prio, not_before, _seq, key = await asyncio.wait_for(self._verify_q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue
            opp = self._opps.get(key)
            if opp is None or opp.gone:
                continue
            wait = not_before - time.time()
            if wait > 0:
                await asyncio.sleep(min(wait, 5.0))
                if not_before - time.time() > 0:
                    self._verify_seq += 1
                    self._verify_q.put_nowait((prio, not_before, self._verify_seq, key))
                    continue
            if time.time() - self._verify_next.get(key, 0) < 1.0:
                continue
            self._verify_next[key] = time.time()
            try:
                await self._verify_one(opp)
            except Exception:  # noqa: BLE001
                log.exception("verification failed for %s", key)
            if len(self._opps) > 5000:
                cutoff = time.time() - 24 * 3600
                self._opps = {k: o for k, o in self._opps.items() if o.detected_at > cutoff}

    # ----------------------------------------------------------- helpers
    def simulate_alert(self) -> Opportunity:
        """'Test Automatic Alert': runs the REAL alert path (profit band → notifier → GUI card) for a synthetic
        TEST opportunity, from a background thread exactly like the scanner. Touches no database table, no
        metrics, no market data."""
        from ..models import Listing, Valuation

        now = time.time()
        lst = Listing(listing_key=f"test|simulated-alert|{int(now)}", source="test", item_name="TEST ALERT (simulated)",
                      item_key="test alert (simulated)", seller="TestShop", channel=1, fm_room=1, price=8_000,
                      source_seen_at=now, local_first_seen_at=now, source_url="")
        tiers = [t for t in self.settings.sound_tiers if t.enabled]
        net = tiers[-1].min_profit + 1 if tiers else 60_000
        v = Valuation(reference=net + 8_000 + 2_000, quick=int((net + 8_000) / 0.97) + 1, conservative=net + 8_000,
                      net_profit=net, net_profit_conservative=int(net * 0.9), roi=net / 8_000, discount=0.8,
                      confidence=0.95, confidence_label="HIGH", liquidity="HIGH",
                      explanation=["Simulated opportunity — this is a test of the alert chain, not market data."])
        opp = Opportunity(listing=lst, valuation=v, level=Level.JACKPOT, reasons=["TEST: simulated alert"])

        def worker() -> None:
            opp.notified_at = time.time()
            title, body = format_alert(opp)
            sound = self.sound_for(opp)
            log.info("ALERT_CREATED level=TEST(simulated) sound=%s thread=worker", sound)
            if self.notifier is not None:
                self.notifier.alert(opp, "JACKPOT", "🧪 TEST " + title, body, sound=sound)
            self._emit("alert", opp, "JACKPOT", "🧪 " + title, body)

        threading.Thread(target=worker, name="mfh-simulated-alert", daemon=True).start()
        return opp

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
                a.live_interval_s = settings.source_clubfm_live_interval_s
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
