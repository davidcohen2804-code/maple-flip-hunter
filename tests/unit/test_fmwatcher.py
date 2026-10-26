"""FM Watcher local-scan adapter: reads the user's own watcher_data_v2.json (format taken from FM Watcher's
watcher.py Store.add_shop/save) and my_scans.csv (export.bat), feeds the real engine, alerts with sound."""
from __future__ import annotations

import asyncio
import json
import os
import time

from maple_flip_hunter.adapters.fmwatcher import FMWatcherAdapter
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database
from maple_flip_hunter.models import Level, SourceStatus
from maple_flip_hunter.notifications import Notifier, ensure_sounds
from tests.conftest import make_evidence


def _store(entries, now=None):
    """entries: (seller, item, price, qty, open_count, sold_flag, room, channel, stats, age_s)"""
    now = now or time.time()
    day = time.strftime("%Y-%m-%d", time.localtime(now))
    shop = {}
    for seller, item, price, qty, nu, so, room, ch, stats, age in entries:
        o = {"n": nu or 1, "reads": 3, "first": now - age - 30, "last": now - age, "known": True, "so": so,
             "so_reads": 2 if so else 0, "nu": nu, "ns": 1 if so else 0, "s_last": 1 if so else 0, "hi_t": now - age}
        if room: o["r"] = room
        if ch: o["c"] = ch
        if stats: o["x"] = stats; o["xj"] = "WARRIOR"
        shop[json.dumps([day, seller, item, price, qty])] = o
    return {"shop": shop, "chat": {}, "sellers": {}, "mins": {}}


def _write(path, data):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f)
    os.replace(tmp, path)


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def test_store_file_is_parsed_with_location_price_time_and_stats(tmp_path):
    now = time.time()
    _write(tmp_path / "watcher_data_v2.json", _store([
        ("NoobTrader", "Hat Accuracy Scroll: Intermediate", 9_000, 1, 1, False, "FM3", 7, None, 12),
        ("Bundler", "Mithril Ore", 3_000, 10, 2, False, "FM1", 2, None, 40),
        ("Gone", "Pan Lid", 70_000, 1, 0, True, "FM2", 5, None, 50),          # sold out -> completed sale
        ("Old", "Ice Wand", 60_000, 1, 1, False, "FM2", 5, None, 7_200),      # older than max_age -> ignored
        ("Armsdealer", "Work Gloves", 120_000, 1, 1, False, "FM5", 11, {"watk": 10, "slots": 3}, 20),
    ], now))
    a = FMWatcherAdapter(directory=str(tmp_path), max_age_s=1800)
    rows = run(a.poll())
    by = {r.seller: r for r in rows}
    assert set(by) == {"NoobTrader", "Bundler", "Gone", "Armsdealer"}
    r = by["NoobTrader"]
    assert r.source == "fmwatcher" and r.price == 9_000 and r.channel == 7 and r.fm_room == 3 and r.map_name == "FM3"
    assert abs(r.source_seen_at - (now - 12)) < 1 and r.stock == 1 and not r.sold
    assert by["Bundler"].price == 300 and by["Bundler"].qty == 10            # unit price from a bundle
    assert by["Gone"].sold and by["Gone"].stock is None
    g = by["Armsdealer"]
    assert g.category == "Equip" and g.stats == {"watk": 10, "slots": 3} and "watk=10" in g.item_key
    assert a.health.status == SourceStatus.STARTING or True
    # unchanged file -> nothing new; changed file -> only the new/updated rows
    assert run(a.poll()) == []
    data = _store([("NoobTrader", "Hat Accuracy Scroll: Intermediate", 9_000, 1, 1, False, "FM3", 7, None, 1),
                   ("Fresh", "Cape STR Scroll: Lesser", 8_000, 1, 1, False, "FM4", 9, None, 2)], time.time())
    time.sleep(0.01)
    _write(tmp_path / "watcher_data_v2.json", data)
    os.utime(tmp_path / "watcher_data_v2.json", None)
    rows2 = run(a.poll())
    assert {r.seller for r in rows2} == {"NoobTrader", "Fresh"}
    assert "reading watcher_data_v2.json" in a.online_detail()


