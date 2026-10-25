"""Notification behaviour (case 14). The Qt/winsound playback paths are exercised on Windows CI;
elsewhere the silent backend is verified."""
from __future__ import annotations

import sys
import wave

from maple_flip_hunter.notifications import Notifier, ensure_sounds
from maple_flip_hunter.collector.engine import format_alert
from maple_flip_hunter.detection import Detector
from maple_flip_hunter.pricing import PricingEngine
from tests.conftest import make_evidence, make_listing


def test_sounds_are_generated(tmp_path):
    sounds = ensure_sounds(tmp_path)
    assert set(sounds) == {"JACKPOT", "GOOD FLIP", "UNCERTAIN"}
    with wave.open(str(sounds["JACKPOT"])) as w:
        assert w.getframerate() == 44100 and w.getnframes() / 44100 > 2.0
    with wave.open(str(sounds["GOOD FLIP"])) as w:
        assert 0.5 < w.getnframes() / 44100 < 1.0


def test_alert_history_and_toast_callback(tmp_path, settings):
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    got = []
    n.toast_callback = lambda opp, level, title, body: got.append((level, title, body))
    lst = make_listing(price=16_000)
    opp = Detector(settings).classify(lst, PricingEngine(settings).value(lst, make_evidence(lst)))
    title, body = format_alert(opp)
    assert title.startswith("🚨 JACKPOT")
    assert "BUY: 16,000" in body and "CH 24 · FM 25" in body and "ExampleShop" in body
    n.alert(opp, "JACKPOT", title, body)
    assert len(n.history) == 1 and got[0][0] == "JACKPOT"
    assert n.backend == "silent"


def test_windows_audio_backend(tmp_path):
    n = Notifier(enable_audio=True, sounds=ensure_sounds(tmp_path))
    if sys.platform == "win32":
        assert n.backend in ("qt", "winsound")
        n.play("GOOD FLIP")            # must not raise (winsound async / Qt effect without app falls back)
    else:
        assert n.backend in ("qt", "silent")


def test_volume_clamped(tmp_path):
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    n.set_volume(5)
    assert n.volume == 1.0
    n.set_volume(-1)
    assert n.volume == 0.0
