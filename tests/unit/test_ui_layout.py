"""UI layout: simple main screen (three numbers, Start/Stop, 10-column table, details on double-click); Settings window fits the screen, scrolls,
keeps Save/Cancel visible, remembers geometry, never opens off-screen (1366×768, 1920×1080, scaled)."""
from __future__ import annotations

import asyncio
import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
pytest.importorskip("PySide6.QtWidgets")

from PySide6.QtCore import QRect, Qt  # noqa: E402
from PySide6.QtWidgets import QApplication, QHeaderView  # noqa: E402

from maple_flip_hunter.collector.engine import MarketEngine  # noqa: E402
from maple_flip_hunter.config import Settings  # noqa: E402
from maple_flip_hunter.db import Database  # noqa: E402
from maple_flip_hunter.notifications import Notifier, ensure_sounds  # noqa: E402
from maple_flip_hunter.ui.main_window import MainWindow  # noqa: E402
from maple_flip_hunter.ui.settings_dialog import MAX_HEIGHT_FRACTION, SettingsDialog, fit_on_screen  # noqa: E402
from maple_flip_hunter.ui.table_model import COLUMNS  # noqa: E402
from tests.conftest import make_evidence, make_listing  # noqa: E402


@pytest.fixture(scope="module")
def app():
    return QApplication.instance() or QApplication([])


async def _evidence(lst):
    r = lst.source_ref_price
    unknown_equip = (lst.category or "").lower() == "equip" and not lst.stats
    return make_evidence(lst, sold_median=r, sold_count=40, sales24=6, asks=(int(r * 1.03), int(r * 1.05), int(r * 1.1)),
                         stats_matched=False if unknown_equip else None)


def _window(tmp_path):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])

    eng.gather_evidence = _evidence  # type: ignore[method-assign]
    w = MainWindow(eng, s, n)
    return w, eng


def test_stale_source_is_not_presented_as_live(app, tmp_path):
    from types import SimpleNamespace
    from maple_flip_hunter.models import SourceHealth, SourceStatus

    w, eng = _window(tmp_path)
    adapter = SimpleNamespace(health=SourceHealth(name="cozy", label="Cozy", status=SourceStatus.STALE))
    eng.adapters = [adapter]
    eng.running = True
    eng.last_data_at = time.time() - 600
    try:
        w._refresh_stats()
        assert w.state_pill.text() == "DEGRADED"
        assert w.status_label.text().startswith("DEGRADED")
        adapter.health.status = SourceStatus.IDLE
        eng.last_data_at = time.time()
        w._refresh_stats()
        assert w.state_pill.text() == "LIVE"
        assert "1 source online" in w.status_label.text()
    finally:
        eng.running = False
        w.tray.hide()
        w.close()


def test_availability_update_ignores_spot_popups(app, tmp_path):
    from types import SimpleNamespace
    from maple_flip_hunter.ui.alert_popup import AlertPopup, SpotPopup

    w, eng = _window(tmp_path)
    opp = asyncio.run(eng.ingest([make_listing(price=8_000, ref=42_000)]))[0]
    # Both card types share the stack, but a spot card has no market opportunity.
    spot = SpotPopup.__new__(SpotPopup)
    market = AlertPopup(opp, "JACKPOT")
    AlertPopup._open.extend([spot, market])
    try:
        w.on_availability(opp, "SOLD", "slot sold out", True)
        assert market.avail.text().startswith("⛔ SOLD")
    finally:
        AlertPopup._open.remove(spot)
        market.close()
        w.tray.hide()
        w.close()


