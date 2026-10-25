"""Price band, QUICK FLIPS ONLY gate and the shared table/alert filter."""
from __future__ import annotations

import time

from maple_flip_hunter.config import Settings
from maple_flip_hunter.detection import Detector, passes_user_filters
from maple_flip_hunter.models import Comparable, Level
from maple_flip_hunter.pricing import PricingEngine
from tests.conftest import make_evidence, make_listing


def _val(price, ref, sold_count=60, sales24=8, age_s=60, asks=None, settings=None, seller="ExampleShop"):
    s = settings or Settings()
    lst = make_listing(name="Hat Accuracy Scroll: Intermediate", price=price, ref=ref, age_s=10, seller=seller)
    ev = make_evidence(lst, sold_median=ref, sold_count=sold_count, sales24=sales24, age_s=age_s,
                       asks=asks if asks is not None else (ref + 1000, ref + 2000, ref + 5000),
                       sold_prices=(ref - 500, ref, ref + 500, ref - 200, ref + 200))
    return lst, PricingEngine(s).value(lst, ev)


def test_default_band_is_2500_to_10000():
    s = Settings()
    assert s.min_buy_price == 2_500 and s.max_buy_price == 10_000


def test_price_below_minimum_or_above_maximum_never_alerts():
    s = Settings()
    for price in (2_000, 10_001, 16_000):
        lst, v = _val(price, 40_000, settings=s)
        opp = Detector(s).classify(lst, v)
        assert opp.level == Level.NONE, (price, opp.level, opp.reasons)
        assert Detector(s).should_alert(opp) is None
        assert not passes_user_filters(lst, v, s)[0]
    lst, v = _val(9_000, 40_000, settings=s)
    assert Detector(s).classify(lst, v).level == Level.JACKPOT    # 9,000 -> ~35K quick sale, inside the band


def test_quick_flips_only_uses_conservative_profit_and_strict_gates():
    s = Settings(quick_flips_only=True)
    lst, v = _val(9_000, 40_000, settings=s)               # conservative ~34K*0.97-9K = 24K: passes
    opp = Detector(s).classify(lst, v)
    assert opp.level == Level.JACKPOT and any("QUICK FLIPS ONLY" in r for r in opp.reasons)
    # conservative net below 10,000 -> no alert even though the optimistic net is fine
    lst2, v2 = _val(9_000, 19_000, settings=s)
    assert v2.net_profit_conservative < 10_000 <= (v2.net_profit or 0) + 10_000
    opp2 = Detector(s).classify(lst2, v2)
    assert opp2.level == Level.UNCERTAIN and Detector(s).should_alert(opp2) is None
    # confidence below 0.80 -> no alert
    lst3, v3 = _val(9_000, 40_000, sold_count=3, settings=s)
    assert v3.confidence < 0.80 and Detector(s).classify(lst3, v3).level != Level.JACKPOT
    # reference data older than 5 minutes -> no alert
    lst4, v4 = _val(9_000, 40_000, age_s=600, settings=s)
    o4 = Detector(s).classify(lst4, v4)
    assert o4.level == Level.UNCERTAIN and any("reference data" in r for r in o4.reasons)
    # listing older than 5 minutes -> no alert
    lst5, v5 = _val(9_000, 40_000, settings=s)
    lst5.source_seen_at = time.time() - 400
    assert Detector(s).classify(lst5, v5).level == Level.UNCERTAIN
    # liquidity below HIGH -> no alert
    lst6, v6 = _val(9_000, 40_000, sold_count=30, sales24=1, settings=s)
    assert v6.liquidity == "MEDIUM" and Detector(s).classify(lst6, v6).level == Level.UNCERTAIN
    s2 = Settings(quick_flips_only=True, quick_flip_require_high_liquidity=False)
    assert Detector(s2).classify(lst6, v6).level in (Level.JACKPOT, Level.GOOD)
    # no conservative estimate at all -> no alert
    lst7, v7 = _val(9_000, 40_000, settings=s)
    v7.conservative = None
    v7.net_profit_conservative = None
    assert Detector(s).classify(lst7, v7).level == Level.UNCERTAIN


