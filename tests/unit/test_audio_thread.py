"""The automatic-alert audio bug: sounds requested from the scanner thread must be played on the Qt GUI thread,
the QSoundEffect objects must be created once and kept, and the full chain must be logged."""
from __future__ import annotations

import os
import threading
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QThread  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _events(n, kind):
    return [d for _, e, d in n.audio_log if e == kind]


def test_worker_thread_play_is_marshalled_to_gui_thread(app, tmp_path):
    n = Notifier(enable_audio=True, sounds=ensure_sounds(tmp_path))
    if n.backend != "qt":
        pytest.skip("Qt Multimedia not available here")
    assert n.attach_gui() and n._proxy is not None
    assert n._effects and all(e.thread() is app.thread() for e in n._effects.values())   # created once, on the GUI thread
    seen_threads = []
    orig = n._play_qt_gui_thread

    def spy(level, volume):
        seen_threads.append(QThread.currentThread() is app.thread())
        orig(level, volume)
    n._play_qt_gui_thread = spy  # type: ignore[method-assign]
    n._proxy.request.disconnect()
    n._proxy.request.connect(lambda sid, vol: spy(sid, vol))
    t = threading.Thread(target=lambda: n.play("melody"))
    t.start(); t.join()
    for _ in range(50):                      # let the queued signal reach the GUI thread
        app.processEvents()
        if seen_threads:
            break
        time.sleep(0.02)
    assert seen_threads == [True], "play() from a worker thread must execute on the GUI thread"
    req = _events(n, "AUDIO_REQUESTED")
    assert req and "thread=worker" in req[-1]
    started = _events(n, "AUDIO_STARTED")
    assert started and "thread=gui" in started[-1] and "sound=melody" in started[-1]
    assert not _events(n, "AUDIO_ERROR")
    # the effect object survives (not garbage collected) and is the same one next time
    before = n._effects["melody"]
    n.play("melody")
    assert n._effects["melody"] is before


def test_suppression_reasons_are_logged(app, tmp_path):
    n = Notifier(enable_audio=True, sounds=ensure_sounds(tmp_path))
    n.attach_gui()
    n.mute_for(30)
    n.play("bell")
    assert any("muted" in d for d in _events(n, "AUDIO_SUPPRESSED"))
    n2 = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    n2.play("bell")
    assert any("audio disabled" in d for d in _events(n2, "AUDIO_SUPPRESSED"))
    n2.play("nope")
    assert any("unknown sound" in d for d in _events(n2, "AUDIO_ERROR"))


def test_simulated_automatic_alert_runs_the_real_path_without_touching_data(app, tmp_path):
    from maple_flip_hunter.ui.alert_popup import AlertPopup
    from maple_flip_hunter.ui.main_window import MainWindow

    s = Settings()
    n = Notifier(enable_audio=True, sounds=ensure_sounds(tmp_path))
    db = Database(":memory:")
    eng = MarketEngine(s, db, n, adapters=[])
    w = MainWindow(eng, s, n)
    w.show()
    for old in list(AlertPopup._open):
        old.close()
    rows_before = w.model.rowCount()
    opp = eng.simulate_alert()
    deadline = time.time() + 3
    while time.time() < deadline and (not _events(n, "ALERT_CREATED") or AlertPopup.open_count() == 0):
        app.processEvents()
        time.sleep(0.02)
    assert opp.listing.source == "test" and opp.valuation.net_profit >= 50_000
    assert _events(n, "ALERT_CREATED") and _events(n, "AUDIO_REQUESTED")
    assert "thread=worker" in _events(n, "AUDIO_REQUESTED")[-1]          # same thread situation as the scanner
    if n.backend == "qt":
        assert _events(n, "AUDIO_STARTED") and "thread=gui" in _events(n, "AUDIO_STARTED")[-1]
        assert "sound=jackpot" in _events(n, "AUDIO_STARTED")[-1]        # highest enabled band's sound
    assert AlertPopup.open_count() == 1                                 # floating card appeared
    assert w.model.rowCount() == rows_before                            # not in the table
    assert db.conn.execute("SELECT COUNT(*) FROM listings").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM opportunities").fetchone()[0] == 0
    assert db.conn.execute("SELECT COUNT(*) FROM notifications").fetchone()[0] == 0
    assert eng.metrics.alerts == 0
    for p in list(AlertPopup._open):
        p.close()
    w.tray.hide(); w.close()
