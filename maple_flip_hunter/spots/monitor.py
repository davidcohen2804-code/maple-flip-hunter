"""Free Spot Detector — the monitoring state machine. Runs in a background thread at a bounded frame rate; frames
come from a capture callable (the GUI grabs the screen with QScreen — ~10 ms — and hands the pixels over).

Per frame:
  1. map signature (minimap corner) must match the monitored map; a change → context reset, nothing is "free".
  2. camera shift vs previous frame: large → the view scrolled; positions are re-anchored, no vacancy decisions.
  3. occlusion mask vs the reference frame (same camera): huts under a UI panel are UNKNOWN, not free.
  4. detections ↔ registered spots (auto-registered on first sight, NORMAL unless the user rated them).
  5. a spot is FREE only after it was OCCUPIED and then NOT detected for ≥ `vacate_confirm_s` across ≥
     `vacate_confirm_frames` consecutive clean frames (no occlusion on it, camera stable, same map). A hut seen again
     cancels the countdown. FREE → alert if the spot's rating is in the alert set and the (map, room, channel) is in
     the user's scope; one alert per vacancy (re-armed when the spot is retaken).
No game input is ever generated: this only looks."""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np

from .registry import Spot, SpotRegistry
from .vision import Detection, ShopDetector, camera_shift, hamming, map_signature, occlusion_mask, to_gray

log = logging.getLogger(__name__)


@dataclass
class SpotEvent:
    kind: str                 # FREE | OCCUPIED | ALERT | CONTEXT
    spot: Optional[Spot]
    at: float
    detail: str = ""
    confidence: float = 0.0
    screenshot: Optional[np.ndarray] = None


@dataclass
class MonitorConfig:
    map_name: str = ""
    room: Optional[int] = None
    channel: Optional[int] = None
    alert_ratings: tuple[str, ...] = ("PRIME",)
    fps: float = 1.0
    vacate_confirm_s: float = 20.0
    vacate_confirm_frames: int = 8
    occupied_confirm_frames: int = 3
    camera_stable_px: float = 6.0
    map_match_bits: int = 12
    alert_cooldown_s: float = 300.0
    scopes: list[dict] = field(default_factory=list)   # [{"map": "...", "room": 1, "channel": 3, "enabled": True}]


