"""Alarm sounds are synthesized at first start (no third-party audio assets, no licensing issues)."""
from __future__ import annotations

import math
import struct
import wave
from pathlib import Path

from ..paths import sounds_dir

RATE = 44100


def _tone(freq_start: float, freq_end: float, seconds: float, volume: float = 0.8) -> list[int]:
    n = int(RATE * seconds)
    out = []
    phase = 0.0
    for i in range(n):
        t = i / n
        f = freq_start + (freq_end - freq_start) * t
        phase += 2 * math.pi * f / RATE
        env = min(1.0, i / (RATE * 0.01), (n - i) / (RATE * 0.03))  # click-free edges
        out.append(int(32767 * volume * env * math.sin(phase)))
    return out


def _silence(seconds: float) -> list[int]:
    return [0] * int(RATE * seconds)


def _write(path: Path, samples: list[int]) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def jackpot_samples() -> list[int]:
    """Urgent siren: 3 fast rising sweeps + high double beep, ~2.4 s."""
    s: list[int] = []
    for _ in range(3):
        s += _tone(600, 1400, 0.28, 0.95) + _tone(1400, 600, 0.22, 0.95)
    s += _silence(0.08)
    s += _tone(1800, 1800, 0.12, 0.95) + _silence(0.06) + _tone(1800, 1800, 0.12, 0.95)
    s += _silence(0.1) + _tone(2200, 2200, 0.25, 0.95)
    return s


def good_samples() -> list[int]:
    """Soft two-note chime, ~0.7 s."""
    return _tone(880, 880, 0.18, 0.55) + _silence(0.05) + _tone(1320, 1320, 0.32, 0.55)


def uncertain_samples() -> list[int]:
    return _tone(660, 660, 0.15, 0.4)


def ensure_sounds(target: Path | None = None) -> dict[str, Path]:
    d = target or sounds_dir()
    d.mkdir(parents=True, exist_ok=True)
    spec = {"JACKPOT": ("jackpot.wav", jackpot_samples), "GOOD FLIP": ("good.wav", good_samples),
            "UNCERTAIN": ("uncertain.wav", uncertain_samples)}
    out: dict[str, Path] = {}
    for level, (fname, fn) in spec.items():
        p = d / fname
        if not p.exists() or p.stat().st_size < 1000:
            _write(p, fn())
        out[level] = p
    return out
