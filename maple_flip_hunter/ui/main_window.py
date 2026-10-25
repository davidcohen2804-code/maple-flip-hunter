from __future__ import annotations

import html
import logging
import time
from typing import Optional

from PySide6.QtCore import QByteArray, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMenu, QPushButton, QSlider, QSplitter, QSystemTrayIcon, QTableView,
                               QSizePolicy, QTextBrowser, QVBoxLayout, QWidget, QComboBox, QCheckBox)

from .. import APP_NAME, USER_AGENT, __version__
from ..collector.engine import MarketEngine
from ..collector.normalize import stats_signature
from ..config import Settings
from ..models import Level, Opportunity, SourceHealth
from .bridge import EngineBridge
from .alert_popup import AlertPopup
from .settings_dialog import SettingsDialog, fit_on_screen
from .startup import set_run_on_startup
from .table_model import OpportunityFilter, OpportunityModel, fmt_age
from .theme import ACCENT, GREEN, LEVEL_COLORS, MUTED, QSS, RED, STATE_COLORS, YELLOW

log = logging.getLogger(__name__)


def make_icon(color: str = ACCENT) -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QColor(color))
    p.setPen(Qt.NoPen)
    p.drawEllipse(4, 4, 56, 56)
    p.setBrush(QColor("#1a1200"))
    p.drawEllipse(22, 22, 20, 20)
    p.end()
    return QIcon(pm)


class Panel(QFrame):
    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("panel")


