"""Free Spot Detector: hut detection on the reference screenshots, occlusion/camera/map primitives, registry
persistence, and the vacancy state machine (PRIME alert, NORMAL silent, no false alerts on map change, camera
scroll, UI occlusion; re-occupation re-arms)."""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from maple_flip_hunter.spots import MonitorConfig, ShopDetector, SpotMonitor, SpotRegistry, occlusion_mask  # noqa: E402
from maple_flip_hunter.spots.vision import camera_shift, hamming, map_signature, to_gray  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "spots"


def img(name):
    return cv2.imread(str(FIX / name))


def test_detector_on_reference_screenshots():
    d = ShopDetector()
    assert len(d.detect(img("fm1_with_gaps.png"))) == 13            # GMS FM1: 13 huts visible
    assert len(d.detect(img("fm_dense_kms.png"))) >= 13              # KMS dense row layout
    assert 4 <= len(d.detect(img("fm2_shop_window_occluding.png"))) <= 6   # only the huts beside the shop window
    assert d.detect(img("kerning_construction_map.png")) == []        # bare map art: no huts, no false positives
    one = d.detect(img("fm1_with_gaps.png"))[0]
    assert one.size > 0 and 0.5 <= one.confidence <= 1.0 and one.cap_w > one.size


def test_occlusion_camera_and_map_primitives():
    base = img("fm1_with_gaps.png")
    panel = base.copy()
    cv2.rectangle(panel, (250, 100), (850, 460), (235, 240, 245), -1)
    m = occlusion_mask(panel, base)
    assert 0.3 < m.mean() / 255 < 0.7                                  # the big UI panel is masked …
    d = ShopDetector().detect(base)[0]
    vac = base.copy()
    x, y, w, h = d.box
    vac[max(0, y):y + h, max(0, x):x + w] = (230, 200, 150)
    assert occlusion_mask(vac, base).mean() == 0                        # … a vanished hut is not an occluder
    assert occlusion_mask(base).mean() == 0                             # no reference → nothing is called a panel
    dx, dy, resp = camera_shift(to_gray(base), to_gray(np.roll(base, 37, axis=1)))
    assert 30 < dx < 45 and abs(dy) < 5 and resp > 0.5
    assert hamming(map_signature(base), map_signature(base)) == 0
    assert hamming(map_signature(base), map_signature(img("fm_dense_kms.png"))) > 12


def _registry():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return SpotRegistry(conn)


def test_registry_persistence_and_matching(tmp_path):
    db = tmp_path / "s.sqlite3"
    conn = sqlite3.connect(str(db)); conn.row_factory = sqlite3.Row
    reg = SpotRegistry(conn)
    s = reg.add("Free Market", 1, 3, 400, 300, rating="PRIME", manual=True, note="entrance left")
    assert reg.find("Free Market", 1, 3, 420, 310) is s and reg.find("Free Market", 1, 4, 400, 300) is None
    assert reg.add("Free Market", 1, 3, 410, 305) is s                  # same spot, not a duplicate
    reg.set_rating(s.spot_id, "GOOD")
    conn.close()
    conn2 = sqlite3.connect(str(db)); conn2.row_factory = sqlite3.Row
    reg2 = SpotRegistry(conn2)
    s2 = reg2.spots[s.spot_id]
    assert s2.rating == "GOOD" and s2.manual and s2.note == "entrance left" and s2.location_text() == "Free Market · FM 1 · CH 3"
    assert [e["kind"] for e in reg2.events()] == ["rated"]


class Scene:
    """Synthetic scene built from the real FM1 screenshot: huts can be removed, panels drawn, camera scrolled."""

    def __init__(self):
        self.base = img("fm1_with_gaps.png")
        self.dets = ShopDetector().detect(self.base)

    def frame(self, remove=(), panel=False, shift=0, other_map=False):
        f = img("fm_dense_kms.png") if other_map else self.base.copy()
        for i in remove:
            x, y, w, h = self.dets[i].box
            f[max(0, y):y + h, max(0, x):x + w] = (230, 200, 150)
        if panel:
            cv2.rectangle(f, (250, 100), (850, 460), (235, 240, 245), -1)
        if shift:
            f = np.roll(f, shift, axis=1)
        return f


def _monitor(scene, ratings=("PRIME",)):
    reg = _registry()
    events = []
    cfg = MonitorConfig(map_name="Free Market", room=1, channel=3, alert_ratings=ratings, vacate_confirm_s=15, vacate_confirm_frames=4,
                        occupied_confirm_frames=2)
    mon = SpotMonitor(reg, cfg, capture=lambda: None, on_event=events.append)
    t = [1_000_000.0]

    def step(frame, dt=5.0):
        t[0] += dt
        return mon.process(frame, now=t[0])
    for _ in range(3):                                   # learn the layout: all huts OCCUPIED
        step(scene.frame())
    return mon, reg, events, step


