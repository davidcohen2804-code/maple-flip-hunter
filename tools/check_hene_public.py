"""Read-only connection diagnostic; an idle feed is not reported as a fresh listing."""
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

from maple_flip_hunter.adapters.base import FetchError
from maple_flip_hunter.adapters.hene_public import HenePublicAdapter
from maple_flip_hunter.config import Settings


async def check():
    settings = Settings()
    adapter = HenePublicAdapter(max_age_s=settings.max_listing_age_s,
                                max_buy_price=settings.max_buy_price, min_profit=settings.min_net_profit)
    try:
        try:
            rows = await adapter.poll()
            return {'checked_at': time.time(), 'source': adapter.name, 'status': adapter.health.status.value,
                    'new_rows': len(rows), 'requests': adapter.health.requests, 'detail': adapter.health.detail,
                    'fresh_listing_verified': bool(rows), 'max_age_s': 900,
                    'coverage': 'Public deal candidates matching age/budget/profit filters, not the whole market'}
        except (FetchError, httpx.HTTPError) as exc:
            return {'checked_at': time.time(), 'source': adapter.name, 'status': 'UNAVAILABLE',
                    'new_rows': 0, 'fresh_listing_verified': False, 'error': str(exc), 'bypass_attempted': False}
    finally:
        await adapter.aclose()


if __name__ == '__main__':
    result = asyncio.run(check())
    print(json.dumps(result, indent=2))
    if len(sys.argv) > 1:
        target = Path(sys.argv[1])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(result, indent=2), encoding='utf-8')
