"""Free Spot Detector — vision primitives (local image analysis only; never touches the game).

Hired-merchant shops are mushroom huts: orange cap, white skirt with a RED STAR, wooden name board. The red star is
small, saturated and always visible when the hut is, so detection = red-star blobs that have a white skirt around
them and an orange cap above. Verified on the reference screenshots (GMS FM1, KMS dense FM, FM2 with a shop window
open): 13 / 14 / 5 huts found, no false positives on UI, characters or scenery.

Also here: an occlusion mask (large low-saturation UI panels such as the shop window, which hide whatever is behind
them — a hut under a panel is UNKNOWN, never 'free'), the camera shift between two frames (phase correlation, so a
scrolled camera is never mistaken for vanished shops), and a map signature from the minimap corner."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np

try:
    import cv2
except Exception:  # pragma: no cover - cv2 missing
    cv2 = None  # type: ignore[assignment]


@dataclass
class Detection:
    x: int            # star centre (screen px) — the hut's anchor point
    y: int
    size: int         # star width in px (scales with the client's zoom)
    cap_x: int        # bounding box of the whole hut (approx, from the star size)
    cap_y: int
    cap_w: int
    cap_h: int
    confidence: float

    @property
    def box(self) -> tuple[int, int, int, int]:
        return self.cap_x, self.cap_y, self.cap_w, self.cap_h


class ShopDetector:
    """Detect hired-merchant huts in a BGR frame. Pure numpy/cv2; ~15 ms on a 1280×720 frame."""

    def __init__(self, min_star: int = 10, max_star: int = 60) -> None:
        self.min_star, self.max_star = min_star, max_star

    def detect(self, frame_bgr: np.ndarray) -> list[Detection]:
        if cv2 is None:
            return []
        hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
        red = cv2.inRange(hsv, (0, 140, 120), (8, 255, 255)) | cv2.inRange(hsv, (172, 140, 120), (180, 255, 255))
        red = cv2.morphologyEx(red, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8))
        orange = cv2.inRange(hsv, (10, 120, 150), (28, 255, 255))
        white = cv2.inRange(hsv, (0, 0, 200), (180, 40, 255))
        n, _lab, stats, cent = cv2.connectedComponentsWithStats(red)
        H, W = red.shape
        out: list[Detection] = []
        for i in range(1, n):
            x, y, w, h, a = (int(v) for v in stats[i])
            if not (80 <= a <= 1500 and 0.6 <= w / max(1, h) <= 1.7 and self.min_star <= w <= self.max_star):
                continue
            cx, cy = int(cent[i][0]), int(cent[i][1])
            s = w
            roi_w = white[max(0, cy - s):min(H, cy + s), max(0, cx - 3 * s):min(W, cx + 3 * s)]
            roi_o = orange[max(0, cy - 5 * s):max(0, cy - int(1.5 * s)), max(0, cx - 3 * s):min(W, cx + 3 * s)]
            wf = float(roi_w.mean() / 255) if roi_w.size else 0.0
            of = float(roi_o.mean() / 255) if roi_o.size else 0.0
            if wf > 0.25 and of > 0.25:
                conf = min(1.0, 0.5 + 0.5 * min(wf, 0.5) + 0.5 * min(of, 0.5))
                out.append(Detection(cx, cy, s, cx - 3 * s, cy - 6 * s, 6 * s, 8 * s, round(conf, 2)))
        out.sort(key=lambda d: (d.y, d.x))
        return out


def occlusion_mask(frame_bgr: np.ndarray, reference_bgr: Optional[np.ndarray] = None,
                   min_w: int = 200, min_h: int = 150) -> np.ndarray:
    """uint8 mask (255 = covered by a UI panel such as the shop window, an NPC dialog or a big system window).

    Temporal rule: compared with the reference frame (same camera position), a panel is a LARGE region
    (≥ min_w × min_h — far bigger than any hut, which is ≈ 120 × 160 px) that changed and is now flat and
    low-saturation (white/grey UI). A hut that really vanished leaves a hut-sized change, never a panel-sized one;
    a channel/map switch changes everything and is caught by the map signature instead. Without a reference nothing
    can be called a panel (sky and clouds are flat too), so the mask is empty."""
    H, W = frame_bgr.shape[:2]
    mask = np.zeros((H, W), np.uint8)
    if cv2 is None or reference_bgr is None or reference_bgr.shape != frame_bgr.shape:
        return mask
    diff = cv2.absdiff(frame_bgr, reference_bgr).max(axis=2)
    changed = (diff > 40).astype(np.uint8) * 255
    changed = cv2.morphologyEx(changed, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    hsv = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2HSV)
    flat = cv2.inRange(hsv, (0, 0, 150), (180, 60, 255))
    n, _lab, stats, _ = cv2.connectedComponentsWithStats(changed)
    for i in range(1, n):
        x, y, w, h, a = (int(v) for v in stats[i])
        if w < min_w or h < min_h:
            continue
        region_flat = flat[y:y + h, x:x + w].mean() / 255
        if region_flat >= 0.45:
            mask[y:y + h, x:x + w] = 255
    return mask


def camera_shift(prev_gray: np.ndarray, cur_gray: np.ndarray) -> tuple[float, float, float]:
    """(dx, dy, response) between two frames via phase correlation on a downscaled copy. response ≈ 1 = confident."""
    if cv2 is None:
        return 0.0, 0.0, 0.0
    a = cv2.resize(prev_gray, (prev_gray.shape[1] // 4, prev_gray.shape[0] // 4)).astype(np.float32)
    b = cv2.resize(cur_gray, (cur_gray.shape[1] // 4, cur_gray.shape[0] // 4)).astype(np.float32)
    (dx, dy), resp = cv2.phaseCorrelate(a, b)
    return dx * 4, dy * 4, float(resp)


def to_gray(frame_bgr: np.ndarray) -> np.ndarray:
    return cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY) if cv2 is not None else frame_bgr[..., 0]


def map_signature(frame_bgr: np.ndarray, region=(0, 0, 190, 130)) -> int:
    """64-bit dHash of the minimap corner: changes when the map (or the minimap view) changes."""
    if cv2 is None:
        return 0
    x, y, w, h = region
    roi = cv2.cvtColor(frame_bgr[y:y + h, x:x + w], cv2.COLOR_BGR2GRAY)
    small = cv2.resize(roi, (9, 8), interpolation=cv2.INTER_AREA)
    bits = small[:, 1:] > small[:, :-1]
    return int("".join("1" if b else "0" for b in bits.flatten()), 2)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")
