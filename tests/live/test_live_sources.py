"""LIVE integration tests - hit the real market sources. Skipped unless MFH_LIVE=1.
They measure source freshness and end-to-end alert latency and write
tests/live/last_live_measurements.json for the documentation."""
from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.environ.get("MFH_LIVE") != "1", reason="set MFH_LIVE=1 to run live source tests")

OUT = Path(__file__).parent / "last_live_measurements.json"


def run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _save(key: str, value):
    data = json.loads(OUT.read_text()) if OUT.exists() else {}
    data[key] = value
    data["measured_at"] = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    OUT.write_text(json.dumps(data, indent=2, default=str))


def test_cozy_live_freshness():
    from maple_flip_hunter.adapters import CozyAdapter

    a = CozyAdapter()
    t0 = time.perf_counter()
    shops = run(a.fetch_json("/api/shops", {"page": 1, "size": 100, "sort": "last_at", "dir": "desc"}))
    ms = (time.perf_counter() - t0) * 1000
    now = time.time()
    ages = sorted(now - float(s["last_at"]) for s in shops["rows"])
    deals = run(a.fetch_json("/api/deals", {"page": 1, "size": 100, "kind": "under-fm", "seen": "1h", "sort": "at", "dir": "desc"}))
    dages = sorted(now - float(r["at"]) for r in deals["rows"])
    listings = run(a.poll())
    _save("cozy", {"http_ms": round(ms), "newest_shop_age_s": round(ages[0]) if ages else None,
                   "median_shop_age_s_top100": round(ages[len(ages) // 2]) if ages else None,
                   "newest_deal_age_s": round(dages[0]) if dages else None, "deals_total_1h": deals.get("total"),
                   "first_poll_listings": len(listings), "health": a.health.model_dump()})
    run(a.aclose())
    assert shops["rows"] and ages[0] < 1800          # newest scan under 30 minutes old
    assert a.health.status.value in ("ONLINE", "STALE")


def test_msw_live_freshness():
    from maple_flip_hunter.adapters import MapleStoryWatchAdapter

    a = MapleStoryWatchAdapter()
    t0 = time.perf_counter()
    n = run(a.refresh_index())
    ms = (time.perf_counter() - t0) * 1000
    now = time.time()
    newest = max(float(i.get("seen") or 0) for i in a.index.values())
    _save("msw", {"http_ms": round(ms), "index_items": n, "index_age_s": round(now - a.index_at),
                  "newest_listing_age_s": round(now - newest)})
    run(a.aclose())
    assert n > 500 and now - a.index_at < 3600


def test_end_to_end_live_run_measures_alert_latency():
    from maple_flip_hunter.headless import run as headless_run

    seconds = float(os.environ.get("MFH_LIVE_SECONDS", "75"))
    res = headless_run(seconds)
    _save("end_to_end", {"seconds": seconds, "scanner_state": res["scanner_state"], "metrics": res["metrics"],
                         "coverage": res["coverage"], "n_opportunities": len(res["opportunities"]),
                         "opportunities_sample": res["opportunities"][:10], "alerts": res["alerts"][:10],
                         "health": res["health"]})
    assert res["metrics"]["listings_ingested"] > 0
    assert res["scanner_state"] in ("LIVE", "DEGRADED")
