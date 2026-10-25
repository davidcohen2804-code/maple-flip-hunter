"""Headless runner: `python -m maple_flip_hunter.headless --seconds 120`
Runs the engine without a GUI and prints ingestion/latency measurements as JSON.
Used by the live integration tests and the CI measurement job."""
from __future__ import annotations

import argparse
import json
import logging
import sys
import time

from .collector.engine import MarketEngine
from .config import Settings
from .db import Database
from .logging_setup import setup_logging
from .notifications import Notifier


def run(seconds: float, db: str = ":memory:", audio: bool = False) -> dict:
    setup_logging(logging.INFO)
    settings = Settings()
    engine = MarketEngine(settings, Database(db), Notifier(enable_audio=audio))
    alerts: list[dict] = []
    opps: list[dict] = []
    engine.on("alert", lambda o, lvl, t, b: alerts.append({"level": lvl, "item": o.listing.item_name, "buy": o.listing.price,
                                                            "quick": o.valuation.quick, "net": o.valuation.net_profit,
                                                            "source_to_alert_s": (o.notified_at or 0) - o.listing.source_seen_at,
                                                            "location": o.listing.location, "shop": o.listing.seller}))
    engine.on("opportunity", lambda o: opps.append({"level": o.level.value, "item": o.listing.item_name, "buy": o.listing.price,
                                                   "quick": o.valuation.quick, "net": o.valuation.net_profit,
                                                   "conf": o.valuation.confidence, "age_s": round(o.listing.source_age_s)}))
    engine.start()
    t_end = time.time() + seconds
    while time.time() < t_end:
        time.sleep(1)
    engine._refresh_state()
    final_state = engine.scanner_state          # capture BEFORE stop() (stop always reports OFFLINE)
    engine.stop()
    return {"seconds": seconds, "scanner_state": final_state,
            "health": [h.model_dump() for h in engine.health()], "metrics": engine.metrics.snapshot(),
            "coverage": engine.coverage(), "opportunities": opps[:50], "alerts": alerts}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=90)
    ap.add_argument("--db", default=":memory:")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = run(a.seconds, a.db)
    txt = json.dumps(res, indent=2, default=str)
    if a.out:
        with open(a.out, "w", encoding="utf-8") as f:
            f.write(txt)
    print(txt)
    return 0


if __name__ == "__main__":
    sys.exit(main())
