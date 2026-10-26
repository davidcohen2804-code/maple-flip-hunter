"""Synthetic tests for the pricing engine and the classifier (spec §15 cases 1,2,3,5,6,12)."""
from __future__ import annotations

from maple_flip_hunter.detection import Detector
from maple_flip_hunter.models import Level
from maple_flip_hunter.pricing import PricingEngine, median
from tests.conftest import make_evidence, make_listing


def test_1_scroll_120k_listed_16k_is_jackpot(settings):
    lst = make_listing(price=16_000)
    ev = make_evidence(lst)
    v = PricingEngine(settings).value(lst, ev)
    assert v.reference == 120_000
    assert v.quick == 114_000                      # 95% of reference, below cheapest competitor*0.97
    assert v.net_profit == round(114_000 * 0.97 - 16_000)  # 94,580
    assert v.confidence >= 0.7 and v.liquidity == "HIGH"
    opp = Detector(settings).classify(lst, v)
    assert opp.level == Level.JACKPOT


def test_2_50k_item_listed_25k_is_good_flip(settings):
    lst = make_listing(name="Red Whip", price=25_000, ref=50_000)
    ev = make_evidence(lst, sold_median=50_000, sold_count=30, sales24=3, asks=(52_000, 55_000),
                       sold_prices=(49_000, 50_000, 51_000, 50_500))
    v = PricingEngine(settings).value(lst, ev)
    assert v.quick == 47_500
    assert v.net_profit == round(47_500 * 0.97 - 25_000)   # 21,075 >= 15,000 minimum
    opp = Detector(settings).classify(lst, v)
    assert opp.level == Level.GOOD


def test_3_equipment_with_different_stats_is_not_compared(settings):
    """Clean Zard vs +7 scrolled Zard: different item keys; unknown stats lose confidence."""
    from maple_flip_hunter.collector.normalize import item_key

    clean = item_key("Zard", "Equip", {"watk": 56, "slots": 7})
    scrolled = item_key("Zard", "Equip", {"watk": 70, "slots": 0})
    unknown = item_key("Zard", "Equip", None)
    assert clean != scrolled and clean != unknown and unknown.endswith("#?")

    lst = make_listing(name="Zard", price=20_000, category="Equip", stats=None, ref=100_000)
    ev = make_evidence(lst, sold_median=100_000, sold_count=30, stats_matched=False)
    v = PricingEngine(settings).value(lst, ev)
    assert any("stats unknown" in r for r in v.risks)
    assert v.confidence <= 0.40
    opp = Detector(settings).classify(lst, v)
    assert opp.level == Level.UNCERTAIN      # shown, never alarmed: variant cannot be verified

    lst2 = make_listing(name="Zard", price=20_000, category="Equip", stats={"watk": 56, "slots": 7}, ref=100_000)
    ev2 = make_evidence(lst2, sold_median=100_000, sold_count=30, stats_matched=True,
                        asks=(99_000, 105_000, 110_000), sold_prices=(98_000, 100_000, 102_000))
    v2 = PricingEngine(settings).value(lst2, ev2)
    assert Detector(settings).classify(lst2, v2).level == Level.JACKPOT


def test_5_many_cheaper_competitors_cap_the_quick_price(settings):
    lst = make_listing(price=30_000, ref=100_000)
    cheap = (35_000, 35_500, 36_000, 36_000, 37_000, 38_000, 38_000, 39_000, 40_000, 41_000)
    ev = make_evidence(lst, sold_median=100_000, sold_count=50, asks=cheap)
    v = PricingEngine(settings).value(lst, ev)
    assert v.reference == 36_000                # median of the 5 cheapest live asks
    assert v.quick == round(35_000 * 0.97)      # undercuts the cheapest competitor, NOT 100,000
    assert v.net_profit < settings.min_net_profit
    assert Detector(settings).classify(lst, v).level != Level.JACKPOT


def test_6_little_sales_history_lowers_confidence(settings):
    lst = make_listing(price=16_000, ref=120_000)
    ev = make_evidence(lst, sold_median=None, sold_count=0, sales24=0, sold_prices=(), asks=(120_000, 125_000),
                       ask_median=122_000, ask_count=8)
    v = PricingEngine(settings).value(lst, ev)
    assert v.reference == round(122_000 * 0.9)
    assert v.liquidity == "UNKNOWN"
    assert any("asking prices" in r for r in v.risks)
    opp = Detector(settings).classify(lst, v)
    assert opp.level == Level.UNCERTAIN

    ev_none = make_evidence(lst, sold_median=None, sold_count=0, sales24=0, sold_prices=(), asks=(), ask_count=0)
    v2 = PricingEngine(settings).value(lst, ev_none)
    assert v2.reference is None and v2.confidence_label == "INSUFFICIENT DATA" and v2.confidence_score == 10


def test_12_selling_fee_calculation(settings):
    lst = make_listing(name="Thing", price=20_000, ref=40_000)
    ev = make_evidence(lst, sold_median=40_000, sold_count=20, asks=(), sold_prices=(39_000, 40_000, 41_000))
    v = PricingEngine(settings).value(lst, ev)
    assert v.quick == 38_000
    assert v.net_profit == 38_000 * 0.97 - 20_000  # 16,860
    settings.selling_fee = 0.0
    v0 = PricingEngine(settings).value(lst, ev)
    assert v0.net_profit == 18_000
    settings.selling_fee = 0.10
    v10 = PricingEngine(settings).value(lst, ev)
    assert v10.net_profit == round(38_000 * 0.9 - 20_000)


def test_absurdly_low_price_is_flagged(settings):
    lst = make_listing(price=1_600, ref=120_000)
    v = PricingEngine(settings).value(lst, make_evidence(lst))
    assert any("<3%" in r for r in v.risks)


def test_median_helper():
    assert median([]) is None
    assert median([3, 1, 2]) == 2
    assert median([1, 2, 3, 4]) == 2.5


def test_dropped_digit_guard_same_shop(settings):
    """Real pattern from cozy data: one shop showing 74,999 / 64,999 / 7,499 for the same scroll."""
    from maple_flip_hunter.models import Comparable
    import time

    lst = make_listing(name="Earring DEX Scroll: Intermediate", price=7_499, seller="Adrigard", ref=94_999)
    ev = make_evidence(lst, sold_median=95_555, sold_count=348, sales24=20, asks=(74_444, 89_999, 99_999))
    ev.active_asks.append(Comparable(unit_price=74_999, at=time.time(), seller="Adrigard"))
    v = PricingEngine(settings).value(lst, ev)
    assert any("misread digit" in r for r in v.risks)
    assert v.confidence <= 0.40
    assert Detector(settings).classify(lst, v).level == Level.UNCERTAIN


def test_unknown_shop_name_caps_confidence(settings):
    lst = make_listing(name="Two-handed Sword Attack Scroll: Intermediate", price=19_999, seller="?", ref=95_000)
    v = PricingEngine(settings).value(lst, make_evidence(lst, sold_median=97_499, sold_count=50, asks=(84_999, 89_999)))
    assert v.confidence <= 0.55
    assert Detector(settings).classify(lst, v).level == Level.GOOD   # alert, but never a JACKPOT siren
