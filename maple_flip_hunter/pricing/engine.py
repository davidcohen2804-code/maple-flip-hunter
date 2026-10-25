"""Market pricing engine.

Produces, for a candidate listing:
  reference     - reasonable market valuation (completed sales first, asks second, never a blind site average)
  quick         - competitive price that should sell fast (undercuts the cheapest live competitors)
  conservative  - lower-risk price based on market depth
  net profit    - quick * (1 - fee) - buy
  confidence    - explainable 0..1 score
  liquidity     - HIGH / MEDIUM / LOW / UNKNOWN from real sale counts only
See docs/PRICING_ENGINE.md.
"""
from __future__ import annotations

import time
from typing import Optional, Sequence

from ..config import Settings
from ..models import Listing, MarketEvidence, Valuation


def median(values: Sequence[int | float]) -> Optional[float]:
    v = sorted(values)
    if not v:
        return None
    n = len(v)
    return float(v[n // 2]) if n % 2 else (v[n // 2 - 1] + v[n // 2]) / 2.0


def _label(score: float) -> str:
    return "HIGH" if score >= 0.70 else "MEDIUM" if score >= 0.45 else "LOW"


class PricingEngine:
    MIN_SOLD_FOR_REFERENCE = 5
    MIN_ASKS_FOR_REFERENCE = 5
    UNDERCUT = 0.97          # quick sale lists 3% under the cheapest live competitor
    QUICK_VS_REF = 0.95      # ...and never above 95% of reference
    CONSERVATIVE_FACTOR = 0.90

    def __init__(self, settings: Settings):
        self.s = settings

    # ------------------------------------------------------------------
    def value(self, listing: Listing, ev: Optional[MarketEvidence]) -> Valuation:
        v = Valuation()
        buy = listing.price
        if ev is None:
            v.risks.append("no market evidence available for this item")
            v.explanation.append("Unknown item: no reference data from any source.")
            return v

        now = time.time()
        v.evidence_age_s = ev.source_data_age_s
        comp_cutoff = now - self.s.comp_window_s
        sold_prices = [c.unit_price for c in ev.comps if c.sold and c.at >= now - self.s.sold_window_s]
        asks = sorted(c.unit_price for c in ev.active_asks if c.at >= comp_cutoff)
        v.competitors = len(asks)
        v.cheapest_competitor = asks[0] if asks else None

        # ---------------- reference -----------------------------------
        ref: Optional[float] = None
        ref_basis = ""
        if ev.sold_median and ev.sold_count >= self.MIN_SOLD_FOR_REFERENCE:
            ref, ref_basis = float(ev.sold_median), f"median of {ev.sold_count} completed sales"
        elif len(sold_prices) >= 3:
            ref, ref_basis = median(sold_prices), f"median of {len(sold_prices)} observed sales (thin)"
            v.risks.append("few completed sales - valuation based on a thin sample")
        elif ev.ask_median and ev.ask_count >= self.MIN_ASKS_FOR_REFERENCE:
            ref, ref_basis = float(ev.ask_median) * 0.90, f"90% of median asking price ({ev.ask_count} asks, no sale data)"
            v.risks.append("no reliable completed sales - reference derived from asking prices")
        elif ev.secondary_reference:
            ref, ref_basis = float(ev.secondary_reference) * 0.90, f"90% of {ev.secondary_label or 'secondary reference'}"
            v.risks.append("reference comes from a secondary price list, not observed sales")
        if ref is None:
            v.explanation.append("Insufficient comparable transactions: cannot estimate a resale value.")
            v.risks.append("insufficient comparable transactions")
            v.confidence, v.confidence_label = 0.1, "LOW"
            return v

        # ---------------- competitive conditions ----------------------
        # If many live competitors sit far below the reference, the market has moved: cap the reference.
        if len(asks) >= 5:
            cheap5 = median(asks[:5]) or ref
            if cheap5 < ref * 0.75:
                v.risks.append(f"{len(asks)} competing sellers list well below the reference ({int(cheap5):,}); reference capped")
                ref = cheap5
                ref_basis += "; capped by competing asks"
        v.reference = int(round(ref))
        v.explanation.append(f"Reference {v.reference:,} = {ref_basis}.")

        # ---------------- quick & conservative ------------------------
        quick = ref * self.QUICK_VS_REF
        if asks:
            undercut = asks[0] * self.UNDERCUT
            if undercut < quick:
                quick = undercut
                v.explanation.append(f"Quick sale undercuts the cheapest of {len(asks)} live competitors ({asks[0]:,}).")
            else:
                v.explanation.append(f"Cheapest live competitor {asks[0]:,}; quick sale set at 95% of reference.")
        else:
            v.explanation.append("No live competing listings seen in the comp window.")
        conservative = quick * self.CONSERVATIVE_FACTOR
        if len(asks) >= 3:
            conservative = min(conservative, asks[min(2, len(asks) - 1)] * 0.95)
        if ev.ask_p25 and ev.ask_count >= 5:
            conservative = min(conservative, float(ev.ask_p25))
        v.quick = int(round(quick))
        v.conservative = int(round(max(0.0, conservative)))

        # ---------------- profit ---------------------------------------
        fee = self.s.selling_fee
        v.net_profit = int(round(v.quick * (1 - fee) - buy))
        v.net_profit_conservative = int(round(v.conservative * (1 - fee) - buy))
        v.roi = (v.net_profit / buy) if buy > 0 else None
        v.discount = 1 - buy / v.reference if v.reference else None
        v.explanation.append(f"Net profit = {v.quick:,} × (1 − {fee:.0%}) − {buy:,} = {v.net_profit:,}.")

        # ---------------- liquidity -----------------------------------
        sales24 = ev.sold_recent_24h
        if sales24 >= 5 or ev.sold_count >= 50:
            v.liquidity = "HIGH"
        elif sales24 >= 1 or ev.sold_count >= 10:
            v.liquidity = "MEDIUM"
        elif ev.sold_count > 0 or len(sold_prices) > 0:
            v.liquidity = "LOW"
        else:
            v.liquidity = "UNKNOWN"
            v.risks.append("no completed-sale information - demand unknown")

        # ---------------- confidence ----------------------------------
        score = 0.0
        n_sold = max(ev.sold_count, len(sold_prices))
        score += min(0.35, 0.35 * n_sold / 20.0)                       # sale sample size
        n_comps = len(ev.comps)
        score += min(0.20, 0.20 * n_comps / 10.0)                      # comparables depth
        age = ev.source_data_age_s if ev.source_data_age_s is not None else 3600
        score += 0.15 if age < 600 else 0.10 if age < 3600 else 0.03   # evidence freshness
        if ev.stats_matched is None:
            score += 0.15                                               # non-equipment: variants not an issue
        elif ev.stats_matched:
            score += 0.15
        else:
            v.risks.append("equipment stats unknown: a scrolled/clean variant mismatch is possible")
        # price plausibility: absurdly low prices are often OCR misreads (e.g. 1,600 vs 16,000)
        if v.reference and buy < v.reference * 0.03:
            v.risks.append("price is <3% of reference - possible misread or bait listing")
            score += 0.0
        elif ref_basis.startswith("median of") and "thin" not in ref_basis:
            score += 0.15
        else:
            score += 0.07
        if listing.source_age_s > self.s.max_listing_age_s:
            v.risks.append("listing observation is stale")
            score *= 0.5
        if ev.stats_matched is False:
            score = min(score, 0.40)   # unverifiable equipment variant: never above LOW -> shown as UNCERTAIN only
        # Dropped-digit guard (seen in real cozy data: a shop showing 74,999 / 64,999 / 7,499 for the same scroll).
        same_shop_10x = [c for c in ev.active_asks if c.seller and listing.seller
                         and c.seller.lower() == listing.seller.lower() and 9.0 <= c.unit_price / buy <= 11.0]
        if same_shop_10x:
            v.risks.append(f"same shop lists this item at {same_shop_10x[0].unit_price:,} (~10x): likely a misread digit")
            score = min(score, 0.40)
        elif v.reference and 0.9 <= (buy * 10) / v.reference <= 1.1:
            v.risks.append("price x10 equals the reference: a dropped digit is plausible")
            score *= 0.7
        if ev.source_data_age_s is not None and ev.source_data_age_s > self.s.max_evidence_age_s:
            v.risks.append(f"reference statistics are {ev.source_data_age_s/3600:.1f} h old - market may have moved")
            score = min(score, 0.50)
        if not listing.seller or listing.seller in ("?", "null", "None"):
            v.risks.append("scanner did not read the shop name - you must find the shop by channel/room")
            score = min(score, 0.55)
        v.confidence = round(min(1.0, max(0.0, score)), 3)
        v.confidence_label = _label(v.confidence)
        return v