def test_table_filter_follows_settings_immediately(tmp_path):
    """Rows already in the table vanish (but stay in the DB) when the saved settings exclude them."""
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    from maple_flip_hunter.ui.table_model import OpportunityFilter, OpportunityModel

    app = QApplication.instance() or QApplication([])
    s = Settings()
    model, proxy = OpportunityModel(), OpportunityFilter()
    proxy.settings = s
    proxy.setSourceModel(model)
    rows = []
    for i, (price, ref) in enumerate(((9_000, 40_000), (9_000, 19_000), (12_000, 60_000), (3_000, 20_000))):
        lst, v = _val(price, ref, settings=s, seller=f"shop{i}")
        rows.append(Detector(s).classify(lst, v))
    for o in rows:
        model.upsert(o)
    assert model.rowCount() == 4                         # the model (and DB) keep everything
    assert proxy.rowCount() == 2                         # 12,000 is above the maximum; 9,000/19,000 nets only 8,510
    s2 = Settings(min_net_profit=5_000)
    proxy.settings = s2
    proxy.invalidateFilter()
    assert proxy.rowCount() == 3                         # lowering the minimum brings the 8,510 row back at once
    s3 = Settings(quick_flips_only=True)                 # conservative net >= 10,000 only
    proxy.settings = s3
    proxy.invalidateFilter()
    shown = {model.opportunity_at(proxy.mapToSource(proxy.index(r, 0)).row()).listing.price
             + model.opportunity_at(proxy.mapToSource(proxy.index(r, 0)).row()).valuation.reference for r in range(proxy.rowCount())}
    assert 9_000 + 19_000 not in shown and 9_000 + 40_000 in shown
    s4 = Settings(min_buy_price=5_000)
    proxy.settings = s4
    proxy.invalidateFilter()
    assert all(model.opportunity_at(proxy.mapToSource(proxy.index(r, 0)).row()).listing.price >= 5_000 for r in range(proxy.rowCount()))
    assert model.rowCount() == 4


def test_settings_change_while_live_applies_to_new_alerts():
    """Engine running: tightening the filters at runtime changes what gets alerted from the next row on."""
    import asyncio

    from maple_flip_hunter.collector.engine import MarketEngine
    from maple_flip_hunter.db import Database
    from maple_flip_hunter.notifications import Notifier
    from tests.unit.test_engine import FakeAdapter

    s = Settings()
    alerts = []
    eng = MarketEngine(s, Database(":memory:"), Notifier(enable_audio=False),
                       adapters=[FakeAdapter(lambda l: make_evidence(l, sold_median=40_000, sold_count=60, sales24=8,
                                                                     asks=(41_000, 42_000), sold_prices=(39_500, 40_000, 40_500, 39_800, 40_200)))])
    eng.on("alert", lambda o, lvl, t, b: alerts.append(o))
    run = lambda c: asyncio.new_event_loop().run_until_complete(c)  # noqa: E731
    run(eng.ingest([make_listing(name="A", price=9_000, ref=40_000, seller="s1", item_id="1")]))
    assert len(alerts) == 1
    eng.update_settings(Settings(quick_flips_only=True, quick_flip_min_net_profit=50_000))   # impossible floor
    run(eng.ingest([make_listing(name="B", price=9_000, ref=40_000, seller="s2", item_id="2")]))
    assert len(alerts) == 1                                # stored + evaluated, but no alert
    assert eng.db.listing_count() == 2
    eng.update_settings(Settings(min_buy_price=9_500))
    run(eng.ingest([make_listing(name="C", price=9_000, ref=40_000, seller="s3", item_id="3")]))
    assert len(alerts) == 1 and eng.db.listing_count() == 3
    eng.update_settings(Settings())
    run(eng.ingest([make_listing(name="D", price=9_000, ref=40_000, seller="s4", item_id="4")]))
    assert len(alerts) == 2