def test_table_has_the_ten_columns_sold_price_is_never_invented_and_details_open_on_double_click(app, tmp_path):
    w, eng = _window(tmp_path)
    w.resize(1366, 768)
    w.show()
    assert COLUMNS == ["Item", "Buy Price", "Sold Price", "Quick Sale", "Net Profit", "ROI %", "Channel", "Room / Map", "Seller", "Age"]
    assert w.model.columnCount() == 10 and w.proxy.columnCount() == 10
    hh = w.table.horizontalHeader()
    assert hh.sectionResizeMode(0) == QHeaderView.Stretch
    lst = make_listing(name="Bow Attack Scroll 60%", price=8_000, ref=42_000, seller="ShopX", age_s=5, channel=24, fm_room=25)
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest([lst]))
    w.on_opportunity(opps[0])
    app.processEvents()
    m = w.model
    texts = [m.data(m.index(0, c), Qt.DisplayRole) for c in range(10)]
    v = opps[0].valuation
    assert texts[0] == "Bow Attack Scroll 60%" and texts[1] == "8,000"
    assert v.sold_price == 42_000 and texts[2] == "42,000"                 # 40 completed sales -> verified sold price
    assert texts[3] == f"{v.quick:,}" and "," in texts[3] and texts[4] == f"{v.net_profit:+,}"
    assert texts[5] == f"{v.roi * 100:.0f}%" and texts[6] == "CH 24" and texts[7] == "FM 25" and texts[8] == "ShopX"
    assert texts[9] in ("5s", "6s", "7s")
    tip = m.data(m.index(0, 4), Qt.ToolTipRole)
    assert "confidence" in tip and f"{v.confidence_score}/100" in tip and "Sold price: 42,000" in tip
    from maple_flip_hunter.ui.theme import GREEN, MUTED
    from maple_flip_hunter.ui.table_model import ORANGE
    assert m.data(m.index(0, 4), Qt.ForegroundRole).color().name() == GREEN     # positive profit green
    assert m.data(m.index(0, 0), Qt.ForegroundRole).color().name() == GREEN     # fresh & graded green
    assert m.data(m.index(0, 1), Qt.TextAlignmentRole) & Qt.AlignRight
    # no reliable completed sales -> Sold Price is N/A (asking prices are never shown as sales)
    async def asks_only(lst):
        return make_evidence(lst, sold_median=None, sold_count=0, sales24=0, sold_prices=(), ask_median=lst.source_ref_price, ask_count=6,
                             asks=tuple(int(lst.source_ref_price * f) for f in (1.0, 1.02, 1.05, 1.08, 1.1, 1.2)))
    eng.gather_evidence = asks_only  # type: ignore[method-assign]
    ask_lst = make_listing(name="Ask Only Item", price=8_000, ref=60_000, seller="Asker", age_s=5, channel=2, fm_room=3)
    ask_opp = asyncio.new_event_loop().run_until_complete(eng.ingest([ask_lst]))
    assert ask_opp, "asks-only listing still gets evaluated"
    w.model.upsert(ask_opp[0]); app.processEvents()
    r = w.model.rows.index(ask_opp[0])
    assert ask_opp[0].valuation.sold_price is None
    assert w.model.data(w.model.index(r, 2), Qt.DisplayRole) == "N/A"
    assert "N/A" in w.model.data(w.model.index(r, 2), Qt.ToolTipRole) and "nothing is invented" in w.model.data(w.model.index(r, 2), Qt.ToolTipRole)
    assert w.model.data(w.model.index(r, 2), Qt.ForegroundRole).color().name() == MUTED
    # details are NOT on the main screen: they open in a dialog on double-click
    assert not w.details_dialog.isVisible()
    bow_row = next(r for r in range(w.proxy.rowCount()) if w.proxy.data(w.proxy.index(r, 0)) == "Bow Attack Scroll 60%")
    w.table.selectRow(bow_row); app.processEvents()
    w.show_details(); app.processEvents()
    assert w.details_dialog.isVisible()
    html = w.details.toPlainText()
    for needle in ("Net profit (quick)", "Net profit (conservative)", "ROI / Discount", "Confidence", "Liquidity",
                   "Source observed", "Location", "ShopX", "Item stats", "Source", "Quick sale price",
                   "Reference value", "Listing price", "8,000", "42,000"):
        assert needle in html, needle
    w.details_dialog.hide()
    # default order: best fresh deal first; sorting works on every column
    lst2 = make_listing(name="Zard", price=8_000, ref=90_000, seller="BigShop", age_s=200, channel=3, fm_room=18)
    opp2 = asyncio.new_event_loop().run_until_complete(eng.ingest([lst2]))[0]
    w.model.upsert(opp2)
    w.table.sortByColumn(4, Qt.DescendingOrder); app.processEvents()
    assert w.proxy.data(w.proxy.index(0, 0)) == "Zard" and w.proxy.data(w.proxy.index(0, 6)) == "CH 3" and w.proxy.data(w.proxy.index(0, 7)) == "FM 18"
    w.table.sortByColumn(9, Qt.AscendingOrder); app.processEvents()          # freshest first
    assert w.proxy.data(w.proxy.index(0, 0)) in ("Bow Attack Scroll 60%", "Ask Only Item")
    for c in range(10):
        w.table.sortByColumn(c, Qt.DescendingOrder); app.processEvents()
    assert w.proxy.rowCount() == 3
    # colours: UNCERTAIN -> orange, stale -> grey (and sorted below fresh deals); Age updates live without a rescan
    eng.gather_evidence = _evidence  # type: ignore[method-assign]
    unc = make_listing(name="Red Whip", price=8_000, ref=20_000, seller="Maybe", age_s=30, category="Equip", stats=None)
    o_unc = asyncio.new_event_loop().run_until_complete(eng.ingest([unc]))
    if o_unc:
        w.model.upsert(o_unc[0])
        r = w.model.rows.index(o_unc[0])
        assert w.model.data(w.model.index(r, 0), Qt.ForegroundRole).color().name() == ORANGE
    old = make_listing(name="Pan Lid", price=8_000, ref=40_000, seller="Sleeper", age_s=2_000)
    o_old = asyncio.new_event_loop().run_until_complete(eng.ingest([old]))[0]
    w.model.upsert(o_old)
    r = w.model.rows.index(o_old)
    assert w.model.data(w.model.index(r, 0), Qt.ForegroundRole).color().name() == MUTED
    assert w.model.data(w.model.index(r, 9), Qt.DisplayRole) == "33m · stale"
    w.table.sortByColumn(4, Qt.DescendingOrder); app.processEvents()
    assert w.proxy.data(w.proxy.index(w.proxy.rowCount() - 1, 0)) == "Pan Lid"      # stale rows sort below fresh ones
    o_old.listing.source_seen_at -= 3600          # time passes: next tick shows the new age, no rescan involved
    w.model.refresh_ages()
    assert w.model.data(w.model.index(r, 9), Qt.DisplayRole).startswith("1.6h")
    w.quit_app = lambda: None
    from maple_flip_hunter.ui.alert_popup import AlertPopup
    for pop in list(AlertPopup._open):
        pop.close()
    w.tray.hide(); w.close()


