from __future__ import annotations

from maple_flip_hunter.collector.normalize import (cross_source_key, item_key, listing_key, normalize_name,
                                                   parse_fm_room, stats_signature)
from maple_flip_hunter.config import Settings


def test_normalize_and_keys():
    assert normalize_name("  Bow  Attack Scroll ") == "bow attack scroll"
    assert item_key("Red Whip", "Use", None) == "red whip"
    assert item_key("Zard", "Equip", {"watk": 56, "slots": 7, "junk": 1}) == "zard#watk=56,slots=7"
    assert stats_signature(None) == ""
    assert listing_key("cozy", "zard#watk=56", "Bob", 3, 100) == "cozy|zard#watk=56|bob|3|100"
    assert cross_source_key("zard", "BOB", None, 5) == "zard|bob|?|5"


def test_parse_fm_room():
    assert parse_fm_room("Free Market <13>") == 13
    assert parse_fm_room("FM 4") == 4
    assert parse_fm_room("Kerning City Construction Site") is None
    assert parse_fm_room("anything", 7) == 7


def test_settings_roundtrip(tmp_path):
    p = tmp_path / "s.json"
    s = Settings(max_buy_price=99_000, selling_fee=0.05)
    s.save(p)
    s2 = Settings.load(p)
    assert s2.max_buy_price == 99_000 and s2.selling_fee == 0.05
    p.write_text("{not json", encoding="utf-8")
    s3 = Settings.load(p)
    assert s3.max_buy_price == 75_000