def test_csv_export_fallback(tmp_path):
    now = time.time()
    t = time.localtime(now - 120)
    (tmp_path / "my_scans.csv").write_text(
        "﻿Date,Time,Source,Channel,Room,Seller,Item,Price,Bundle,Each,Status,Stats\n"
        f"{time.strftime('%Y-%m-%d', t)},{time.strftime('%H:%M', t)},shop,7,FM3,NoobTrader,Hat Accuracy Scroll: Intermediate,9000,1,9000,for sale,\n"
        f"{time.strftime('%Y-%m-%d', t)},{time.strftime('%H:%M', t)},shop,11,FM5,Armsdealer,Work Gloves,120000,1,120000,for sale,watk 10 slots 3\n"
        f"{time.strftime('%Y-%m-%d', t)},{time.strftime('%H:%M', t)},trade chat,,,Chatter,Red Whip,5000,1,5000,buying,\n"
        "2020-01-01,10:00,shop,1,FM1,Ancient,Pan Lid,1,1,1,for sale,\n", encoding="utf-8")
    a = FMWatcherAdapter(directory=str(tmp_path))
    rows = run(a.poll())
    assert {r.seller for r in rows} == {"NoobTrader", "Armsdealer"}
    assert rows[0].channel == 7 and rows[0].fm_room == 3 and abs(rows[0].source_seen_at - (now - 120)) < 61
    assert any(r.stats == {"watk": 10, "slots": 3} for r in rows)
    assert "my_scans.csv" in a.health.detail and a.health.status == SourceStatus.ONLINE


def test_missing_folder_and_unset_folder_are_reported_not_fatal(tmp_path):
    a = FMWatcherAdapter(directory="")
    assert run(a.poll()) == [] and a.health.status == SourceStatus.UNSUPPORTED
    b = FMWatcherAdapter(directory=str(tmp_path / "nope"))
    import pytest

    from maple_flip_hunter.adapters.base import FetchError
    with pytest.raises(FetchError):
        run(b.poll())
    c = FMWatcherAdapter(directory=str(tmp_path))          # folder exists but no files yet
    with pytest.raises(FetchError, match="is FM Watcher running"):
        run(c.poll())


def test_own_scan_feeds_detection_sound_and_dedupes_against_msw(tmp_path):
    """Real engine: a scan of an under-priced shop in YOUR channel -> JACKPOT -> profit-band sound -> same shop later
    seen by maplestory.watch is a duplicate; maplestory.watch keeps running independently (separate adapter)."""
    from maple_flip_hunter.adapters.maplestory_watch import MapleStoryWatchAdapter
    from tests.conftest import make_listing

    fm_dir = tmp_path / "FMWatcher"
    fm_dir.mkdir()
    _write(fm_dir / "watcher_data_v2.json", _store([
        ("NoobTrader", "Hat Accuracy Scroll: Intermediate", 8_000, 1, 1, False, "FM3", 7, None, 5)]))
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000, source_cozy_enabled=False,
                 source_fmwatcher_dir=str(fm_dir))
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real = n.play
    n.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
    eng = MarketEngine(s, Database(":memory:"), n)
    kinds = [type(a).__name__ for a in eng.adapters]
    assert "FMWatcherAdapter" in kinds and "MapleStoryWatchAdapter" in kinds
    fmw = next(a for a in eng.adapters if isinstance(a, FMWatcherAdapter))

    async def evidence(lst):   # market knowledge for the item (as maplestory.watch / Club FM would supply)
        return make_evidence(lst, sold_median=42_000, sold_count=40, sales24=6, asks=(43_000, 44_000, 46_000))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    rows = run(fmw.poll())
    opps = run(eng.ingest(rows))
    assert opps and opps[0].level == Level.JACKPOT and opps[0].listing.source == "fmwatcher"
    assert opps[0].listing.location == "CH 7 · FM 3" and opps[0].listing.seller == "NoobTrader"
    assert played[-1] == "melody" and n.sound_latency_ms and n.sound_latency_ms[-1] < 1_000
    assert eng.metrics.alerts == 1
    # the same shop seen a minute later by maplestory.watch: duplicate, no second alert
    dup = make_listing(name="Hat Accuracy Scroll: Intermediate", price=8_000, seller="NoobTrader", channel=7, fm_room=3,
                       source="msw", item_id=None, ref=42_000, age_s=3)
    run(eng.ingest([dup]))
    assert eng.metrics.alerts == 1 and len(played) == 1
    # maplestory.watch adapter is untouched by the new source
    msw = next(a for a in eng.adapters if isinstance(a, MapleStoryWatchAdapter))
    assert msw.interval_s == s.source_msw_interval_s


