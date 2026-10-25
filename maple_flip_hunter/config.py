"""User settings (pydantic model persisted as JSON)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field

from .paths import settings_path

log = logging.getLogger(__name__)


class Settings(BaseModel):
    # --- profitability -------------------------------------------------
    max_buy_price: int = Field(75_000, ge=0, description="Preferred maximum purchase price (mesos)")
    preferred_value_min: int = Field(50_000, ge=0)
    preferred_value_max: int = Field(150_000, ge=0)
    min_net_profit: int = Field(15_000, ge=0, description="Minimum worthwhile estimated net profit")
    selling_fee: float = Field(0.03, ge=0.0, le=0.5, description="Fraction taken when selling (0.03 = 3%)")

    # --- classification -------------------------------------------------
    jackpot_min_profit: int = 50_000
    jackpot_roi_profit: int = 25_000          # profit needed when ROI >= jackpot_roi
    jackpot_roi: float = 1.0
    jackpot_min_confidence: float = 0.60
    good_min_confidence: float = 0.45
    uncertain_min_discount: float = 0.30      # show as UNCERTAIN when discount >= this but evidence weak

    # --- freshness ------------------------------------------------------
    max_listing_age_s: int = Field(900, description="Listings older than this (source observation) are STALE and never alerted")
    comp_window_s: int = Field(24 * 3600, description="Window for competing active listings")
    sold_window_s: int = Field(7 * 24 * 3600, description="Window for completed-sale comparables")
    max_evidence_age_s: int = Field(6 * 3600, description="Reference stats older than this cap confidence at 0.5")

    # --- alerts ---------------------------------------------------------
    notifications_enabled: bool = True
    alert_volume: float = Field(0.9, ge=0.0, le=1.0)
    alert_jackpot: bool = True
    alert_good: bool = True
    show_uncertain: bool = True
    start_minimized: bool = False
    run_on_windows_startup: bool = False

    # --- sources --------------------------------------------------------
    source_cozy_enabled: bool = True
    source_cozy_interval_s: float = Field(10.0, ge=5.0)
    source_cozy_max_shops_per_cycle: int = 25
    source_msw_enabled: bool = True
    source_msw_interval_s: float = Field(180.0, ge=60.0)
    source_msw_max_item_lookups_per_cycle: int = 200
    source_msw_candidate_discount: float = 0.20

    # --- retention ------------------------------------------------------
    retention_listings_days: int = 7
    retention_events_days: int = 30
    retention_notifications_days: int = 30

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        path = path or settings_path()
        if path.exists():
            try:
                return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception as exc:  # corrupt file -> defaults, keep a backup
                log.warning("settings unreadable (%s); using defaults", exc)
                try:
                    path.rename(path.with_suffix(".bad.json"))
                except OSError:
                    pass
        s = cls()
        try:
            s.save(path)
        except OSError:
            pass
        return s
