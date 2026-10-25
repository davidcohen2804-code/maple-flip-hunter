"""Always-on-top alert card + ingest→sound latency + automatic sound from the scanner path (not Test alarm)."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.models import Level  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402
from maple_flip_hunter.ui.alert_popup import AlertPopup  # noqa: E402
from tests.conftest import make_evidence, make_listing  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


def _engine(tmp_path, notifier):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000)
    eng = MarketEngine(s, Database(":memory:"), notifier, adapters=[])

    async def evidence(lst):
        r = lst.source_ref_price
        unknown_equip = (lst.category or "").lower() == "equip" and not lst.stats
        return make_evidence(lst, sold_median=r, sold_count=40, sales24=6, asks=(int(r * 1.03), int(r * 1.05), int(r * 1.1)),
                             stats_matched=False if unknown_equip else None)   # as the real adapters report it
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    return eng


def test_sound_fires_automatically_from_scanner_and_latency_is_measured(tmp_path):
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    played = []
    real_play = n.play

    def play(sid, volume=None):
        played.append(sid)
        real_play(sid, volume)
    n.play = play  # type: ignore[method-assign]
    eng = _engine(tmp_path, n)
    loop = asyncio.new_event_loop()
    t0 = time.time()
    opps = loop.run_until_complete(eng.ingest([make_listing(price=8_000, ref=42_000, seller="a", age_s=5)]))
    assert opps[0].level == Level.JACKPOT and played == ["melody"]       # chosen by the computed profit band
    assert n.last_play_at is not None and n.last_play_at >= t0
    assert len(n.sound_latency_ms) == 1 and 0 <= n.sound_latency_ms[0] < 2_000
    # stale listing and UNCERTAIN (unverified) rows never reach the sound
    before = len(played)
    loop.run_until_complete(eng.ingest([make_listing(price=8_000, ref=42_000, seller="b", age_s=3_600)]))
    unc = make_listing(price=8_000, ref=42_000, seller="c", age_s=5, category="Equip", stats=None, name="Zard")
    loop.run_until_complete(eng.ingest([unc]))
    assert len(played) == before


def test_popup_contents_copy_mute_and_stays_until_ok(app, tmp_path):
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = _engine(tmp_path, n)
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest([make_listing(price=8_000, ref=42_000, seller="ShopX", age_s=5)]))
    opp = opps[0]
    pop = AlertPopup(opp, "JACKPOT", on_mute=lambda: n.mute_for(60), latency_ms=12.5)
    pop.show_stacked()
    assert pop.windowFlags() & Qt.WindowStaysOnTopHint and pop.windowFlags() & Qt.WindowDoesNotAcceptFocus
    assert AlertPopup.open_count() == 1 and pop.isVisible()
    labels = " ".join(lbl.text() for lbl in pop.findChildren(__import__("PySide6.QtWidgets", fromlist=["QLabel"]).QLabel))
    assert "Bow Attack Scroll 60%" in labels and "8,000" in labels and "estimated net profit" in labels
    assert "not guaranteed" in labels and "CH 24" in labels and "ShopX" in labels and "alert in 12 ms" in labels
    pop.btn_copy.click()
    assert QApplication.clipboard().text() == "CH 24 · FM 25 · Shop: ShopX"
    pop.btn_mute.click()
    assert n.muted_until > time.time() + 50
    n.last_play_at = None
    n.play("bell")
    assert n.last_play_at is None                       # muted: nothing reached a backend
    for _ in range(20):
        app.processEvents()
    assert pop.isVisible()                               # no auto-hide
    pop.btn_ok.click()
    app.processEvents()
    assert AlertPopup.open_count() == 0
    # a second alert while one is open stacks below it
    a = AlertPopup(opp, "GOOD FLIP")
    a.show_stacked()
    b = AlertPopup(opp, "GOOD FLIP")
    b.show_stacked()
    assert b.y() > a.y()
    a.close(); b.close()
