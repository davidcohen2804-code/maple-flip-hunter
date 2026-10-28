"""User settings (pydantic model persisted as JSON)."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pydantic import BaseModel, Field, model_validator

from .paths import settings_path

log = logging.getLogger(__name__)


class SoundTier(BaseModel):
    """One profit band -> one sound. Bands are half-open [min_profit, max_profit]; max_profit None = no upper bound."""
    name: str = ""
    min_profit: int = Field(0, ge=0)
    max_profit: int | None = Field(None, ge=0)
    sound: str = "bell"              # id from notifications.sounds.SOUND_LIBRARY
    enabled: bool = True

    def contains(self, net_profit: int) -> bool:
        return net_profit >= self.min_profit and (self.max_profit is None or net_profit <= self.max_profit)

    @model_validator(mode="after")
    def _range_ok(self) -> "SoundTier":
        if self.max_profit is not None and self.max_profit < self.min_profit:
            raise ValueError(f"tier '{self.name}': max_profit {self.max_profit} below min_profit {self.min_profit}")
        return self


def default_sound_tiers() -> list[SoundTier]:
    return [
        SoundTier(name="Nice flip", min_profit=10_000, max_profit=19_999, sound="bell"),
        SoundTier(name="Great flip", min_profit=20_000, max_profit=49_999, sound="melody"),
        SoundTier(name="JACKPOT", min_profit=50_000, max_profit=None, sound="jackpot"),
    ]


def validate_tiers(tiers: list[SoundTier]) -> list[SoundTier]:
    """Sort by min_profit and refuse overlapping bands (gaps are allowed: a profit in a gap alerts silently)."""
    out = sorted(tiers, key=lambda t: t.min_profit)
    for a, b in zip(out, out[1:]):
        if a.max_profit is None or b.min_profit <= a.max_profit:
            raise ValueError(f"sound tiers overlap: '{a.name}' [{a.min_profit}, {a.max_profit}] and "
                             f"'{b.name}' [{b.min_profit}, {b.max_profit}]")
    return out


def tier_for_profit(tiers: list[SoundTier], net_profit: int | None) -> SoundTier | None:
    if net_profit is None:
        return None
    for t in tiers:
        if t.contains(net_profit):
            return t
    return None


class Settings(BaseModel):
    # --- profitability -------------------------------------------------
    min_buy_price: int = Field(2_500, ge=0, description="Alert only on listings priced at or above this (mesos)")
    max_buy_price: int = Field(10_000, ge=0, description="Alert only on listings priced at or below this (mesos)")
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

    # --- QUICK FLIPS ONLY: stricter gate on JACKPOT / GOOD FLIP alerts --------
    quick_flips_only: bool = False
    quick_flip_min_net_profit: int = Field(10_000, ge=0, description="Minimum net profit at the CONSERVATIVE sale price, after fee")
    quick_flip_min_confidence: float = Field(0.80, ge=0.0, le=1.0)
    quick_flip_max_data_age_s: int = Field(300, ge=30, description="Listing observation AND reference statistics must be at most this old")
    quick_flip_require_high_liquidity: bool = True

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
    # Sound per NET-PROFIT band (the profit the pricing engine actually computed, not the JACKPOT/GOOD label).
    # Tiers must not overlap; a disabled tier (or a profit outside every tier) alerts without sound.
    sound_tiers: list[SoundTier] = Field(default_factory=default_sound_tiers)
    # Minimum deal-confidence grade for an AUDIBLE alert (LOW deals still show in the table / toast without sound)
    alert_min_confidence_label: str = Field("MEDIUM", pattern="^(LOW|MEDIUM|HIGH)$")

    @model_validator(mode="after")
    def _tiers_ok(self) -> "Settings":
        self.sound_tiers = validate_tiers(self.sound_tiers)
        return self
    show_uncertain: bool = True
    start_minimized: bool = False
    run_on_windows_startup: bool = False

    # --- sources --------------------------------------------------------
    source_public_market_enabled: bool = True
    source_public_market_interval_s: float = Field(60.0, ge=60.0)
    source_cozy_enabled: bool = True
    source_cozy_interval_s: float = Field(10.0, ge=5.0)
    source_cozy_max_shops_per_cycle: int = 25
    source_msw_enabled: bool = True
    # maplestory.watch Terms (2026-10-27): /api/ is not public and apps may not read it. Only set True after WRITTEN
    # permission from the site (Discord https://discord.gg/tdgvx66gk6); record who/when in the note.
    source_msw_permission_granted: bool = False
    source_msw_permission_note: str = ""
    source_msw_interval_s: float = Field(180.0, ge=60.0)
    source_msw_max_item_lookups_per_cycle: int = 40
    source_msw_candidate_discount: float = 0.20
    # Club FM (maplefm.gg): their Terms of Use require written permission for automated collection.
    # Set permission_granted ONLY after Club FM has agreed; paste a token if they issue one for /data/live.
    source_clubfm_enabled: bool = True
    source_clubfm_permission_granted: bool = False
    source_clubfm_token: str = ""
    source_clubfm_interval_s: float = Field(60.0, ge=30.0, description="Snapshot polling interval (their limit: 1 per 60 s)")
    source_clubfm_live_interval_s: float = Field(60.0, ge=5.0, description="Live-feed polling interval when a token works")
    source_clubfm_live_backoff_s: float = Field(30.0, ge=5.0, description="First retry delay after the live feed refuses/fails; doubles up to 10 min")
    source_clubfm_base_url: str = Field("https://api.maplefm.gg", description="Change only for the local simulator (tools/clubfm_simulator.py)")
    # FM Watcher (Club FM's free scanner that YOU run): Maple Flip Hunter reads its local files
    # (watcher_data_v2.json / my_scans.csv) — your own scans, no server access, no permission needed.
    source_fmwatcher_enabled: bool = True
    source_fmwatcher_dir: str = Field("", description="Folder where FM Watcher's start.bat lives (empty = off)")
    source_fmwatcher_autostart: bool = Field(False, description="Start FM Watcher hidden when Maple Flip Hunter starts; stop it on quit if we started it (only with physical_features_enabled)")
    source_fmwatcher_interval_s: float = Field(5.0, ge=2.0)
    source_fmwatcher_max_age_s: int = Field(1800, ge=60, description="Ignore scans older than this")
    # cozy.markets: the public JSON answers browsers but returns HTTP 403 to this app (Cloudflare bot
    # protection), both from datacenters and from a home PC. The adapter stays enabled so a future
    # whitelisting / token simply works; nothing circumvents the block.
    source_cozy_token: str = ""

    # --- retention ------------------------------------------------------
    retention_listings_days: int = 7
    retention_events_days: int = 30
    retention_notifications_days: int = 30

    # --- Physical features (OFF in the distributed build) ----------------------
    # False = pure alert system: no FM Watcher auto-start, no Physical Scanner / Free Spot Detector tabs, no screen
    # capture, no local-scan adapter. The code stays in the repository; set true in settings.json to re-enable.
    physical_features_enabled: bool = False

    # --- Free Spot Detector ----------------------------------------------
    spots_map_name: str = Field("Free Market", description="Map being monitored (as you name it)")
    spots_room: int | None = Field(None, description="FM room number (None for town maps)")
    spots_channel: int | None = Field(None, description="Channel you are on")
    spots_alert_ratings: list[str] = Field(default_factory=lambda: ["PRIME"])
    spots_fps: float = Field(1.0, ge=0.2, le=4.0)
    spots_vacate_confirm_s: float = Field(20.0, ge=5.0)
    spots_scopes: list[dict] = Field(default_factory=list, description="[{map, room, channels:[...], enabled}] — empty = everything")
    spots_sound: str = "spot"
    spots_capture_rect: list[int] = Field(default_factory=list, description="[x, y, w, h] screen rectangle of the game window; empty = whole primary screen")

    # --- UI memory ------------------------------------------------------
    ui_settings_dialog_geometry: str = Field("", description="base64 QWidget.saveGeometry() of the Settings window")
    ui_main_window_geometry: str = Field("", description="base64 saveGeometry() of the main window (incl. maximized state)")
    ui_main_splitter_state: str = Field("", description="base64 QSplitter.saveState() — table / details split")

    def save(self, path: Path | None = None) -> None:
        path = path or settings_path()
        path.write_text(self.model_dump_json(indent=2), encoding="utf-8")

    def _friends_hardening(self) -> "Settings":
        """Friends Edition never runs physical features or unpermitted sources, whatever settings.json says."""
        try:
            from .edition import is_friends
        except Exception:  # noqa: BLE001
            return self
        if is_friends():
            self.physical_features_enabled = False
            self.source_fmwatcher_autostart = False
            self.source_fmwatcher_enabled = False
            self.source_clubfm_base_url = "https://api.maplefm.gg"
            if not self.source_cozy_token.strip():
                self.source_cozy_enabled = False     # no access permission → never polled (would only ever be HTTP 403)
            # provider permissions are the OWNER's: they ship in friends_config.json at build time, a friend's
            # settings.json cannot switch a source on
            try:
                from .licensing.client import LicenseClient
                cfg = LicenseClient._load_config()
            except Exception:  # noqa: BLE001
                cfg = {}
            self.source_msw_permission_granted = bool(cfg.get("msw_permission", False))
            self.source_clubfm_permission_granted = bool(cfg.get("clubfm_permission", False)) and bool(self.source_clubfm_token.strip())
        return self

    @classmethod
    def load(cls, path: Path | None = None) -> "Settings":
        return cls._load_raw(path)._friends_hardening()

    @classmethod
    def _load_raw(cls, path: Path | None = None) -> "Settings":
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
