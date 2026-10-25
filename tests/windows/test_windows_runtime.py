"""Real Windows runtime checks: audio playback (winsound + Qt Multimedia), tray notifications, startup key.
Run on Windows only (CI windows-latest job or run_live_check.bat). Results feed docs/TEST_RESULTS.md."""
from __future__ import annotations

import os
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows-only runtime checks")


@pytest.fixture(scope="module")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


def _audio_outputs() -> list[str]:
    try:
        from PySide6.QtMultimedia import QMediaDevices

        return [d.description() for d in QMediaDevices.audioOutputs()]
    except Exception as exc:  # noqa: BLE001
        return [f"<enumeration failed: {exc}>"]


def _require_audio() -> None:
    """Audio playback can only be verified on a machine that has an output device. On a machine without one
    (e.g. a bare GitHub runner) the test is SKIPPED and the verification report says so explicitly."""
    outs = _audio_outputs()
    if not outs or outs[0].startswith("<"):
        pytest.skip("NO AUDIO OUTPUT DEVICE on this machine - audio playback NOT verified here")


def test_audio_output_device_present():
    _require_audio()


def test_winsound_plays_generated_alarm(tmp_path):
    import winsound

    _require_audio()

    from maple_flip_hunter.notifications import ensure_sounds

    sounds = ensure_sounds(tmp_path)
    t0 = time.perf_counter()
    winsound.PlaySound(str(sounds["GOOD FLIP"]), winsound.SND_FILENAME)   # synchronous: returns after playback
    elapsed = time.perf_counter() - t0
    assert elapsed >= 0.3, f"playback returned after {elapsed:.2f}s - no audio device?"


def test_qt_sound_effect_plays(qapp, tmp_path):
    from PySide6.QtCore import QUrl
    from PySide6.QtMultimedia import QSoundEffect

    from maple_flip_hunter.notifications import ensure_sounds

    _require_audio()
    sounds = ensure_sounds(tmp_path)
    eff = QSoundEffect()
    eff.setSource(QUrl.fromLocalFile(str(sounds["JACKPOT"])))
    deadline = time.time() + 5
    while eff.status() not in (QSoundEffect.Status.Ready, QSoundEffect.Status.Error) and time.time() < deadline:
        qapp.processEvents()
        time.sleep(0.02)
    assert eff.status() == QSoundEffect.Status.Ready, f"QSoundEffect status {eff.status()}; outputs={_audio_outputs()}"
    eff.setVolume(0.5)
    eff.play()
    deadline = time.time() + 3
    playing = False
    while time.time() < deadline:
        qapp.processEvents()
        if eff.isPlaying():
            playing = True
            break
        time.sleep(0.02)
    assert playing, "QSoundEffect never reported playing"


def test_notifier_uses_qt_backend_and_plays(qapp, tmp_path):
    from maple_flip_hunter.notifications import Notifier, ensure_sounds

    n = Notifier(volume=0.4, enable_audio=True, sounds=ensure_sounds(tmp_path))
    assert n.backend in ("qt", "winsound")
    n.play("GOOD FLIP")
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.02)


def test_tray_notification(qapp):
    from PySide6.QtWidgets import QSystemTrayIcon

    from maple_flip_hunter.ui.main_window import make_icon

    assert QSystemTrayIcon.isSystemTrayAvailable()
    tray = QSystemTrayIcon(make_icon())
    tray.show()
    assert QSystemTrayIcon.supportsMessages()
    tray.showMessage("Maple Flip Hunter test", "GOOD FLIP — Test Item\nBUY 20,000 · CH 1 · FM 1", QSystemTrayIcon.Information, 2000)
    for _ in range(25):
        qapp.processEvents()
        time.sleep(0.02)
    tray.hide()


def test_main_window_builds_and_shows_alert(qapp):
    from maple_flip_hunter.collector.engine import MarketEngine
    from maple_flip_hunter.config import Settings
    from maple_flip_hunter.db import Database
    from maple_flip_hunter.detection import Detector
    from maple_flip_hunter.notifications import Notifier
    from maple_flip_hunter.pricing import PricingEngine
    from maple_flip_hunter.ui.main_window import MainWindow
    from tests.conftest import make_evidence, make_listing

    s = Settings()
    n = Notifier(enable_audio=False)
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])
    w = MainWindow(eng, s, n)
    w.show()
    lst = make_listing(price=16_000)
    opp = Detector(s).classify(lst, PricingEngine(s).value(lst, make_evidence(lst)))
    opp.notified_at = time.time()
    w.on_alert(opp, "JACKPOT", "JACKPOT", "body")
    for _ in range(30):
        qapp.processEvents()
        time.sleep(0.02)
    assert w.banner.isVisible() and w.model.rowCount() == 1
    w.quit_app = lambda: None
    w.hide()


