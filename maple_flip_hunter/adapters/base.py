"""Shared adapter plumbing: rate limiting, retries with backoff, health tracking."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any, AsyncIterator, Callable, Optional

import httpx

from .. import USER_AGENT
from ..models import Listing, SourceHealth, SourceStatus

log = logging.getLogger(__name__)


class FetchError(Exception):
    pass


class RateLimited(FetchError):
    def __init__(self, retry_after: float):
        super().__init__(f"rate limited, retry after {retry_after:.0f}s")
        self.retry_after = retry_after


class TokenBucket:
    """Simple async token bucket: `rate` tokens/second, burst up to `capacity`."""

    def __init__(self, rate: float, capacity: int):
        self.rate = rate
        self.capacity = capacity
        self._tokens = float(capacity)
        self._last = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self._tokens = min(self.capacity, self._tokens + (now - self._last) * self.rate)
                self._last = now
                if self._tokens >= 1:
                    self._tokens -= 1
                    return
                await asyncio.sleep((1 - self._tokens) / self.rate)


class BaseAdapter:
    """Base class for market-data adapters.

    Subclasses implement `poll()` (one incremental cycle -> list of Listings) and may
    implement `fetch_evidence()` for item-level comparables.
    """

    name = "base"
    label = "Base"
    base_url = ""
    interval_s = 30.0
    rate = 2.0
    burst = 4
    retry_base_delay = 1.0

    def __init__(self, client: Optional[httpx.AsyncClient] = None, interval_s: Optional[float] = None):
        self._own_client = client is None
        self._extra_headers: dict[str, str] = {}
        self.client = client or self._make_client()
        if interval_s:
            self.interval_s = interval_s
        self.bucket = TokenBucket(self.rate, self.burst)
        self.health = SourceHealth(name=self.name, label=self.label)
        self._stop = asyncio.Event()
        self.closed = False

    def _make_client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT, "Accept": "application/json", **self._extra_headers},
            timeout=httpx.Timeout(15.0, connect=10.0),
            http2=False,
            follow_redirects=True,
        )

    def set_header(self, name: str, value: str) -> None:
        """Header that must survive a client rebuild (e.g. an operator-issued bearer token)."""
        self._extra_headers[name] = value
        self.client.headers[name] = value

    def reset(self) -> None:
        """Prepare the adapter for a (re)start: fresh HTTP client, fresh stop flag, STARTING health.
        Called by the engine on every start so a client closed by a previous stop is never reused."""
        if self._own_client and (self.closed or self.client.is_closed):
            self.client = self._make_client()
        self.closed = False
        self._stop = asyncio.Event()
        self.bucket = TokenBucket(self.rate, self.burst)
        self.health.status = SourceStatus.STARTING
        self.health.detail = ""
        self.health.last_error = None
        self.health.rate_limited_until = None

    # ------------------------------------------------------------------ http
    async def fetch_json(self, path: str, params: Optional[dict[str, Any]] = None, retries: int = 3) -> Any:
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        delay = self.retry_base_delay
        for attempt in range(retries + 1):
            if self.health.rate_limited_until and time.time() < self.health.rate_limited_until:
                raise RateLimited(self.health.rate_limited_until - time.time())
            await self.bucket.acquire()
            if self.closed or self.client.is_closed:
                raise FetchError("adapter is stopped (HTTP client closed)")
            t0 = time.perf_counter()
            try:
                r = await self.client.get(url, params=params)
                self.health.requests += 1
                self.health.last_latency_ms = (time.perf_counter() - t0) * 1000
                if r.status_code == 429:
                    ra = float(r.headers.get("Retry-After") or 60)
                    self.health.rate_limited_until = time.time() + ra
                    self._set_status(SourceStatus.RATE_LIMITED, f"HTTP 429, retry after {ra:.0f}s")
                    raise RateLimited(ra)
                if r.status_code in (401, 403):
                    self._set_status(SourceStatus.AUTH_REQUIRED, f"HTTP {r.status_code}")
                    raise FetchError(f"HTTP {r.status_code} for {url}")
                if r.status_code >= 500:
                    raise FetchError(f"HTTP {r.status_code} for {url}")
                if r.status_code == 404:
                    raise FetchError(f"HTTP 404 for {url}")
                r.raise_for_status()
                if not r.content:
                    return None
                return r.json()
            except RateLimited:
                raise
            except (httpx.TransportError, FetchError, ValueError) as exc:
                self.health.errors += 1
                self.health.last_error = str(exc)[:200]
                if attempt >= retries or isinstance(exc, FetchError) and "404" in str(exc):
                    raise FetchError(str(exc)) from exc
                await asyncio.sleep(delay + random.random() * 0.3)
                delay = min(delay * 2, 20)
        raise FetchError("unreachable")

    # --------------------------------------------------------------- health
    def _set_status(self, status: SourceStatus, detail: str = "") -> None:
        self.health.status = status
        self.health.detail = detail

    def mark_success(self, newest_data_age_s: Optional[float], n_listings: int) -> None:
        self.health.last_success_at = time.time()
        self.health.newest_data_age_s = newest_data_age_s
        self.health.listings_received += n_listings
        self.health.rate_limited_until = None
        if newest_data_age_s is not None and newest_data_age_s > self.stale_after_s():
            self._set_status(SourceStatus.STALE, f"newest observation {newest_data_age_s/60:.0f} min old")
        else:
            self._set_status(SourceStatus.ONLINE, "")

    def mark_failure(self, exc: Exception) -> None:
        self.health.last_error = str(exc)[:200]
        if isinstance(exc, RateLimited):
            self._set_status(SourceStatus.RATE_LIMITED, str(exc))
        elif self.health.status != SourceStatus.AUTH_REQUIRED:
            self._set_status(SourceStatus.UNAVAILABLE, str(exc)[:120])

    def next_delay(self) -> float:
        """Seconds to wait before the next poll (adapters may shorten it while they have a backlog)."""
        return self.interval_s

    def stale_after_s(self) -> float:
        return max(600.0, self.interval_s * 6)

    # ------------------------------------------------------------------ api
    async def poll(self) -> list[Listing]:  # pragma: no cover - abstract
        raise NotImplementedError

    async def run(self, on_listings: Callable[[list[Listing]], Any]) -> None:
        """Poll forever at `interval_s`; one source failing never affects the others."""
        while not self._stop.is_set():
            t0 = time.monotonic()
            try:
                listings = await self.poll()
                if listings:
                    res = on_listings(listings)
                    if asyncio.iscoroutine(res):
                        await res
            except RateLimited as exc:
                self.mark_failure(exc)
                log.warning("%s rate limited: %s", self.name, exc)
                await self._sleep(min(exc.retry_after, 300))
                continue
            except Exception as exc:  # noqa: BLE001
                self.mark_failure(exc)
                log.warning("%s poll failed: %s", self.name, exc)
            elapsed = time.monotonic() - t0
            await self._sleep(max(1.0, self.next_delay() - elapsed))

    async def _sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            pass

    def stop(self) -> None:
        self._stop.set()

    async def aclose(self) -> None:
        """Stop polling and close the HTTP client. The adapter can be used again after reset()."""
        self.stop()
        self.closed = True
        if self._own_client:
            try:
                await self.client.aclose()
            except Exception:  # noqa: BLE001
                pass
