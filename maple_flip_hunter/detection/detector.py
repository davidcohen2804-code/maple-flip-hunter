"""Opportunity classification + duplicate-alert suppression."""
from __future__ import annotations

import time
from typing import Optional

from ..config import Settings
from ..models import Level, Listing, Opportunity, Valuation


class Detector:
    def __init__(self, settings: Settings):
        self.s = settings
        self._alerted: dict[str, float] = {}     # listing_key -> alerted at (in-memory; DB holds history)

    def classify(self, listing: Listing, v: Valuation, analysis_ms: float = 0.0) -> Opportunity:
        s = self.s
        reasons: list[str] = []
        stale = listing.source_age_s > s.max_listing_age_s
        level = Level.NONE

        if v.reference is None or v.quick is None or v.net_profit is None:
            if listing.source_ref_price and listing.price <= listing.source_ref_price * (1 - s.uncertain_min_discount):
                level = Level.UNCERTAIN
                reasons.append("price far below the source's reference but no reliable valuation")
        else:
            net, roi = v.net_profit, v.roi or 0.0
            within_budget = listing.price <= s.max_buy_price
            if not within_budget:
                reasons.append(f"buy price {listing.price:,} exceeds budget {s.max_buy_price:,}")
            if net < s.min_net_profit:
                reasons.append(f"net profit {net:,} below minimum {s.min_net_profit:,}")

            jackpot_profit = net >= s.jackpot_min_profit or (roi >= s.jackpot_roi and net >= s.jackpot_roi_profit)
            demand_ok = v.liquidity in ("HIGH", "MEDIUM")
            if within_budget and net >= s.min_net_profit:
                if jackpot_profit and v.confidence >= s.jackpot_min_confidence and demand_ok:
                    level = Level.JACKPOT
                    reasons.append(f"net {net:,} (ROI {roi:.0%}) with {v.confidence_label} confidence and {v.liquidity} liquidity")
                elif v.confidence >= s.good_min_confidence and demand_ok:
                    level = Level.GOOD
                    reasons.append(f"net {net:,} (ROI {roi:.0%}), confidence {v.confidence_label}, liquidity {v.liquidity}")
                else:
                    level = Level.UNCERTAIN
                    if not demand_ok:
                        reasons.append(f"liquidity {v.liquidity}: resale speed unproven")
                    if v.confidence < s.good_min_confidence:
                        reasons.append(f"confidence {v.confidence:.2f} below {s.good_min_confidence:.2f}")
            elif within_budget and v.discount is not None and v.discount >= s.uncertain_min_discount:
                level = Level.UNCERTAIN
                reasons.append(f"{v.discount:.0%} under reference but profit after fee is only {net:,}")

        if stale and level != Level.NONE:
            reasons.append(f"observation is {listing.source_age_s/60:.0f} min old (stale) - not alerted")
        return Opportunity(listing=listing, valuation=v, level=level, analysis_ms=analysis_ms, stale=stale,
                           reasons=reasons)

    # ------------------------------------------------------------------
    def should_alert(self, opp: Opportunity, already_alerted_in_db: bool = False) -> Optional[str]:
        """Return the sound/level name to alert with, or None when no alert must fire."""
        if opp.stale or opp.level in (Level.NONE, Level.UNCERTAIN):
            return None
        if opp.level == Level.JACKPOT and not self.s.alert_jackpot:
            return None
        if opp.level == Level.GOOD and not self.s.alert_good:
            return None
        if opp.key in self._alerted or already_alerted_in_db:
            return None
        self._alerted[opp.key] = time.time()
        if len(self._alerted) > 20_000:
            cutoff = time.time() - 24 * 3600
            self._alerted = {k: t for k, t in self._alerted.items() if t > cutoff}
        return opp.level.value