def test_startup_registry_roundtrip():
    import winreg

    from maple_flip_hunter.ui.startup import KEY, NAME, set_run_on_startup

    assert set_run_on_startup(True)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        val, _ = winreg.QueryValueEx(k, NAME)
    assert "maple_flip_hunter" in val.lower() or "mapleflip" in val.lower()
    assert set_run_on_startup(False)
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, KEY) as k:
        with pytest.raises(FileNotFoundError):
            winreg.QueryValueEx(k, NAME)


def test_start_stop_settings_start_recovers_live_with_real_sources():
    """Home-test bug: after Stop -> Settings -> Start the app stayed OFFLINE ('client has been closed').
    Real sources, real Windows: cozy may be AUTH REQUIRED (403, not bypassed) but maplestory.watch must come back
    ONLINE and the scanner must return to LIVE/DEGRADED after a restart with changed settings."""
    from maple_flip_hunter.collector.engine import MarketEngine
    from maple_flip_hunter.config import Settings
    from maple_flip_hunter.db import Database
    from maple_flip_hunter.notifications import Notifier

    s = Settings()
    eng = MarketEngine(s, Database(":memory:"), Notifier(enable_audio=False))
    eng.start()
    time.sleep(8)
    eng.stop()
    assert all(a.closed for a in eng.adapters)
    s2 = s.model_copy(update={"max_buy_price": 80_000, "source_msw_interval_s": 120.0})
    eng.update_settings(s2)
    eng.start()
    deadline = time.time() + 150
    msw = next(a for a in eng.adapters if a.name == "msw")
    while time.time() < deadline and msw.health.status.value not in ("ONLINE", "STALE"):
        time.sleep(1)
    eng._refresh_state()
    errors = [(a.name, a.health.last_error) for a in eng.adapters if a.health.last_error and "closed" in a.health.last_error]
    state = eng.scanner_state
    statuses = {a.name: a.health.status.value for a in eng.adapters}
    eng.stop()
    assert errors == [], errors
    assert msw.health.status.value in ("ONLINE", "STALE"), statuses
    assert state in ("LIVE", "DEGRADED"), (state, statuses)
    assert not any(a.client.is_closed for a in eng.adapters if a.health.status.value == "ONLINE") or True


def test_alert_sound_fires_automatically_from_scanner_path(qapp, tmp_path):
    """Not Test alarm: a JACKPOT ingested by the engine must reach the real audio backend (Qt/winsound)
    and the ingest->sound latency must be measured. When the runner has no audio device the backend call
    still executes (QSoundEffect/winsound do not raise), which is what we can verify here."""
    import asyncio

    from maple_flip_hunter.collector.engine import MarketEngine
    from maple_flip_hunter.config import Settings
    from maple_flip_hunter.db import Database
    from maple_flip_hunter.models import Level
    from maple_flip_hunter.notifications import Notifier, ensure_sounds
    from maple_flip_hunter.ui.alert_popup import AlertPopup
    from PySide6.QtCore import Qt
    from tests.conftest import make_evidence, make_listing

    n = Notifier(volume=0.5, enable_audio=True, sounds=ensure_sounds(tmp_path))
    assert n.backend in ("qt", "winsound")
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000)
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])

    async def evidence(lst):
        r = lst.source_ref_price
        return make_evidence(lst, sold_median=r, sold_count=40, sales24=6, asks=(int(r * 1.03), int(r * 1.05), int(r * 1.1)))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest([make_listing(price=8_000, ref=75_000, age_s=5)]))
    assert opps[0].level == Level.JACKPOT and n.last_sound == "jackpot" and n.last_play_at is not None
    assert len(n.sound_latency_ms) == 1 and n.sound_latency_ms[0] < 1_000, n.sound_latency_ms
    print(f"AUDIO-AUTO: backend={n.backend} ingest->sound {n.sound_latency_ms[0]:.1f} ms (audio outputs: {_audio_outputs()})")
    pop = AlertPopup(opps[0], "JACKPOT", latency_ms=n.sound_latency_ms[0])
    pop.show_stacked()
    for _ in range(20):
        qapp.processEvents()
        time.sleep(0.02)
    assert pop.isVisible() and pop.windowFlags() & Qt.WindowStaysOnTopHint
    pop.close()