class MainWindow(QMainWindow):
    def __init__(self, engine: MarketEngine, settings: Settings, notifier) -> None:
        super().__init__()
        self.engine = engine
        self.settings = settings
        self.notifier = notifier
        self.setWindowTitle(f"{APP_NAME} v{__version__}")
        self.setWindowIcon(make_icon())
        self.setMinimumSize(900, 560)           # free resizing from every edge/corner above this
        self.resize(1480, 900)
        self.setStyleSheet(QSS)
        self._quitting = False
        self._icon_cache: dict[str, QByteArray] = {}
        self.net = QNetworkAccessManager(self)

        self.bridge = EngineBridge(engine)
        self.bridge.opportunity.connect(self.on_opportunity)
        self.bridge.alert.connect(self.on_alert)
        self.bridge.health.connect(self.on_health)
        self.bridge.listing.connect(self.on_listing)
        notifier.toast_callback = None  # toasts are handled from the alert signal on the UI thread

        self.model = OpportunityModel()
        self.proxy = OpportunityFilter()
        self.proxy.settings = settings
        self.proxy.setSourceModel(self.model)
        self._build()
        self._tray()
        for opp in engine.db.recent_opportunities(time.time() - 6 * 3600):
            self.model.upsert(opp)

        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_stats)
        self.tick.start(1000)
        self._new_listings = 0

    # ------------------------------------------------------------- layout
    def _build(self) -> None:
        central = QWidget()
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 14, 16, 12)
        root.setSpacing(12)

        # header
        header = QHBoxLayout()
        title = QLabel("MAPLE FLIP HUNTER")
        title.setObjectName("title")
        header.addWidget(title)
        self.state_pill = QLabel("OFFLINE")
        self.state_pill.setObjectName("pill")
        header.addWidget(self.state_pill)
        header.addStretch(1)
        self.btn_start = QPushButton("▶  Start scanner")
        self.btn_start.setObjectName("primary")
        self.btn_start.clicked.connect(self.toggle_scanner)
        self.btn_test = QPushButton("🔊  Test alarm")
        self.btn_test.clicked.connect(self._test_alarm)
        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.clicked.connect(self.open_settings)
        for b in (self.btn_test, self.btn_settings, self.btn_start):
            header.addWidget(b)
        root.addLayout(header)

        # stats strip
        strip = Panel()
        g = QGridLayout(strip)
        g.setContentsMargins(8, 8, 8, 8)
        g.setHorizontalSpacing(8)
        self.stat_labels: dict[str, QLabel] = {}
        for i, (key, label) in enumerate([
            ("sources", "Connected sources"), ("update", "Latest source update"), ("age", "Newest data age"),
            ("new", "New listings (session)"), ("latency", "Avg source→alert latency"),
            ("analysis", "Avg local analysis"), ("opps", "Opportunities (session)"), ("coverage", "Coverage"),
        ]):
            k = QLabel(label)
            k.setObjectName("stat")
            v = QLabel("—")
            v.setObjectName("statValue")
            v.setWordWrap(True)
            v.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)   # long texts never force the window wider
            v.setMinimumWidth(60)
            k.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
            g.setColumnStretch(i, 1)
            g.addWidget(k, 0, i)
            g.addWidget(v, 1, i)
            self.stat_labels[key] = v
        root.addWidget(strip)

        # banner (hidden until a jackpot fires)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        root.addWidget(self.banner)
        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

        # filters
        filt = QHBoxLayout()
        filt.setSpacing(10)
        self.cb_jp = QCheckBox("JACKPOT")
        self.cb_good = QCheckBox("GOOD FLIP")
        self.cb_unc = QCheckBox("UNCERTAIN")
        for cb in (self.cb_jp, self.cb_good, self.cb_unc):
            cb.setChecked(True)
            cb.toggled.connect(self._apply_filters)
            filt.addWidget(cb)
        filt.addSpacing(16)
        filt.addWidget(QLabel("Fresh:"))
        self.fresh = QComboBox()
        for label, secs in (("Any", None), ("Last 5 min", 300), ("Last 15 min", 900), ("Last 1 h", 3600)):
            self.fresh.addItem(label, secs)
        self.fresh.setCurrentIndex(2)
        self.fresh.currentIndexChanged.connect(self._apply_filters)
        filt.addWidget(self.fresh)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by item or shop…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        filt.addWidget(self.search, 1)
        filt.addWidget(QLabel("Alerts"))
        self.cb_notif = QCheckBox()
        self.cb_notif.setChecked(self.settings.notifications_enabled)
        self.cb_notif.toggled.connect(self._toggle_notifications)
        filt.addWidget(self.cb_notif)
        filt.addWidget(QLabel("Vol"))
        self.vol = QSlider(Qt.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setFixedWidth(110)
        self.vol.setValue(int(self.settings.alert_volume * 100))
        self.vol.valueChanged.connect(self._volume)
        filt.addWidget(self.vol)
        root.addLayout(filt)

        # table + details
        split = QSplitter(Qt.Horizontal)
        self.split = split
        split.setChildrenCollapsible(False)
        split.setHandleWidth(8)
        split.setStyleSheet("QSplitter::handle { background: #2a3442; border-radius: 3px; margin: 40px 2px; }"
                            "QSplitter::handle:hover { background: #f5a524; }")
        self.table = QTableView()
        self.table.setMinimumWidth(420)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(-1, Qt.AscendingOrder)      # no sort column = newest first; click a header to sort
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(36)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)      # Item takes the remaining width
        for i in (1, 2, 3):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, 120)
        hh.setMinimumSectionSize(90)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.table.setWordWrap(False)
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        split.addWidget(self.table)

        right = QWidget()
        right.setMinimumWidth(320)
        rv = QVBoxLayout(right)
        rv.setContentsMargins(0, 0, 0, 0)
        rv.setSpacing(8)
        sec = QLabel("OPPORTUNITY DETAILS")
        sec.setObjectName("section")
        rv.addWidget(sec)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setHtml(self._placeholder_html())
        rv.addWidget(self.details, 1)
        btns = QHBoxLayout()
        self.btn_copy_shop = QPushButton("📋 Copy shop name")
        self.btn_copy_loc = QPushButton("📋 Copy FM location")
        self.btn_copy_shop.clicked.connect(lambda: self._copy("shop"))
        self.btn_copy_loc.clicked.connect(lambda: self._copy("loc"))
        btns.addWidget(self.btn_copy_shop)
        btns.addWidget(self.btn_copy_loc)
        rv.addLayout(btns)
        split.addWidget(right)
        split.setStretchFactor(0, 3)
        split.setStretchFactor(1, 2)
        split.setSizes([980, 480])
        root.addWidget(split, 1)
        self._restore_layout()

        self.setCentralWidget(central)
        self.status = self.statusBar()
        self.status.showMessage("Ready. Press Start scanner.")
        self._set_state("OFFLINE")

    def _placeholder_html(self) -> str:
        return (f"<div style='color:{MUTED};padding:8px'>Select an opportunity to see item stats, comparable "
                f"listings, completed-sale data, the pricing explanation and the exact Free Market location.<br><br>"
                f"The scanner evaluates <b>every</b> listing from the connected sources automatically — "
                f"no watchlist is needed.</div>")

    # ---------------------------------------------------------------- tray
    def _tray(self) -> None:
        self.tray = QSystemTrayIcon(make_icon(), self)
        menu = QMenu()
        a_show = QAction("Show dashboard", self)
        a_show.triggered.connect(self.show_normal)
        a_toggle = QAction("Start / Stop scanner", self)
        a_toggle.triggered.connect(self.toggle_scanner)
        a_test = QAction("Test alarm", self)
        a_test.triggered.connect(self._test_alarm)
        a_quit = QAction("Quit", self)
        a_quit.triggered.connect(self.quit_app)
        for a in (a_show, a_toggle, a_test):
            menu.addAction(a)
        menu.addSeparator()
        menu.addAction(a_quit)
        self.tray.setContextMenu(menu)
        self.tray.activated.connect(lambda r: self.show_normal() if r == QSystemTrayIcon.Trigger else None)
        self.tray.setToolTip(APP_NAME)
        self.tray.show()

    # ------------------------------------------------------------ layout memory
    def _screen_rect(self) -> QRect:
        scr = self.screen() or QGuiApplication.primaryScreen()
        return scr.availableGeometry() if scr else QRect(0, 0, 1366, 768)

    def _restore_layout(self) -> None:
        s = self.settings
        if s.ui_main_window_geometry:
            try:
                self.restoreGeometry(QByteArray.fromBase64(s.ui_main_window_geometry.encode("ascii")))
            except Exception:  # noqa: BLE001
                pass
        if s.ui_main_splitter_state:
            try:
                self.split.restoreState(QByteArray.fromBase64(s.ui_main_splitter_state.encode("ascii")))
            except Exception:  # noqa: BLE001
                pass
        if not self.isMaximized():
            screen = self._screen_rect()
            wanted = self.geometry() if s.ui_main_window_geometry else QRect(0, 0, min(1480, screen.width() - 40),
                                                                             min(900, screen.height() - 40))
            if not s.ui_main_window_geometry:
                wanted.moveCenter(screen.center())
            self.setGeometry(fit_on_screen(wanted, screen, max_fraction=1.0))

    def save_layout(self) -> None:
        self.settings.ui_main_window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
        self.settings.ui_main_splitter_state = bytes(self.split.saveState().toBase64()).decode("ascii")
        try:
            self.settings.save()
        except Exception:  # noqa: BLE001
            log.exception("could not save window layout")

    def show_normal(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def closeEvent(self, event) -> None:  # noqa: N802
        self.save_layout()
        if self._quitting or not self.tray.isVisible():
            self.engine.stop()
            event.accept()
            return
        event.ignore()
        self.hide()
        self.tray.showMessage(APP_NAME, "Still scanning in the background. Double-click the tray icon to reopen.",
                              QSystemTrayIcon.Information, 3000)

    def quit_app(self) -> None:
        self._quitting = True
        self.save_layout()
        self.engine.stop()
        QApplication.instance().quit()

    # ------------------------------------------------------------ controls
    def toggle_scanner(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self.btn_start.setText("▶  Start scanner")
            self.status.showMessage("Scanner stopped.")
        else:
            self.engine.start()
            self.btn_start.setText("⏹  Stop scanner")
            self.status.showMessage("Scanner started — discovering the whole accessible market…")
        self._refresh_stats()

    def _test_alarm(self) -> None:
        """Play the sound of the highest enabled profit band (the one a JACKPOT-sized profit would trigger)."""
        enabled = [t for t in self.settings.sound_tiers if t.enabled]
        self.notifier.play(enabled[-1].sound if enabled else "jackpot")

    def _toggle_notifications(self, on: bool) -> None:
        self.settings.notifications_enabled = on
        self.settings.save()
        self.engine.update_settings(self.settings)

    def _volume(self, v: int) -> None:
        self.settings.alert_volume = v / 100
        self.notifier.set_volume(self.settings.alert_volume)
        self.settings.save()

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self, notifier=self.notifier)
        if not dlg.exec():
            self.settings.save()               # Cancel still remembers the window's size and position
            return
        if True:
            old = self.settings
            new = dlg.result_settings(self.settings)
            self.settings = new
            new.save()
            self.vol.blockSignals(True)
            self.vol.setValue(int(new.alert_volume * 100))
            self.vol.blockSignals(False)
            self.notifier.set_volume(new.alert_volume)
            self.engine.update_settings(new)
            self.cb_notif.setChecked(new.notifications_enabled)
            set_run_on_startup(new.run_on_windows_startup)
            self.proxy.settings = new
            self.proxy.invalidateFilter()      # existing rows that no longer pass disappear at once (DB keeps them)
            source_changed = any(getattr(old, k) != getattr(new, k) for k in new.model_fields if k.startswith("source_"))
            if source_changed and self.engine.running:
                self.engine.stop()
                self.engine.start()            # rebuilds adapters with fresh clients from the new settings
                self.status.showMessage("Settings saved — scanner restarted with the new source settings.")
            else:
                self.status.showMessage("Settings saved.")

    def _apply_filters(self) -> None:
        lv = set()
        if self.cb_jp.isChecked():
            lv.add(Level.JACKPOT)
        if self.cb_good.isChecked():
            lv.add(Level.GOOD)
        if self.cb_unc.isChecked():
            lv.add(Level.UNCERTAIN)
        self.proxy.show_levels = lv
        self.proxy.max_age_s = self.fresh.currentData()
        self.proxy.text = self.search.text().strip().lower()
        self.proxy.invalidateFilter()

    def _copy(self, what: str) -> None:
        opp = self._selected()
        if not opp:
            return
        txt = opp.listing.seller if what == "shop" else opp.listing.location
        QGuiApplication.clipboard().setText(txt)
        self.status.showMessage(f"Copied: {txt}", 3000)

    # -------------------------------------------------------------- events
    def on_listing(self, lst, kind: str) -> None:
        self._new_listings += 1

    def on_opportunity(self, opp: Opportunity) -> None:
        self.model.upsert(opp)

    def on_alert(self, opp: Opportunity, level: str, title: str, body: str) -> None:
        self.model.upsert(opp)
        l = opp.listing
        # always-on-top card: stays until OK; Mute stops the sound now and silences alerts for 60 s
        lat = (self.notifier.last_play_at - l.local_first_seen_at) * 1000 if self.notifier.last_play_at else None
        self.popup = AlertPopup(opp, level, on_mute=lambda: self.notifier.mute_for(60), latency_ms=lat)
        self.popup.show_stacked()
        if level == "JACKPOT":
            self.banner.setText(f"{title}<br>{html.escape(l.item_name)} — BUY {l.price:,} · QUICK SELL "
                                f"{opp.valuation.quick or 0:,} · NET {opp.valuation.net_profit or 0:,}<br>"
                                f"{html.escape(l.location)} · Shop: <b>{html.escape(l.seller)}</b>")
            self.banner.show()
            self.banner_timer.start(45_000)
            self.tray.showMessage(title, body, QSystemTrayIcon.Critical, 20_000)
            if self.isMinimized() or not self.isVisible():
                self.show_normal()
        else:
            self.tray.showMessage(title, body, QSystemTrayIcon.Information, 8_000)
        # select the alerted row
        for r in range(self.proxy.rowCount()):
            if self.model.opportunity_at(self.proxy.mapToSource(self.proxy.index(r, 0)).row()).key == opp.key:
                self.table.selectRow(r)
                break

    def on_health(self, health: list[SourceHealth]) -> None:
        self._refresh_stats(health)

    # ---------------------------------------------------------------- stats
    def _set_state(self, state: str) -> None:
        color = STATE_COLORS.get(state, MUTED)
        self.state_pill.setText(state)
        self.state_pill.setStyleSheet(f"background:{color}22; color:{color}; border:1px solid {color};")
        if hasattr(self, "tray"):
            self.tray.setIcon(make_icon(color if state != "OFFLINE" else MUTED))

    def _refresh_stats(self, health: Optional[list[SourceHealth]] = None) -> None:
        e = self.engine
        health = health or e.health()
        e._refresh_state()
        self._set_state(e.scanner_state)
        online = [h for h in health if h.status.value == "ONLINE"]
        self.stat_labels["sources"].setText(
            " · ".join(f"<span style='color:{GREEN if h.status.value=='ONLINE' else YELLOW if h.status.value in ('STALE','RATE LIMITED') else RED}'>"
                       f"{h.label}: {h.status.value}</span>" for h in health) or "none")
        last = max((h.last_success_at or 0) for h in health) if health else 0
        self.stat_labels["update"].setText(fmt_age(time.time() - last) + " ago" if last else "—")
        ages = [h.newest_data_age_s for h in online if h.newest_data_age_s is not None]
        self.stat_labels["age"].setText(fmt_age(min(ages)) if ages else "unknown")
        self.stat_labels["new"].setText(f"{e.metrics.listings_new:,}")
        m = e.metrics.snapshot()
        s2a = m.get("source_to_alert_avg_s")
        snd = self.notifier.sound_latency_ms
        snd_txt = f" · ingest→sound {sum(snd[-50:]) / len(snd[-50:]):.0f} ms" if snd else ""
        self.stat_labels["latency"].setText(f"{s2a:.1f} s (n={e.metrics.alerts}){snd_txt}" if s2a is not None else "no alerts yet")
        am = m.get("analysis_ms_avg")
        self.stat_labels["analysis"].setText(f"{am:.0f} ms" if am is not None else "—")
        self.stat_labels["opps"].setText(f"{sum(1 for o in self.model.rows if o.level != Level.NONE):,}")
        cov = e.coverage()
        self.stat_labels["coverage"].setText(
            f"{cov['items_with_listings']:,} items seen / {cov['catalog_items']:,} catalog · {cov['listings_1h']:,} rows last 1h")
        for key, lbl in self.stat_labels.items():
            if key != "coverage":
                lbl.setToolTip(lbl.text())
        self.stat_labels["coverage"].setToolTip(
            "Coverage = what the connected community scanners uploaded. Shops nobody scanned are NOT visible.")
        self.model.refresh_ages()
        if e.scanner_state == "LIVE":
            self.status.showMessage(f"LIVE · {e.metrics.listings_ingested:,} rows ingested · {e.metrics.evaluations:,} evaluated · "
                                    f"{e.metrics.alerts} alerts · dup alerts suppressed {e.metrics.duplicate_alerts_suppressed}")

    # -------------------------------------------------------------- details
    def _selected(self) -> Optional[Opportunity]:
        idx = self.table.selectionModel().selectedRows()
        if not idx:
            return None
        return self.model.opportunity_at(self.proxy.mapToSource(idx[0]).row())

    def _selection_changed(self, *_):
        opp = self._selected()
        if opp:
            self.details.setHtml(self.details_html(opp))
            if opp.listing.icon_url:
                self._load_icon(opp.listing.icon_url)

    def _load_icon(self, url: str) -> None:
        if url in self._icon_cache:
            self.details.document().addResource(2, QUrl(url), self._icon_cache[url])  # 2 = ImageResource
            self.details.setHtml(self.details.toHtml())
            return
        req = QNetworkRequest(QUrl(url))
        req.setHeader(QNetworkRequest.UserAgentHeader, USER_AGENT)
        reply = self.net.get(req)

        def done() -> None:
            if reply.error() == QNetworkReply.NoError:
                data = reply.readAll()
                self._icon_cache[url] = data
                self.details.document().addResource(2, QUrl(url), data)
                self.details.setHtml(self.details.toHtml())
            reply.deleteLater()
        reply.finished.connect(done)

    def details_html(self, opp: Opportunity) -> str:
        l, v = opp.listing, opp.valuation
        c = LEVEL_COLORS.get(opp.level.value, MUTED)
        esc = html.escape
        rows = []

        def row(k: str, val: str) -> None:
            rows.append(f"<tr><td style='color:{MUTED};padding:4px 14px 4px 0;white-space:nowrap'>{k}</td>"
                        f"<td style='padding:4px 0'>{val}</td></tr>")

        row("Listing price", f"<b>{l.price:,}</b> mesos" + (f" × {l.qty}" if l.qty > 1 else ""))
        row("Reference value", f"{v.reference:,}" if v.reference else "n/a")
        row("Quick sale price", f"{v.quick:,}" if v.quick else "n/a")
        row("Conservative sale", f"{v.conservative:,}" if v.conservative else "n/a")
        row("Net profit (quick)", f"<b style='color:{GREEN if (v.net_profit or 0) > 0 else RED}'>{v.net_profit:+,}</b>" if v.net_profit is not None else "n/a")
        row("Net profit (conservative)", f"{v.net_profit_conservative:+,}" if v.net_profit_conservative is not None else "n/a")
        row("ROI / Discount", (f"{v.roi:.0%}" if v.roi is not None else "n/a") + " / " + (f"{v.discount:.0%}" if v.discount is not None else "n/a"))
        row("Confidence", f"{v.confidence_label} ({v.confidence:.2f})")
        row("Liquidity", v.liquidity + (f" · {v.competitors} live competitors (cheapest {v.cheapest_competitor:,})" if v.competitors else " · no live competitors seen"))
        row("Seller / shop", f"<b>{esc(l.seller)}</b>" + (f" — “{esc(l.shop_title)}”" if l.shop_title else ""))
        row("Location", f"<b>{esc(l.location)}</b>" + (f" ({esc(l.map_name)})" if l.map_name and l.fm_room is not None else ""))
        row("Seller posted at", "unknown (sources do not report it)")
        row("Source observed", f"{time.strftime('%H:%M:%S', time.localtime(l.source_seen_at))} ({fmt_age(l.source_age_s)} ago)" +
            (f"; first seen by source {fmt_age(time.time()-l.source_first_seen_at)} ago" if l.source_first_seen_at else ""))
        row("Our first sight", f"{time.strftime('%H:%M:%S', time.localtime(l.local_first_seen_at))}")
        row("Analysis", f"{opp.analysis_ms:.0f} ms" + (f"; alerted {time.strftime('%H:%M:%S', time.localtime(opp.notified_at))}" if opp.notified_at else ""))
        row("Source", f"<a style='color:{ACCENT}' href='{esc(l.source_url or '')}'>{esc(l.source)}</a>")
        stats = stats_signature(l.stats).replace(",", " · ") if l.stats else ("<i>unknown</i>" if (l.category or "").lower() == "equip" else "n/a (not equipment)")
        row("Item stats", stats)
        icon = f"<img src='{esc(l.icon_url)}' width='32' height='32' style='vertical-align:middle;margin-right:10px'>" if l.icon_url else ""
        expl = "".join(f"<li style='margin:3px 0'>{esc(x)}</li>" for x in v.explanation) or "<li>—</li>"
        risks = "".join(f"<li style='margin:3px 0;color:{YELLOW}'>{esc(x)}</li>" for x in v.risks) or f"<li style='color:{MUTED}'>none recorded</li>"
        reasons = "".join(f"<li style='margin:3px 0'>{esc(x)}</li>" for x in opp.reasons)
        return f"""
        <div style='padding:4px 6px'>
          <div style='font-size:12px;font-weight:800;color:{c};letter-spacing:1px'>{opp.level.value}{' · STALE' if opp.stale else ''}</div>
          <div style='font-size:19px;font-weight:700;margin:6px 0 12px 0'>{icon}{esc(l.item_name)}
             <span style='color:{MUTED};font-size:12px;font-weight:400'> {esc(l.category or '')} {esc(l.subcategory or '')}</span></div>
          <table style='border-collapse:collapse'>{''.join(rows)}</table>
          <div style='margin-top:14px;color:{MUTED};font-weight:700'>PRICING EXPLANATION</div><ul style='margin:6px 0 0 18px;padding:0'>{expl}</ul>
          <div style='margin-top:12px;color:{MUTED};font-weight:700'>RISKS</div><ul style='margin:6px 0 0 18px;padding:0'>{risks}</ul>
          <div style='margin-top:12px;color:{MUTED};font-weight:700'>CLASSIFICATION</div><ul style='margin:6px 0 0 18px;padding:0'>{reasons}</ul>
        </div>"""