def test_three_main_fields_autosave_and_drive_table_and_scanner(app, tmp_path, monkeypatch):
    import maple_flip_hunter.config as cfg
    monkeypatch.setattr(cfg, "settings_path", lambda: tmp_path / "settings.json")
    w, eng = _window(tmp_path)
    w.show(); app.processEvents()
    assert w.in_min_buy.value() == 0 and w.in_max_buy.value() == 1_000_000 and w.in_min_profit.value() == 5_000
    lst = make_listing(name="Cheap Thing", price=8_000, ref=30_000, seller="S", age_s=5)
    opp = asyncio.new_event_loop().run_until_complete(eng.ingest([lst]))[0]
    w.model.upsert(opp); app.processEvents()
    assert w.proxy.rowCount() == 1
    w.in_min_profit.setValue(1_000_000)                 # nothing earns a million
    w._apply_main_filters(); app.processEvents()
    assert w.settings.min_net_profit == 1_000_000 and eng.settings.min_net_profit == 1_000_000
    assert w.proxy.rowCount() == 0
    import json
    assert json.loads((tmp_path / "settings.json").read_text())["min_net_profit"] == 1_000_000   # saved automatically
    w.in_min_profit.setValue(5_000)
    w.in_max_buy.setValue(100)                          # max below min is corrected, never an error dialog
    w.in_min_buy.setValue(5_000)
    w._apply_main_filters(); app.processEvents()
    assert w.settings.max_buy_price >= w.settings.min_buy_price == 5_000
    assert "STOPPED" == w.state_pill.text()
    w.tray.hide(); w.close()


@pytest.mark.parametrize("screen", [QRect(0, 0, 1366, 728), QRect(0, 0, 1920, 1040), QRect(0, 0, 1536, 824),   # 125 % scaling
                                    QRect(0, 0, 1280, 672), QRect(1920, 0, 1366, 728)])                        # 150 % / second monitor
def test_settings_geometry_fits_every_screen(screen):
    tall = QRect(100, 100, 640, 1400)
    r = fit_on_screen(tall, screen)
    assert r.height() <= screen.height() * MAX_HEIGHT_FRACTION and r.width() <= screen.width()
    assert screen.contains(r), (r, screen)
    off = QRect(screen.right() - 50, screen.bottom() - 10, 640, 500)      # mostly outside -> pulled back in
    r2 = fit_on_screen(off, screen)
    assert screen.contains(r2) and r2.width() == 640 and r2.height() == 500
    inside = QRect(screen.left() + 20, screen.top() + 20, 600, 400)        # already fine -> untouched
    assert fit_on_screen(inside, screen) == inside


def test_settings_dialog_scrolls_keeps_buttons_visible_and_remembers_geometry(app):
    s = Settings()
    dlg = SettingsDialog(s)
    dlg.show()
    app.processEvents()
    avail = dlg._screen_rect()
    assert dlg.height() <= avail.height() * MAX_HEIGHT_FRACTION + 1
    assert avail.contains(dlg.frameGeometry()) or avail.contains(dlg.geometry())
    assert dlg.scroll.widget().sizeHint().height() > dlg.scroll.viewport().height()    # content taller -> scrolls
    assert dlg.scroll.verticalScrollBar().maximum() > 0
    assert dlg.button_bar.isVisible() and dlg.bb.isVisible()
    dlg.scroll.verticalScrollBar().setValue(dlg.scroll.verticalScrollBar().maximum())
    app.processEvents()
    assert dlg.button_bar.geometry().bottom() <= dlg.height() and dlg.bb.isVisible()  # pinned, not scrolled away
    assert not (dlg.windowFlags() & Qt.MSWindowsFixedSizeDialogHint) and dlg.minimumHeight() <= 400  # resizable
    dlg.resize(700, 420)
    dlg.move(avail.left() + 33, avail.top() + 44)
    app.processEvents()
    dlg.reject()                                 # Cancel also remembers size/position
    assert s.ui_settings_dialog_geometry
    dlg2 = SettingsDialog(s)
    dlg2.show()
    app.processEvents()
    assert abs(dlg2.width() - 700) <= 2 and abs(dlg2.height() - 420) <= 2
    assert abs(dlg2.x() - (avail.left() + 33)) <= 2 and abs(dlg2.y() - (avail.top() + 44)) <= 2
    out = dlg2.result_settings(s)
    assert out.ui_settings_dialog_geometry == dlg2.saved_geometry()
    dlg2.reject()


