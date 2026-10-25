"""UI layout: 4-column table with full details on the right; Settings window fits the screen, scrolls,
keeps Save/Cancel visible, remembers geometry, never opens off-screen (1366×768, 1920×1080, scaled)."""
from __future__ import annotations

import asyncio
import os

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


def _window(tmp_path):
    s = Settings(min_buy_price=0, max_buy_price=1_000_000, min_net_profit=5_000)
    n = Notifier(enable_audio=False, sounds=ensure_sounds(tmp_path))
    eng = MarketEngine(s, Database(":memory:"), n, adapters=[])

    async def evidence(lst):
        r = lst.source_ref_price
        return make_evidence(lst, sold_median=r, sold_count=40, sales24=6, asks=(int(r * 1.03), int(r * 1.05), int(r * 1.1)))
    eng.gather_evidence = evidence  # type: ignore[method-assign]
    w = MainWindow(eng, s, n)
    return w, eng


def test_table_has_exactly_four_columns_and_full_details_on_the_right(app, tmp_path):
    w, eng = _window(tmp_path)
    w.resize(1366, 768)
    w.show()
    assert COLUMNS == ["Item", "Buy", "Market Value", "Quick Sale"]
    assert w.model.columnCount() == 4 and w.proxy.columnCount() == 4
    hh = w.table.horizontalHeader()
    assert hh.sectionResizeMode(0) == QHeaderView.Stretch and all(hh.sectionResizeMode(i) == QHeaderView.Fixed for i in (1, 2, 3))
    assert w.table.horizontalScrollBarPolicy() == Qt.ScrollBarAlwaysOff
    lst = make_listing(name="Bow Attack Scroll 60%", price=8_000, ref=42_000, seller="ShopX", age_s=5, channel=24, fm_room=25)
    opps = asyncio.new_event_loop().run_until_complete(eng.ingest([lst]))
    w.on_opportunity(opps[0]) if hasattr(w, "on_opportunity") else w.model.upsert(opps[0])
    app.processEvents()
    m = w.model
    texts = [m.data(m.index(0, c), Qt.DisplayRole) for c in range(4)]
    assert texts[0] == "Bow Attack Scroll 60%" and texts[1] == "8,000" and texts[2] == "42,000"
    assert texts[3] == f"{opps[0].valuation.quick:,}" and "," in texts[3]
    assert m.data(m.index(0, 0), Qt.ForegroundRole) is not None          # level colour on the item name
    assert m.data(m.index(0, 1), Qt.TextAlignmentRole) & Qt.AlignRight
    # no horizontal overflow at 1366 wide: columns fit the viewport
    w.table.resize(700, 400)
    app.processEvents()
    assert sum(w.table.columnWidth(i) for i in range(4)) <= w.table.viewport().width() + 2
    # clicking the row shows everything else on the right
    w.table.selectRow(0)
    app.processEvents()
    html = w.details.toPlainText()
    for needle in ("Net profit (quick)", "Net profit (conservative)", "ROI / Discount", "Confidence", "Liquidity",
                   "Source observed", "Location", "CH 24", "FM 25", "ShopX", "Item stats", "Source", "Quick sale price",
                   "Reference value", "Listing price", "8,000", "42,000"):
        assert needle in html, needle
    # sorting still works on every column
    for c in range(4):
        w.table.sortByColumn(c, Qt.DescendingOrder)
        app.processEvents()
    assert w.proxy.rowCount() == 1
    w.quit_app = lambda: None
    from maple_flip_hunter.ui.alert_popup import AlertPopup
    for pop in list(AlertPopup._open):
        pop.close()


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
    w.stat_labels["sources"].setText("cozy.markets: AUTH REQUIRED · maplestory.watch: ONLINE · Club FM (maplefm.gg): ONLINE — live mode · " * 2)
    app.processEvents()
    assert w.centralWidget().minimumSizeHint().width() <= 900
    for size in ((900, 560), (1366, 728), (1920, 1040), (1000, 650)):
        w.resize(*size)
        app.processEvents()
        assert (w.width(), w.height()) == size
        total = w.split.width()
        assert abs(sum(w.split.sizes()) + w.split.handleWidth() - total) <= 8   # panels fill the splitter
        assert w.table.width() >= 420 and w.details.parentWidget().width() >= 320
        assert sum(w.table.columnWidth(i) for i in range(4)) <= w.table.viewport().width() + 2
        assert w.btn_copy_loc.isVisible() and w.btn_copy_loc.width() > 40 and w.vol.isVisible()
    # draggable split: move it and the value is kept
    scr = w._screen_rect()
    tw, th = max(900, min(1366, scr.width() - 40)), max(560, min(728, scr.height() - 40))
    w.resize(tw, th)
    app.processEvents()
    want_right = int(w.split.width() * 0.42)
    w.split.setSizes([w.split.width() - want_right, want_right])
    app.processEvents()
    assert abs(w.split.sizes()[1] - want_right) <= 40, (w.split.sizes(), want_right)
    assert not w.split.childrenCollapsible()
    # maximize / restore keeps everything, and the layout survives close -> reopen
    w.showMaximized(); app.processEvents()
    w.showNormal(); app.processEvents()
    w.move(w._screen_rect().left() + 15, w._screen_rect().top() + 25)
    app.processEvents()
    w.tray.hide()                                   # so close() really closes (no tray in tests)
    w.close(); app.processEvents()
    assert w.settings.ui_main_window_geometry and w.settings.ui_main_splitter_state
    w2 = MainWindow(eng, w.settings, w.notifier)
    w2.show(); app.processEvents()
    fits = scr.width() >= tw and scr.height() >= th
    if fits:
        assert abs(w2.width() - tw) <= 2 and abs(w2.height() - th) <= 2
        assert w2._screen_rect().contains(w2.geometry())
    assert abs(w2.split.sizes()[1] - w.split.sizes()[1]) <= 40
    # a remembered position off any screen is pulled back on-screen
    from maple_flip_hunter.ui.settings_dialog import fit_on_screen
    r = fit_on_screen(QRect(-5000, -5000, 1366, 728), w2._screen_rect(), 1.0)
    assert w2._screen_rect().contains(r)
    w2.tray.hide(); w2.close()