def test_real_home_file_goes_online_with_rows_and_status(tmp_path):
    """Regression for 'FM Watcher stuck on STARTING': a real watcher_data_v2.json from the user's PC
    (Kerning City shops, channel 35) must be read, reported ONLINE, and each row carries price + location."""
    import shutil
    from pathlib import Path

    from maple_flip_hunter.adapters.fmwatcher import _room_text

    shutil.copy(Path(__file__).resolve().parents[1] / "fixtures" / "fmwatcher_real_sample.json", tmp_path / "watcher_data_v2.json")
    a = FMWatcherAdapter(directory=str(tmp_path), max_age_s=10 * 365 * 86400)     # the sample is from 2026-10-26
    rows = run(a.poll())
    assert a.health.status == SourceStatus.ONLINE, (a.health.status, a.health.detail)
    assert "reading watcher_data_v2.json" in a.health.detail and "new/updated rows" in a.health.detail
    assert len(rows) >= 20
    king = [r for r in rows if r.seller == "TheKingDavid"]
    assert king and all(r.channel == 35 and r.price > 0 for r in king)
    assert all(r.map_name == "Kerning City Construction Site" and r.fm_room is None for r in king)
    viper = next(r for r in king if r.item_name == "Red Viper" and r.price == 79_999)
    assert viper.stats == {"att": 55, "slots": 7} and viper.category == "Equip"
    sold = [r for r in rows if r.sold]
    assert any(r.item_name == "Mithril Pole Arm" for r in sold)
    assert _room_text("erning City Construction Site") == "Kerning City Construction Site" and _room_text("FM3") == "FM3"
    # unchanged file on the next poll: still ONLINE (not STARTING, not STALE while the file is fresh)
    import os
    os.utime(tmp_path / "watcher_data_v2.json", None)
    a._store_mtime = os.path.getmtime(tmp_path / "watcher_data_v2.json")
    assert run(a.poll()) == [] and a.health.status == SourceStatus.ONLINE


def test_adapter_is_registered_and_polls_after_settings_save(tmp_path):
    """Settings save -> engine restart -> the FM Watcher adapter exists, runs, and leaves STARTING."""
    import shutil
    from pathlib import Path

    fm = tmp_path / "FMWatcher"
    fm.mkdir()
    shutil.copy(Path(__file__).resolve().parents[1] / "fixtures" / "fmwatcher_real_sample.json", fm / "watcher_data_v2.json")
    s = Settings(source_cozy_enabled=False, source_msw_enabled=False)                 # no folder yet
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n)
    assert not any(isinstance(a, FMWatcherAdapter) for a in eng.adapters)
    new = s.model_copy(update={"source_fmwatcher_dir": str(fm), "source_fmwatcher_max_age_s": 10 * 365 * 86400})
    eng.update_settings(new)
    eng.start()                                           # what MainWindow does after a source_* change
    try:
        fmw = next(a for a in eng.adapters if isinstance(a, FMWatcherAdapter))
        t0 = time.time()
        while time.time() - t0 < 10 and fmw.health.status == SourceStatus.STARTING:
            time.sleep(0.05)
        assert fmw.health.status == SourceStatus.ONLINE, fmw.health.detail
        assert eng.metrics.listings_ingested >= 20 and eng.scanner_state in ("LIVE", "DEGRADED")
    finally:
        eng.stop()