def test_main_window_resizes_freely_without_clipping_and_remembers_layout(app, tmp_path):
    w, eng = _window(tmp_path)
    w.show()
    app.processEvents()
    # a small screen: nothing in the window demands more than 900×560
    assert w.minimumWidth() <= 900 and w.minimumHeight() <= 560
    assert w.centralWidget().minimumSizeHint().width() <= 900, w.centralWidget().minimumSizeHint()
    # very long status texts must not widen the minimum size (this is what used to push content off-screen)
    w.status_label.setText("cozy.markets: AUTH REQUIRED · maplestory.watch: ONLINE · Club FM (maplefm.gg): ONLINE — live mode · " * 2)
    app.processEvents()
    assert w.centralWidget().minimumSizeHint().width() <= 900
    for size in ((900, 560), (1280, 720), (1366, 728), (1920, 1040), (1000, 650)):
        w.resize(*size)
        app.processEvents()
        assert (w.width(), w.height()) == size
        # the three fields, Start and Settings are visible and not squeezed; the table fills the width
        for wd in (w.in_min_buy, w.in_max_buy, w.in_min_profit, w.btn_start, w.btn_settings):
            assert wd.isVisible() and wd.width() >= 90, (size, wd)
        assert w.table.width() >= size[0] - 40
        if size[0] >= 1280:                              # at 1280×720 every column fits without a horizontal scrollbar
            assert sum(w.table.columnWidth(i) for i in range(10)) <= w.table.viewport().width() + 2, size
    # maximize / restore keeps everything, and the layout survives close -> reopen
    scr = w._screen_rect()
    tw, th = max(900, min(1366, scr.width() - 40)), max(560, min(728, scr.height() - 40))
    w.resize(tw, th)
    w.showMaximized(); app.processEvents()
    w.showNormal(); app.processEvents()
    w.move(w._screen_rect().left() + 15, w._screen_rect().top() + 25)
    app.processEvents()
    w.tray.hide()                                   # so close() really closes (no tray in tests)
    w.close(); app.processEvents()
    assert w.settings.ui_main_window_geometry
    w2 = MainWindow(eng, w.settings, w.notifier)
    w2.show(); app.processEvents()
    fits = scr.width() >= tw and scr.height() >= th
    if fits:
        assert abs(w2.width() - tw) <= 2 and abs(w2.height() - th) <= 2
        assert w2._screen_rect().contains(w2.geometry())
    # a remembered position off any screen is pulled back on-screen
    from maple_flip_hunter.ui.settings_dialog import fit_on_screen
    r = fit_on_screen(QRect(-5000, -5000, 1366, 728), w2._screen_rect(), 1.0)
    assert w2._screen_rect().contains(r)
    w2.tray.hide(); w2.close()


def test_gone_rows_leave_the_active_table_and_details_show_last_verified(app, tmp_path):
    w, eng = _window(tmp_path)
    w.show()
    opp = asyncio.new_event_loop().run_until_complete(eng.ingest([make_listing(name="Gone Item", price=8_000, ref=42_000, seller="S", age_s=5)]))[0]
    w.model.upsert(opp); app.processEvents()
    assert w.proxy.rowCount() == 1
    w.table.selectRow(0); w.show_details(); app.processEvents()
    assert "Availability" in w.details.toPlainText() and "never" in w.details.toPlainText()
    eng._set_availability(opp, "SOLD", time.time(), "slot sold", "test")
    w.on_availability(opp, "SOLD", "slot sold", True); app.processEvents()
    assert w.proxy.rowCount() == 0 and w.model.rowCount() == 1          # hidden from the active table, kept in memory/DB
    assert eng.db.availability(opp.key)["status"] == "SOLD"
    w.proxy.show_gone = True; w.proxy.invalidateFilter(); app.processEvents()
    assert w.proxy.rowCount() == 1
    w.table.selectRow(0); w.show_details(); app.processEvents()
    txt = w.details.toPlainText()
    assert "SOLD" in txt and "Last verified" in txt and "slot sold" in txt
    w.tray.hide(); w.close()
