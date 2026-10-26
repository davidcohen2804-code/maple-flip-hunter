"""Alert sounds are synthesized at first start (no third-party audio assets, no licensing issues).

Design rule (user requirement): every sound is MELODIC and PLEASANT — bell-like tones with soft attacks and
natural decays, built from notes of a major scale. No sirens, no sweeps, no harsh beeps, no frequencies above
~2.1 kHz, and the peak level is capped (see PEAK) so nothing is startling.
"""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path
from typing import Callable

from ..paths import sounds_dir

RATE = 44100
PEAK = 0.80            # hard cap of the peak amplitude of any generated file (fraction of full scale)
MAX_FREQ_HZ = 2200.0   # no partial above this; keeps everything out of the "piercing" range

# note frequencies (equal temperament)
C5, D5, E5, F5, G5, A5, B5 = 523.25, 587.33, 659.25, 698.46, 783.99, 880.0, 987.77
C6, D6, E6, G6 = 1046.5, 1174.66, 1318.51, 1567.98
C4, E4, G4, A4 = 261.63, 329.63, 392.0, 440.0


def _bell(freq: float, seconds: float, volume: float, partials=((1.0, 1.0), (2.0, 0.45), (3.0, 0.18))) -> list[float]:
    """One bell-like note: a few harmonic partials, 12 ms attack, exponential decay. Returns floats in [-1, 1]."""
    n = int(RATE * seconds)
    out = [0.0] * n
    attack = int(RATE * 0.012)
    for mult, amp in partials:
        f = freq * mult
        if f > MAX_FREQ_HZ:
            continue
        decay = 3.2 + 1.5 * mult        # higher partials die faster, like a real bell
        for i in range(n):
            t = i / RATE
            env = (i / attack if i < attack else 1.0) * math.exp(-decay * t / seconds)
            out[i] += amp * env * math.sin(2 * math.pi * f * t)
    # normalise this note to `volume` and fade the last 20 ms so there is never a click
    peak = max(1e-9, max(abs(x) for x in out))
    fade = int(RATE * 0.02)
    for i in range(n):
        g = volume / peak
        if i > n - fade:
            g *= (n - i) / fade
        out[i] *= g
    return out


def _mix(base: list[float], add: list[float], at_s: float) -> list[float]:
    start = int(RATE * at_s)
    if start + len(add) > len(base):
        base += [0.0] * (start + len(add) - len(base))
    for i, x in enumerate(add):
        base[start + i] += x
    return base


def _finish(samples: list[float], peak: float = PEAK) -> list[int]:
    m = max(1e-9, max(abs(x) for x in samples))
    g = min(1.0, peak / m)
    return [int(32767 * g * x) for x in samples]


# ----------------------------------------------------------------------------- the sounds
def bell_samples() -> list[int]:
    """Tier 1 (default 10k–19,999): one short, gentle bell. ~0.9 s."""
    s: list[float] = []
    s = _mix(s, _bell(E5, 0.9, 0.7), 0.0)
    return _finish(s, 0.7)


def melody_samples() -> list[int]:
    """Tier 2 (default 20k–49,999): short cheerful "found it" motif, C-E-G-C rising, ~1.3 s."""
    s: list[float] = []
    for i, f in enumerate((C5, E5, G5, C6)):
        s = _mix(s, _bell(f, 0.7, 0.75), i * 0.16)
    return _finish(s, 0.78)


def jackpot_samples() -> list[int]:
    """Tier 3 (default ≥ 50k): festive fanfare — rising arpeggio, a sparkle on top, then a warm major chord. ~2.3 s."""
    s: list[float] = []
    for i, f in enumerate((C5, E5, G5, C6, E6)):
        s = _mix(s, _bell(f, 0.55, 0.7), i * 0.11)
    for i, f in enumerate((G6, E6, C6)):                     # little sparkle
        s = _mix(s, _bell(f, 0.35, 0.45), 0.62 + i * 0.09)
    for f, v in ((C4, 0.55), (G4, 0.5), (C5, 0.6), (E5, 0.55), (G5, 0.5)):   # closing chord
        s = _mix(s, _bell(f, 1.3, v), 0.95)
    return _finish(s, PEAK)


def chime_samples() -> list[int]:
    """Alternative: two soft notes (A5 → C6). ~1.0 s."""
    s: list[float] = []
    s = _mix(s, _bell(A5, 0.6, 0.65), 0.0)
    s = _mix(s, _bell(C6, 0.7, 0.65), 0.22)
    return _finish(s, 0.72)


def harp_samples() -> list[int]:
    """Alternative: quick harp-like run up the pentatonic scale. ~1.4 s."""
    s: list[float] = []
    for i, f in enumerate((C5, D5, E5, G5, A5, C6, D6, E6)):
        s = _mix(s, _bell(f, 0.5, 0.6), i * 0.07)
    return _finish(s, 0.75)


def spot_samples() -> list[int]:
    """Free Spot Detector: a distinct "doorbell" — two descending notes then a bright third. ~1.4 s."""
    s: list[float] = []
    s = _mix(s, _bell(E6, 0.45, 0.6), 0.0)
    s = _mix(s, _bell(C6, 0.55, 0.6), 0.25)
    s = _mix(s, _bell(G6, 0.7, 0.55), 0.6)
    return _finish(s, 0.74)


def soft_samples() -> list[int]:
    """Very quiet single low note, used for UNCERTAIN previews / tests. ~0.5 s."""
    return _finish(_bell(A4, 0.5, 0.4), 0.4)


# sound id -> (file name, generator, human label). Users pick one per profit tier; add new ones here.
SOUND_LIBRARY: dict[str, tuple[str, Callable[[], list[int]], str]] = {
    "bell": ("bell_v2.wav", bell_samples, "Gentle bell (short)"),
    "melody": ("melody_v2.wav", melody_samples, "Happy little melody"),
    "jackpot": ("jackpot_v2.wav", jackpot_samples, "JACKPOT fanfare (festive)"),
    "chime": ("chime_v2.wav", chime_samples, "Two-note chime"),
    "harp": ("harp_v2.wav", harp_samples, "Harp run"),
    "soft": ("soft_v2.wav", soft_samples, "Soft single note (quiet)"),
    "spot": ("spot_v2.wav", spot_samples, "Doorbell (free spot)"),
}

# backwards-compatible level names (Test alarm button, old call sites)
LEVEL_DEFAULT_SOUND = {"JACKPOT": "jackpot", "GOOD FLIP": "bell", "UNCERTAIN": "soft"}


def _write(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def ensure_sounds(target: Path | None = None) -> dict[str, Path]:
    """Generate every library sound once and return {sound_id: path} plus the legacy level keys."""
    d = target or sounds_dir()
    d.mkdir(parents=True, exist_ok=True)
    out: dict[str, Path] = {}
    for sid, (fname, fn, _label) in SOUND_LIBRARY.items():
        p = d / fname
        if not p.exists() or p.stat().st_size < 1000:
            _write(p, fn())
        out[sid] = p
    for level, sid in LEVEL_DEFAULT_SOUND.items():
        out[level] = out[sid]
    return out


def sound_labels() -> dict[str, str]:
    return {sid: label for sid, (_f, _fn, label) in SOUND_LIBRARY.items()}
