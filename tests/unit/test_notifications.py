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
    assert {"JACKPOT", "GOOD FLIP", "UNCERTAIN", "bell", "melody", "jackpot", "chime", "harp", "soft"} <= set(sounds)
    with wave.open(str(sounds["jackpot"])) as w:
        assert w.getframerate() == 44100 and 1.8 < w.getnframes() / 44100 < 3.0
    with wave.open(str(sounds["bell"])) as w:
        assert 0.5 < w.getnframes() / 44100 < 1.2
    assert sounds["JACKPOT"] == sounds["jackpot"] and sounds["GOOD FLIP"] == sounds["bell"]


def test_sounds_are_pleasant_not_sirens():
    """User requirement: melodic, no sirens / harsh beeps. Checked on the synthesis itself:
    peak amplitude capped, short, every partial below 2.2 kHz, and the spectrum is dominated by scale notes."""
    import math
    import struct

    from maple_flip_hunter.notifications.sounds import MAX_FREQ_HZ, PEAK, RATE, SOUND_LIBRARY

    for sid, (_f, fn, _label) in SOUND_LIBRARY.items():
        samples = fn()
        assert len(samples) / RATE <= 3.0, sid
        assert max(abs(x) for x in samples) <= PEAK * 32767 + 1, sid
        # crude spectral check on the loudest 4096 samples: energy above MAX_FREQ_HZ must be negligible
        # compared with the scale notes the sound is built from (no shrill sweeps or high beeps)
        peak_i = max(range(len(samples)), key=lambda i: abs(samples[i]))
        seg = samples[max(0, peak_i - 1024):max(0, peak_i - 1024) + 4096]
        hi = sum(abs(_dft_bin(seg, f)) for f in range(int(MAX_FREQ_HZ) + 300, 8000, 500))
        lo = sum(abs(_dft_bin(seg, f)) for f in (262, 392, 440, 523, 659, 784, 880, 1047, 1319, 1568))
        assert hi < lo * 0.1, (sid, hi, lo)


def _dft_bin(seg, freq):
    import cmath
    import math

    from maple_flip_hunter.notifications.sounds import RATE

    w = -2j * math.pi * freq / RATE
    return sum(x * cmath.exp(w * i) for i, x in enumerate(seg)) / len(seg)


def test_sound_tiers_validation_and_lookup():
    import pytest

    from maple_flip_hunter.config import Settings, SoundTier, default_sound_tiers, tier_for_profit, validate_tiers

    tiers = default_sound_tiers()
    assert tier_for_profit(tiers, 9_999) is None
    assert tier_for_profit(tiers, 10_000).sound == "bell"
    assert tier_for_profit(tiers, 19_999).sound == "bell"
    assert tier_for_profit(tiers, 20_000).sound == "melody"
    assert tier_for_profit(tiers, 49_999).sound == "melody"
    assert tier_for_profit(tiers, 50_000).sound == "jackpot"
    assert tier_for_profit(tiers, 5_000_000).sound == "jackpot"
    assert tier_for_profit(tiers, None) is None
    with pytest.raises(ValueError):
        validate_tiers([SoundTier(min_profit=0, max_profit=20_000), SoundTier(min_profit=20_000, max_profit=None)])
    with pytest.raises(ValueError):
        validate_tiers([SoundTier(min_profit=0, max_profit=None), SoundTier(min_profit=100_000, max_profit=None)])
    with pytest.raises(ValueError):
        SoundTier(min_profit=5_000, max_profit=4_000)
    # unsorted input is sorted; gaps are allowed and extra bands can be added
    s = Settings(sound_tiers=[SoundTier(min_profit=100_000, sound="harp"), SoundTier(min_profit=10_000, max_profit=20_000),
                              SoundTier(min_profit=30_000, max_profit=99_999, sound="chime")])
    assert [t.min_profit for t in s.sound_tiers] == [10_000, 30_000, 100_000]
    assert tier_for_profit(s.sound_tiers, 25_000) is None
    # settings file with overlapping tiers falls back to defaults instead of crashing
    import json
    import tempfile
    from pathlib import Path

    bad = Settings().model_dump()
    bad["sound_tiers"] = [{"min_profit": 0, "max_profit": None}, {"min_profit": 10, "max_profit": 20}]
    with tempfile.TemporaryDirectory() as d:
        pth = Path(d) / "s.json"
        pth.write_text(json.dumps(bad), encoding="utf-8")
        assert Settings.load(pth).sound_tiers[0].sound == "bell"


def test_alert_sound_follows_actual_profit_not_label(tmp_path):
    """A JACKPOT-labelled row (ROI rule) with net profit 30k plays the 20k–49,999 melody, not the fanfare;
    a 60k profit plays the fanfare; a disabled band alerts silently; a profit below every band is silent."""
    import asyncio

    from maple_flip_hunter.collector.engine import MarketEngine
    from maple_flip_hunter.config import Settings
    from maple_flip_hunter.db import Database
    from maple_flip_hunter.models import Level

    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    n.play = lambda sid, volume=None: played.append(sid)  # type: ignore[method-assign]
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])

    async def evidence(lst):          # strong market evidence around the listing's reference price
        r = lst.source_ref_price
        return make_evidence(lst, sold_median=r, sold_count=40, sales24=6, asks=(int(r * 1.03), int(r * 1.05), int(r * 1.1)))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    loop = asyncio.new_event_loop()

    def run(price, ref, seller):
        lst = make_listing(price=price, ref=ref, seller=seller, age_s=5)
        return loop.run_until_complete(eng.ingest([lst]))

    opps = run(8_000, 42_000, "a")           # net ≈ 42k*0.95*0.97 - 8k ≈ 30.7k → ROI 380% → JACKPOT label
    assert opps[0].level == Level.JACKPOT and 20_000 <= opps[0].valuation.net_profit <= 49_999
    assert played[-1] == "melody"
    opps = run(8_000, 75_000, "b")           # net ≈ 61k → fanfare
    assert played[-1] == "jackpot" and opps[0].valuation.net_profit >= 50_000
    opps = run(8_000, 22_000, "c")           # net ≈ 12k → bell
    assert played[-1] == "bell" and 10_000 <= opps[0].valuation.net_profit < 20_000
    # disable the middle band → an alert in that band is silent (toast/table only)
    s2 = s.model_copy(deep=True)
    s2.sound_tiers[1].enabled = False
    eng.update_settings(s2)
    before = len(played)
    opps = run(8_000, 42_000, "d")
    assert opps and opps[0].notified_at and len(played) == before and n.last_sound is None
    assert eng.sound_for(opps[0]) is None
    # profit below the lowest band: alert fires (min_net_profit 5k) but no sound
    opps = run(8_000, 16_500, "e")
    assert opps and 5_000 <= opps[0].valuation.net_profit < 10_000 and len(played) == before


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
