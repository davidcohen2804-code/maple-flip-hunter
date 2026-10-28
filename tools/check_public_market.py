"""Live, read-only check of the public HTML source; stale data is reported, never called fresh."""
import asyncio
import json
import sys
import time
from pathlib import Path

from maple_flip_hunter.adapters.public_market import PublicMarketAdapter
from maple_flip_hunter.collector.engine import MarketEngine
from maple_flip_hunter.config import Settings
from maple_flip_hunter.db import Database


async def check():
    adapter = PublicMarketAdapter()
    db = Database(":memory:")
    engine = MarketEngine(Settings(), db, adapters=[adapter])
    alerts = []
    engine.on("alert", lambda *args: alerts.append(args))
    try:
        rows = await adapter.poll()
        await engine.ingest(rows)
        result = {"checked_at": time.time(), "source": adapter.name, "status": adapter.health.status.value,
                  "rows": len(rows), "requests": adapter.health.requests,
                  "newest_observation_age_s": adapter.health.newest_data_age_s,
                  "alerts": len(alerts), "detail": adapter.health.detail,
                  "coverage": "Rows rendered on the public /fm page only; not the entire market"}
        if not rows:
            raise RuntimeError("No usable public market rows were received")
        if all(row.source_age_s > engine.settings.max_listing_age_s for row in rows) and alerts:
            raise RuntimeError("Stale public data generated an alert")
        return result
    finally:
        await adapter.aclose()
        db.close()


if __name__ == "__main__":
    result = asyncio.run(check())
    print(json.dumps(result, indent=2))
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2), encoding="utf-8")
