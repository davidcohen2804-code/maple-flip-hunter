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

    from maple_flip_hunter.adapters.base import FetchError

    a = CozyAdapter()
    t0 = time.perf_counter()
    try:
        shops = run(a.fetch_json("/api/shops", {"page": 1, "size": 100, "sort": "last_at", "dir": "desc"}))
    except FetchError as exc:
        _save("cozy", {"status": "BLOCKED" if "403" in str(exc) else "UNREACHABLE", "error": str(exc),
                       "note": "cozy.markets refused this network (Cloudflare). Not bypassed. Re-test from a home PC with run_live_check.bat."})
        run(a.aclose())
        pytest.skip(f"cozy.markets BLOCKED from this network: {exc}")
    ms = (time.perf_counter() - t0) * 1000
    now = time.time()
    ages = sorted(now - float(s["last_at"]) for s in shops["rows"])
    deals = run(a.fetch_json("/api/deals", {"page": 1, "size": 100, "kind": "under-fm", "seen": "1h", "sort": "at", "dir": "desc"}))
    dages = sorted(now - float(r["at"]) for r in deals["rows"])
    listings = run(a.poll())
    _save("cozy", {"status": "OK", "http_ms": round(ms), "newest_shop_age_s": round(ages[0]) if ages else None,
                   "median_shop_age_s_top100": round(ages[len(ages) // 2]) if ages else None,
                   "newest_deal_age_s": round(dages[0]) if dages else None, "deals_total_1h": deals.get("total"),
                   "first_poll_listings": len(listings), "health": a.health.model_dump()})
    run(a.aclose())
    assert shops["rows"] and ages[0] < 1800          # newest scan under 30 minutes old
    assert a.health.status.value in ("ONLINE", "STALE")


def test_msw_not_polled_without_permission_and_terms_still_forbid_apps():
    """maplestory.watch Terms (2026-10-27): /api/ is not public, apps may not read it. The adapter must stay
    UNSUPPORTED and send nothing. We only read robots.txt (allowed for everyone) to record the current policy."""
    from maple_flip_hunter.adapters import MapleStoryWatchAdapter

    a = MapleStoryWatchAdapter()
    rows = run(a.poll())
    assert rows == [] and a.health.status.value == "UNSUPPORTED" and a.health.requests == 0 and a.health.errors == 0
    allowed = run(a._robots_allow())
    _save("msw", {"polled": False, "status": a.health.status.value, "robots_allows_api": allowed})
    run(a.aclose())
    assert allowed is False, "robots.txt no longer disallows /api/ — re-read the Terms before enabling anything"


def test_end_to_end_live_run_measures_alert_latency():
    from maple_flip_hunter.headless import run as headless_run

    seconds = float(os.environ.get("MFH_LIVE_SECONDS", "75"))
    res = headless_run(seconds)
    _save("end_to_end", {"seconds": seconds, "scanner_state": res["scanner_state"], "metrics": res["metrics"],
                         "coverage": res["coverage"], "n_opportunities": len(res["opportunities"]),
                         "opportunities_sample": res["opportunities"][:10], "alerts": res["alerts"][:10],
                         "health": res["health"]})
    online = [h["name"] for h in res["health"] if h["status"] in ("ONLINE", "STALE")]
    assert online, f"no source delivered data: {[(h['name'], h['status'], h['last_error']) for h in res['health']]}"
    assert res["metrics"]["listings_ingested"] > 0
    assert res["scanner_state"] in ("LIVE", "DEGRADED")
    # no JACKPOT alarm may ever come from a stale observation
    for a in res["alerts"]:
        assert not (a["level"] == "JACKPOT" and a.get("listing_age_s", 0) > 900), a
