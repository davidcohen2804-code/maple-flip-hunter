from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import Comparable, Listing, MarketEvidence

FIXTURES = Path(__file__).parent / "fixtures"


FIXTURE_EPOCH = 1792936312.0      # when the real-site fixtures were captured (2026-10-25)


def load_fixture(name: str, shift_to_now: bool = True):
    """Fixtures hold real timestamps from capture day; shift them so 'seen N hours ago' stays true forever."""
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    if not shift_to_now:
        return data
    delta = time.time() - FIXTURE_EPOCH

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                if k in ("seen", "firstSeen", "updatedAt", "seenAt", "ts") and isinstance(v, (int, float)) and v > 1_600_000_000:
                    o[k] = v + delta
                else:
                    walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(data)
    return data


@pytest.fixture
def settings() -> Settings:
    # pricing/classification tests use the wide legacy band; the band itself is tested separately
    return Settings(min_buy_price=0, max_buy_price=75_000)


@pytest.fixture
def db() -> Database:
    return Database(":memory:")


def make_listing(name="Bow Attack Scroll 60%", price=16_000, seller="ExampleShop", channel=24, fm_room=25,
                 category="Use", stats=None, age_s=3.0, ref=120_000, source="cozy", item_id="1") -> Listing:
    from maple_flip_hunter.collector.normalize import item_key, listing_key

    ik = item_key(name, category, stats)
    return Listing(listing_key=listing_key(source, ik, seller, channel, price), source=source, item_name=name,
                   item_key=ik, source_item_id=item_id, category=category, seller=seller, channel=channel,
                   fm_room=fm_room, map_name=f"Free Market <{fm_room}>", price=price, stats=stats,
                   source_seen_at=time.time() - age_s, source_ref_price=ref, source_ref_basis="sold")


def make_evidence(listing: Listing, sold_median=120_000, sold_count=40, sales24=6, asks=(124_000, 125_000, 130_000),
                  sold_prices=(118_000, 120_000, 122_000, 119_000, 121_000), age_s=100, stats_matched=None,
                  ask_median=None, ask_count=0) -> MarketEvidence:
    now = time.time()
    return MarketEvidence(
        item_key=listing.item_key, item_name=listing.item_name, category=listing.category,
        sold_median=sold_median, sold_count=sold_count, sold_recent_24h=sales24,
        ask_median=ask_median, ask_count=ask_count, source_data_age_s=age_s, stats_matched=stats_matched,
        comps=[Comparable(unit_price=p, at=now - i * 600, sold=True) for i, p in enumerate(sold_prices)],
        active_asks=[Comparable(unit_price=p, at=now - 100, seller=f"seller{i}") for i, p in enumerate(asks)],
    )