def test_prime_spot_vacates_and_alerts_normal_stays_silent():
    sc = Scene()
    mon, reg, events, step = _monitor(sc)
    spots = reg.in_scope("Free Market", 1, 3)
    assert len(spots) == 13 and all(s.state == "OCCUPIED" for s in spots)
    prime = reg.find("Free Market", 1, 3, sc.dets[0].x, sc.dets[0].y)
    normal = reg.find("Free Market", 1, 3, sc.dets[5].x, sc.dets[5].y)
    reg.set_rating(prime.spot_id, "PRIME")
    events.clear()
    for _ in range(3):                                   # gone for 15 s / 3 frames: not yet
        step(sc.frame(remove=(0, 5)))
    assert not [e for e in events if e.kind == "FREE"]
    step(sc.frame(remove=(0, 5)))                        # 20 s / 4 clean frames → FREE
    free = [e for e in events if e.kind == "FREE"]
    alerts = [e for e in events if e.kind == "ALERT"]
    assert {e.spot.spot_id for e in free} == {prime.spot_id, normal.spot_id}
    assert [e.spot.spot_id for e in alerts] == [prime.spot_id] and alerts[0].detail == "PRIME SPOT AVAILABLE"
    assert 0.6 <= alerts[0].confidence <= 0.99 and alerts[0].screenshot is not None
    assert prime.vacate_count == 1 and prime.occupied_runs == 1
    # still free: no second alert; hut comes back → OCCUPIED, retake time measured, alert re-armed
    step(sc.frame(remove=(0, 5)))
    assert len([e for e in events if e.kind == "ALERT"]) == 1
    for _ in range(2):
        step(sc.frame())
    assert prime.state == "OCCUPIED" and prime.retake_runs == 1
    events.clear()
    for _ in range(5):
        step(sc.frame(remove=(0,)))
    assert len([e for e in events if e.kind == "ALERT"]) == 1          # vacated again → alerts again
    assert reg.events(1)[0]["kind"] == "alert"
    # GOOD included in the alert set
    mon.cfg.alert_ratings = ("PRIME", "GOOD")
    reg.set_rating(normal.spot_id, "GOOD")


def test_no_false_alert_on_occlusion_camera_scroll_or_map_change():
    sc = Scene()
    mon, reg, events, step = _monitor(sc)
    prime = reg.find("Free Market", 1, 3, sc.dets[2].x, sc.dets[2].y)
    reg.set_rating(prime.spot_id, "PRIME")
    events.clear()
    for _ in range(8):                                   # a shop window covers the spot for 40 s
        step(sc.frame(panel=True))
    assert not [e for e in events if e.kind in ("FREE", "ALERT")] and prime.state == "OCCUPIED"
    for _ in range(8):                                   # camera scrolls every frame: no decisions
        step(sc.frame(remove=(2,), shift=40 * (_ + 1)))
    assert not [e for e in events if e.kind in ("FREE", "ALERT")] and not mon.context_ok
    step(sc.frame(other_map=True))                       # channel/map change: context reset
    assert [e.kind for e in events][-1] == "CONTEXT" and "minimap differs" in events[-1].detail
    for _ in range(8):
        step(sc.frame(other_map=True))
    assert not [e for e in events if e.kind in ("FREE", "ALERT")]
    # scope filter: alerts only in the chosen channels
    mon.cfg.scopes = [{"map": "Free Market", "room": 1, "channels": [7], "enabled": True}]
    assert not mon.scope_enabled("Free Market", 1, 3) and mon.scope_enabled("Free Market", 1, 7)


def test_monitor_thread_start_stop_rate_limited():
    sc = Scene()
    reg = _registry()
    frames = []
    mon = SpotMonitor(reg, MonitorConfig(map_name="FM", room=1, channel=1, fps=5), capture=lambda: (frames.append(1), sc.frame())[1])
    mon.start()
    assert mon.running and mon.state == "ONLINE"
    time.sleep(1.2)
    mon.stop()
    assert not mon.running and mon.state == "OFFLINE"
    assert 3 <= len(frames) <= 8 and mon.last_frame_ms < 500           # ≈ 5 fps, bounded CPU
    n = len(frames)
    time.sleep(0.4)
    assert len(frames) == n                                             # really stopped
    mon.start(); mon.start()                                            # idempotent
    time.sleep(0.3); mon.stop()
