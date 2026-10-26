"""UI responsiveness watchdog: a 200 ms timer measures how late it fires. Lag above STALL_MS is logged as
UI_STALL with the duration and what the main thread was last doing, and counted in metrics."""
from __future__ import annotations

import logging
import time
from typing import Callable, Optional

from PySide6.QtCore import QObject, QTimer

log = logging.getLogger(__name__)

PERIOD_MS = 200
STALL_MS = 300


class UiWatchdog(QObject):
    def __init__(self, metrics, parent: Optional[QObject] = None) -> None:
        super().__init__(parent)
        self.metrics = metrics
        self.last_activity = "idle"
        self.worst_ms = 0.0
        self.last_lag_ms = 0.0
        self._t = time.perf_counter()
        self.timer = QTimer(self)
        self.timer.setTimerType(__import__("PySide6.QtCore", fromlist=["Qt"]).Qt.PreciseTimer)
        self.timer.timeout.connect(self._tick)
        self.timer.start(PERIOD_MS)

    def mark(self, what: str) -> None:
        self.last_activity = what

    def timed(self, what: str) -> Callable:
        """Decorator-ish helper: wrap a UI slot so the stall log can name it."""
        def deco(fn):
            def inner(*a, **k):
                self.last_activity = what
                t0 = time.perf_counter()
                try:
                    return fn(*a, **k)
                finally:
                    dt = (time.perf_counter() - t0) * 1000
                    if dt > STALL_MS:
                        log.warning("UI_STALL %.0f ms inside %s", dt, what)
            return inner
        return deco

    def _tick(self) -> None:
        now = time.perf_counter()
        lag = (now - self._t) * 1000 - PERIOD_MS
        self._t = now
        if lag < 0:
            lag = 0.0
        self.last_lag_ms = lag
        self.metrics.ui_lag_ms.add(lag)
        if lag > self.worst_ms:
            self.worst_ms = lag
        if lag > STALL_MS:
            self.metrics.ui_stalls += 1
            log.warning("UI_STALL %.0f ms (last activity: %s)", lag, self.last_activity)
        self.last_activity = "idle"
