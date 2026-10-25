"""Latency / throughput measurements (all in-process, cheap)."""
from __future__ import annotations

import collections
import os
import threading
import time
from typing import Deque, Optional


class Rolling:
    def __init__(self, n: int = 200):
        self.values: Deque[float] = collections.deque(maxlen=n)

    def add(self, v: float) -> None:
        self.values.append(v)

    @property
    def avg(self) -> Optional[float]:
        return sum(self.values) / len(self.values) if self.values else None

    @property
    def p95(self) -> Optional[float]:
        if not self.values:
            return None
        s = sorted(self.values)
        return s[min(len(s) - 1, int(len(s) * 0.95))]

    @property
    def last(self) -> Optional[float]:
        return self.values[-1] if self.values else None

    def stats(self) -> Optional[dict[str, float]]:
        if not self.values:
            return None
        s = sorted(self.values)
        return {"n": len(s), "min": s[0], "median": s[len(s) // 2], "p95": s[min(len(s) - 1, int(len(s) * 0.95))],
                "max": s[-1], "avg": sum(s) / len(s)}


class Metrics:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.started_at = time.time()
        self.listings_ingested = 0
        self.listings_new = 0
        self.listings_repriced = 0
        self.listings_duplicate = 0
        self.evaluations = 0
        self.alerts = 0
        self.duplicate_alerts_suppressed = 0
        self.errors = 0
        self.source_age_at_ingest = Rolling()      # seconds: now - source observation time, when we first see a row
        self.analysis_ms = Rolling()               # local processing time per evaluated listing
        self.source_to_alert_s = Rolling()         # source observation -> notification fired
        self.ingest_to_alert_ms = Rolling()        # our first sight -> notification fired
        self.verifications = 0                      # background availability checks completed
        self.verified_gone = 0                      # rows confirmed REMOVED / SOLD
        self.poll_intervals = collections.defaultdict(Rolling)  # per source: seconds between successful polls
        self._last_poll: dict[str, float] = {}
        self.last_new_listing_at: Optional[float] = None

    def poll_done(self, source: str) -> None:
        now = time.time()
        with self.lock:
            if source in self._last_poll:
                self.poll_intervals[source].add(now - self._last_poll[source])
            self._last_poll[source] = now

    @staticmethod
    def process_usage() -> dict[str, float]:
        out: dict[str, float] = {}
        try:
            import psutil  # type: ignore

            p = psutil.Process(os.getpid())
            out["rss_mb"] = p.memory_info().rss / 1e6
            out["cpu_percent"] = p.cpu_percent(interval=None)
        except Exception:  # noqa: BLE001
            try:
                import resource

                out["rss_mb"] = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0
            except Exception:  # noqa: BLE001
                pass
        return out

    def snapshot(self) -> dict[str, object]:
        with self.lock:
            return {
                "uptime_s": time.time() - self.started_at,
                "listings_ingested": self.listings_ingested,
                "listings_new": self.listings_new,
                "listings_repriced": self.listings_repriced,
                "listings_duplicate": self.listings_duplicate,
                "evaluations": self.evaluations,
                "alerts": self.alerts,
                "duplicate_alerts_suppressed": self.duplicate_alerts_suppressed,
                "errors": self.errors,
                "source_age_at_ingest_avg_s": self.source_age_at_ingest.avg,
                "analysis_ms_avg": self.analysis_ms.avg,
                "analysis_ms_p95": self.analysis_ms.p95,
                "source_to_alert_avg_s": self.source_to_alert_s.avg,
                "source_to_alert_s": self.source_to_alert_s.stats(),
                "source_age_at_ingest_s": self.source_age_at_ingest.stats(),
                "ingest_to_alert_avg_ms": self.ingest_to_alert_ms.avg,
                "verifications": self.verifications, "verified_gone": self.verified_gone,
                "poll_interval_avg_s": {k: v.avg for k, v in self.poll_intervals.items()},
                **self.process_usage(),
            }
