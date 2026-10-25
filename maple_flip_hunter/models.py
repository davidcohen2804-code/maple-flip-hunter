"""Core data models shared by adapters, collector, pricing, detection and UI."""
from __future__ import annotations

import time
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class SourceStatus(str, Enum):
    ONLINE = "ONLINE"
    STALE = "STALE"
    RATE_LIMITED = "RATE LIMITED"
    AUTH_REQUIRED = "AUTH REQUIRED"
    UNAVAILABLE = "UNAVAILABLE"
    UNSUPPORTED = "UNSUPPORTED"
    STARTING = "STARTING"


class Level(str, Enum):
    JACKPOT = "JACKPOT"
    GOOD = "GOOD FLIP"
    UNCERTAIN = "UNCERTAIN"
    NONE = "NONE"


class Listing(BaseModel):
    """One shop row observed by a data source. Prices are per unit, in mesos."""

    listing_key: str                       # stable id: source|item|seller|channel|price|stats
    source: str                            # 'cozy' | 'msw'
    item_name: str
    item_key: str                          # normalized name (+ stats signature for equipment)
    source_item_id: Optional[str] = None
    category: Optional[str] = None         # Equip / Use / Setup / Etc / Cash
    subcategory: Optional[str] = None
    seller: str
    shop_title: Optional[str] = None
    channel: Optional[int] = None
    fm_room: Optional[int] = None
    map_name: Optional[str] = None
    price: int                             # unit price
    qty: int = 1
    stock: Optional[int] = None
    stats: Optional[dict[str, Any]] = None
    sold: bool = False
    confirmed: bool = True
    source_seen_at: float                  # when the SOURCE observed this row (unix seconds)
    source_first_seen_at: Optional[float] = None
    local_first_seen_at: float = Field(default_factory=time.time)
    source_ref_price: Optional[int] = None  # the source's own reference ("fm"/"typical") if any
    source_ref_basis: Optional[str] = None
    source_url: Optional[str] = None
    icon_url: Optional[str] = None

    @property
    def source_age_s(self) -> float:
        return max(0.0, time.time() - self.source_seen_at)

    @property
    def location(self) -> str:
        parts = []
        if self.channel is not None:
            parts.append(f"CH {self.channel}")
        if self.fm_room is not None:
            parts.append(f"FM {self.fm_room}")
        elif self.map_name:
            parts.append(self.map_name)
        return " · ".join(parts) if parts else "unknown"


class Comparable(BaseModel):
    """A comparable observation used by the pricing engine."""
    unit_price: int
    at: float
    sold: bool = False
    seller: Optional[str] = None
    channel: Optional[int] = None
    fm_room: Optional[int] = None
    stats: Optional[dict[str, Any]] = None
    source: str = ""


class MarketEvidence(BaseModel):
    """Everything the pricing engine knows about an item at valuation time."""
    item_key: str
    item_name: str
    category: Optional[str] = None
    sold_median: Optional[int] = None       # median of completed sales (if source provides)
    sold_count: int = 0                     # completed-sale sample size behind sold_median
    sold_recent_24h: int = 0
    ask_median: Optional[int] = None
    ask_p25: Optional[int] = None
    ask_low: Optional[int] = None
    ask_count: int = 0
    secondary_reference: Optional[int] = None  # e.g. another site's reference price
    secondary_label: Optional[str] = None
    comps: list[Comparable] = Field(default_factory=list)   # recent sightings (sold + asks), same variant when stats known
    active_asks: list[Comparable] = Field(default_factory=list)  # competing unsold listings (excludes the candidate)
    evidence_at: float = Field(default_factory=time.time)
    source_data_age_s: Optional[float] = None
    stats_matched: Optional[bool] = None    # None = not applicable (non-equip), False = equip w/o stats
    notes: list[str] = Field(default_factory=list)


class Valuation(BaseModel):
    reference: Optional[int] = None
    quick: Optional[int] = None
    conservative: Optional[int] = None
    net_profit: Optional[int] = None        # based on quick sale price
    net_profit_conservative: Optional[int] = None
    roi: Optional[float] = None
    discount: Optional[float] = None
    confidence: float = 0.0                 # 0..1
    confidence_label: str = "LOW"
    liquidity: str = "UNKNOWN"              # HIGH / MEDIUM / LOW / UNKNOWN
    competitors: int = 0
    cheapest_competitor: Optional[int] = None
    evidence_age_s: Optional[float] = None   # age of the reference statistics behind this valuation
    explanation: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)


class Opportunity(BaseModel):
    listing: Listing
    valuation: Valuation
    level: Level
    detected_at: float = Field(default_factory=time.time)
    analysis_ms: float = 0.0
    notified_at: Optional[float] = None
    stale: bool = False
    reasons: list[str] = Field(default_factory=list)
    # availability (verified in the background AFTER the alert; never delays it)
    availability: str = "UNVERIFIED"           # ACTIVE / UNVERIFIED / REMOVED / SOLD
    last_verified_at: Optional[float] = None    # when a source last confirmed/denied the listing
    availability_note: str = ""

    @property
    def key(self) -> str:
        return self.listing.listing_key

    @property
    def gone(self) -> bool:
        return self.availability in ("REMOVED", "SOLD")


class SourceHealth(BaseModel):
    name: str
    label: str
    status: SourceStatus = SourceStatus.STARTING
    last_success_at: Optional[float] = None
    last_error: Optional[str] = None
    last_latency_ms: Optional[float] = None
    newest_data_age_s: Optional[float] = None   # age of newest observation received in last poll
    requests: int = 0
    errors: int = 0
    listings_received: int = 0
    rate_limited_until: Optional[float] = None
    detail: str = ""
