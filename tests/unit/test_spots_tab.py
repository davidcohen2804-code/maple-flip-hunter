"""Free Spot Detector tab: Start/Stop, scope parsing/persistence, manual marking, alert card + sound on a PRIME
vacancy fed through the real monitor with reference frames, history."""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")
cv2 = pytest.importorskip("cv2")
from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402
from maple_flip_hunter.spots import ShopDetector  # noqa: E402
from maple_flip_hunter.ui.alert_popup import AlertPopup  # noqa: E402
from maple_flip_hunter.ui.main_window import MainWindow  # noqa: E402

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "spots"


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def test_spots_tab_end_to_end(app, tmp_path):
    s = Settings(physical_features_enabled=True, source_hene_public_enabled=False, source_public_market_enabled=False, source_cozy_enabled=False, source_msw_enabled=False, source_fmwatcher_autostart=False, spots_vacate_confirm_s=5)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real = n.play
    n.play = lambda sid, volume=None: (played.append(sid), real(sid, volume))  # type: ignore[method-assign]
    db = Database(str(tmp_path / "m.sqlite3"))
    w = MainWindow(MarketEngine(s, db, n), s, n)
    w.show(); app.processEvents()
    assert w.tabs.tabText(2) == "Free Spot Detector"
    tab = w.spots
    assert tab.parse_channels("1-3, 7, 12;13") == [1, 2, 3, 7, 12, 13] and tab.parse_channels("") == []
    tab.map_edit.setText("Free Market"); tab.room_spin.setValue(1); tab.ch_spin.setValue(3)
    tab.channels_edit.setText("1-5"); tab.cb_good.setChecked(True)
    tab.apply_scope()
    assert tab.cfg.scopes == [{"map": "Free Market", "room": 1, "channels": [1, 2, 3, 4, 5], "enabled": True}]
    assert s.spots_scopes == tab.cfg.scopes and set(s.spots_alert_ratings) == {"PRIME", "GOOD"}
    # feed reference frames through the real monitor instead of the screen
    base = cv2.imread(str(FIX / "fm1_with_gaps.png"))
    dets = ShopDetector().detect(base)
    tab.start_monitoring()
    assert tab.status_pill.text() == "ONLINE" and not tab.btn_start.isEnabled() and tab.btn_stop.isEnabled()
    mon = tab.monitor
    t = [10_000.0]

    def step(frame, dt=2.0):
        t[0] += dt
        mon.process(frame, now=t[0])                 # events reach the tab through its relay signal
        app.processEvents()
    for _ in range(3):
        step(base)
    assert len(tab.registry.in_scope("Free Market", 1, 3)) == 13 and tab.table.rowCount() == 13
    # mark the first hut PRIME by "clicking" it
    tab.mark_spot(dets[0].x, dets[0].y, "PRIME")
    prime = tab.registry.find("Free Market", 1, 3, dets[0].x, dets[0].y)
    assert prime.rating == "PRIME" and prime.manual
    for old in list(AlertPopup._open):
        old.close()
    gone = base.copy()
    x, y, bw, bh = dets[0].box
    gone[max(0, y):y + bh, max(0, x):x + bw] = (230, 200, 150)
    for _ in range(9):                                   # 18 s without the hut
        step(gone)
    app.processEvents()
    assert prime.state == "FREE" and played and played[-1] == "spot"
    assert AlertPopup.open_count() == 1 and "SPOT AVAILABLE" in [w2 for w2 in AlertPopup._open][0].findChildren(type(tab.status_text))[0].text()
    assert any("ALERT" in tab.history.item(r, 1).text() for r in range(tab.history.rowCount()))
    for p in list(AlertPopup._open):
        p.close()
    tab.stop_monitoring()
    assert tab.status_pill.text() == "OFFLINE" and not mon.running
    # spots and history survive a restart
    w.tray.hide(); w.close()
    w2 = MainWindow(MarketEngine(s, db, n), s, n)
    assert w2.spots.registry.spots[prime.spot_id].rating == "PRIME" and w2.spots.registry.spots[prime.spot_id].vacate_count == 1
    w2.tray.hide(); w2.close()