class SpotMonitor:
    def __init__(self, registry: SpotRegistry, config: MonitorConfig, capture: Callable[[], Optional[np.ndarray]],
                 on_event: Optional[Callable[[SpotEvent], None]] = None) -> None:
        self.reg = registry
        self.cfg = config
        self.capture = capture
        self.on_event = on_event
        self.detector = ShopDetector()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.state = "OFFLINE"            # ONLINE | OFFLINE | ERROR
        self.error = ""
        self.frames = 0
        self.last_frame_ms = 0.0
        self.last_detections: list[Detection] = []
        self.last_frame: Optional[np.ndarray] = None
        self.context_ok = False
        self.context_note = "not started"
        # per-spot tracking (runtime only)
        self._seen_run: dict[str, int] = {}         # consecutive frames with a hut
        self._missing_run: dict[str, int] = {}      # consecutive clean frames without a hut
        self._missing_since: dict[str, float] = {}
        self._alerted_at: dict[str, float] = {}
        self._occupied_since: dict[str, float] = {}
        self._free_since: dict[str, float] = {}
        self._ref: Optional[np.ndarray] = None       # reference frame for occlusion (same camera position)
        self._prev_gray: Optional[np.ndarray] = None
        self._map_sig: Optional[int] = None
        self._cam_dx = 0.0
        self._cam_dy = 0.0

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self.state, self.error = "ONLINE", ""
        self._thread = threading.Thread(target=self._loop, name="mfh-spot-monitor", daemon=True)
        self._thread.start()
        log.info("SPOT_MONITOR start map=%r room=%s ch=%s fps=%s", self.cfg.map_name, self.cfg.room, self.cfg.channel, self.cfg.fps)

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(3)
        self.state = "OFFLINE"
        self.context_note = "stopped"
        log.info("SPOT_MONITOR stop")

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _loop(self) -> None:
        period = 1.0 / max(0.2, self.cfg.fps)
        while not self._stop.is_set():
            t0 = time.perf_counter()
            try:
                frame = self.capture()
                if frame is not None:
                    self.process(frame)
            except Exception as exc:  # noqa: BLE001
                self.state, self.error = "ERROR", f"{exc.__class__.__name__}: {exc}"
                log.exception("SPOT_MONITOR frame failed")
            self.last_frame_ms = (time.perf_counter() - t0) * 1000
            self._stop.wait(max(0.05, period - (time.perf_counter() - t0)))

    # ------------------------------------------------------------------ scope
    def scope_enabled(self, map_name: str, room: Optional[int], channel: Optional[int]) -> bool:
        if not self.cfg.scopes:
            return True
        for sc in self.cfg.scopes:
            if not sc.get("enabled", True):
                continue
            if sc.get("map") and sc["map"].lower() != (map_name or "").lower():
                continue
            if sc.get("room") is not None and sc["room"] != room:
                continue
            chs = sc.get("channels")
            if chs and channel not in chs:
                continue
            return True
        return False

    # ------------------------------------------------------------------ core
    def process(self, frame: np.ndarray, now: Optional[float] = None) -> list[SpotEvent]:
        """Process one BGR frame; returns the events it produced (also delivered to on_event)."""
        now = now or time.time()
        self.frames += 1
        self.last_frame = frame
        events: list[SpotEvent] = []
        gray = to_gray(frame)
        # 1) same map?
        sig = map_signature(frame)
        if self._map_sig is None:
            self._map_sig = sig                       # the monitored map's minimap, learned on the first frame
        elif hamming(sig, self._map_sig) > self.cfg.map_match_bits:
            if self.context_ok or self.context_note != "away from the monitored map (minimap differs) — no decisions":
                self._reset_context("away from the monitored map (minimap differs) — no decisions")
                events.append(SpotEvent("CONTEXT", None, now, self.context_note))
            self._prev_gray = gray
            self._ref = None
            return self._emit(events)
        # 2) camera stable?
        if self._prev_gray is not None:
            dx, dy, resp = camera_shift(self._prev_gray, gray)
            if abs(dx) > self.cfg.camera_stable_px or abs(dy) > self.cfg.camera_stable_px:
                self._cam_dx += dx
                self._cam_dy += dy
                self._prev_gray = gray
                self._ref = None
                self.context_ok, self.context_note = False, f"camera moved ({dx:+.0f},{dy:+.0f}) — waiting for a still view"
                self._missing_run.clear()
                return self._emit(events)
        self._prev_gray = gray
        if self._ref is None:
            self._ref = frame.copy()
        self.context_ok, self.context_note = True, "map and camera stable"
        # 3) occlusion + detections
        occ = occlusion_mask(frame, self._ref)
        dets = self.detector.detect(frame)
        self.last_detections = dets
        H, W = gray.shape
        seen_ids: set[str] = set()
        for d in dets:
            sx, sy = int(d.x - self._cam_dx), int(d.y - self._cam_dy)
            spot = self.reg.find(self.cfg.map_name, self.cfg.room, self.cfg.channel, sx, sy)
            if spot is None:
                spot = self.reg.add(self.cfg.map_name, self.cfg.room, self.cfg.channel, sx, sy)
                self.reg.event(spot.spot_id, "discovered", f"auto at ({sx},{sy})", d.confidence)
            seen_ids.add(spot.spot_id)
            self._seen_run[spot.spot_id] = self._seen_run.get(spot.spot_id, 0) + 1
            self._missing_run.pop(spot.spot_id, None)
            self._missing_since.pop(spot.spot_id, None)
            spot.last_seen_at = now
            if spot.state != "OCCUPIED" and self._seen_run[spot.spot_id] >= self.cfg.occupied_confirm_frames:
                if spot.state == "FREE" and spot.spot_id in self._free_since:
                    spot.retake_s_total += now - self._free_since.pop(spot.spot_id)
                    spot.retake_runs += 1
                spot.state, spot.last_change_at = "OCCUPIED", now
                self._occupied_since[spot.spot_id] = now
                self._alerted_at.pop(spot.spot_id, None)
                self.reg.save(spot)
                self.reg.event(spot.spot_id, "occupied", "", d.confidence)
                events.append(SpotEvent("OCCUPIED", spot, now, "hut seen", d.confidence))
        # 4) spots not seen this frame
        for spot in self.reg.in_scope(self.cfg.map_name, self.cfg.room, self.cfg.channel):
            if spot.spot_id in seen_ids:
                continue
            self._seen_run[spot.spot_id] = 0
            px, py = int(spot.x + self._cam_dx), int(spot.y + self._cam_dy)
            if not (0 <= px < W and 0 <= py < H):
                continue                                   # off-screen: unknown, not free
            x0, y0, x1, y1 = max(0, px - 60), max(0, py - 120), min(W, px + 60), min(H, py + 40)
            if occ[y0:y1, x0:x1].mean() > 25:
                self._missing_run[spot.spot_id] = 0        # under a panel: can't tell
                continue
            if spot.state != "OCCUPIED":
                continue
            self._missing_run[spot.spot_id] = self._missing_run.get(spot.spot_id, 0) + 1
            self._missing_since.setdefault(spot.spot_id, now)
            gone_for = now - self._missing_since[spot.spot_id]
            if self._missing_run[spot.spot_id] >= self.cfg.vacate_confirm_frames and gone_for >= self.cfg.vacate_confirm_s:
                conf = min(0.99, 0.6 + 0.02 * self._missing_run[spot.spot_id] + (0.1 if gone_for > 2 * self.cfg.vacate_confirm_s else 0))
                spot.state, spot.last_change_at = "FREE", now
                spot.vacate_count += 1
                if spot.spot_id in self._occupied_since:
                    spot.occupied_s_total += now - self._occupied_since.pop(spot.spot_id)
                    spot.occupied_runs += 1
                self._free_since[spot.spot_id] = now
                self.reg.save(spot)
                self.reg.event(spot.spot_id, "free", f"not seen for {gone_for:.0f}s over {self._missing_run[spot.spot_id]} clean frames", conf)
                ev = SpotEvent("FREE", spot, now, f"vacated ~{self._missing_since[spot.spot_id]:.0f}", conf, frame.copy())
                events.append(ev)
                if (spot.rating in self.cfg.alert_ratings and self.scope_enabled(spot.map, spot.room, spot.channel)
                        and now - self._alerted_at.get(spot.spot_id, 0) > self.cfg.alert_cooldown_s):
                    self._alerted_at[spot.spot_id] = now
                    self.reg.event(spot.spot_id, "alert", spot.rating, conf)
                    events.append(SpotEvent("ALERT", spot, now, f"{spot.rating} SPOT AVAILABLE", conf, frame.copy()))
        return self._emit(events)

    def _reset_context(self, why: str) -> None:
        self.context_ok, self.context_note = False, why
        self._missing_run.clear()
        self._missing_since.clear()
        self._seen_run.clear()
        self._cam_dx = self._cam_dy = 0.0
        log.info("SPOT_MONITOR context reset: %s", why)

    def _emit(self, events: list[SpotEvent]) -> list[SpotEvent]:
        if self.on_event:
            for e in events:
                try:
                    self.on_event(e)
                except Exception:  # noqa: BLE001
                    log.exception("spot event handler failed")
        return events
