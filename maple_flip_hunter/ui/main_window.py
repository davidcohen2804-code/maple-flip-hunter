from __future__ import annotations

import html
import logging
import time
from typing import Optional

from PySide6.QtCore import QByteArray, QRect, Qt, QTimer, QUrl
from PySide6.QtGui import QAction, QColor, QGuiApplication, QIcon, QPainter, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (QApplication, QDialog, QFrame, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMainWindow, QMenu, QPushButton, QSpinBox, QSystemTrayIcon, QTableView,
                               QSizePolicy, QTabWidget, QTextBrowser, QVBoxLayout, QWidget, QComboBox)

from .. import APP_NAME, USER_AGENT, __version__
from ..collector.engine import MarketEngine
from ..collector.normalize import stats_signature
from ..config import Settings
from ..models import Level, Opportunity, SourceHealth, SourceStatus
from .bridge import EngineBridge
from ..fmwatcher_launcher import FMWatcherLauncher
from ..paths import log_dir
from .alert_popup import AlertPopup
from .physical_tab import PhysicalScannerTab
from .spots_tab import SpotsTab
from .alert_popup import SpotPopup
from .watchdog import UiWatchdog
from .health_dialog import HealthDialog
from .flow_layout import FlowLayout
from .settings_dialog import SettingsDialog, fit_on_screen
from .startup import set_run_on_startup
from .table_model import COLUMNS, OpportunityFilter, OpportunityModel, fmt_age
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
        self.bridge.availability.connect(self.on_availability)
        self.bridge.valued.connect(lambda lst, val, level: self.physical.on_valued(lst, val, level) if self.physical else None)
        self.bridge.listing.connect(self.on_listing)
        notifier.toast_callback = None  # toasts are handled from the alert signal on the UI thread
        if not notifier.attach_gui():    # creates the GUI-thread audio proxy so scanner-thread alerts can play
            log.warning("audio: GUI proxy could not be attached; automatic alerts will use the fallback backend")

        self.model = OpportunityModel()
        self.proxy = OpportunityFilter()
        self.proxy.settings = settings
        self.proxy.setSourceModel(self.model)
        self._build()
        self._tray()
        for opp in engine.db.recent_opportunities(time.time() - 6 * 3600):
            self.model.upsert(opp)

        self.watchdog = UiWatchdog(engine.metrics, self)
        self.tick = QTimer(self)
        self.tick.timeout.connect(self._refresh_stats)
        self.tick.start(1000)
        self.fmw_launcher = FMWatcherLauncher(self.settings.source_fmwatcher_dir, log_dir())
        self._fmw_status_text = ""
        if self.settings.physical_features_enabled:
            QTimer.singleShot(1500, self.autostart_fmwatcher)   # after the window is up (physical features only)
        self._new_listings = 0

    # ------------------------------------------------------------- layout
    def _build(self) -> None:
        """Simple main screen: three numbers → Start → alerts. Everything else is in Settings or on double-click."""
        central = QWidget()
        self.central = central
        root = QVBoxLayout(central)
        root.setContentsMargins(16, 12, 16, 10)
        root.setSpacing(10)

        # ---- row 1: title · state · Start/Stop · Settings
        top = QHBoxLayout()
        top.setSpacing(10)
        title = QLabel("MAPLE FLIP HUNTER")
        title.setObjectName("title")
        title.setMinimumWidth(0)
        title.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        top.addWidget(title, 1)
        self.state_pill = QLabel("STOPPED")
        self.state_pill.setObjectName("pill")
        self.state_pill.setToolTip("Scanner state — click for scanner health")
        self.state_pill.setCursor(Qt.PointingHandCursor)
        self.state_pill.mousePressEvent = lambda _e: HealthDialog(self).exec()     # type: ignore[assignment]
        top.addWidget(self.state_pill)
        self.btn_start = QPushButton("▶  Start")
        self.btn_start.setObjectName("primary")
        self.btn_start.setMinimumWidth(120)
        self.btn_start.clicked.connect(self.toggle_scanner)
        self.btn_settings = QPushButton("⚙  Settings")
        self.btn_settings.setToolTip("Advanced settings: sounds, profit bands, deal confidence, sources, startup")
        self.btn_settings.clicked.connect(self.open_settings)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_settings)
        root.addLayout(top)

        # ---- row 2: the three numbers that matter (wrap on narrow windows)
        header_host = QWidget()
        header = FlowLayout(header_host, h_spacing=14)

        def money_field(label: str, value: int, tip: str) -> QSpinBox:
            box = QWidget()
            h = QHBoxLayout(box)
            h.setContentsMargins(0, 0, 0, 0)
            h.setSpacing(6)
            k = QLabel(label)
            k.setObjectName("fieldLabel")
            k.setToolTip(tip)
            sp = QSpinBox()
            sp.setRange(0, 2_000_000_000)
            sp.setSingleStep(1_000)
            sp.setGroupSeparatorShown(True)
            sp.setSuffix(" mesos")
            sp.setValue(value)
            sp.setToolTip(tip)
            sp.setMinimumWidth(150)
            sp.setKeyboardTracking(False)          # one change per edit, not per keystroke
            sp.valueChanged.connect(self._filters_edited)
            h.addWidget(k)
            h.addWidget(sp)
            header.addWidget(box)
            return sp
        s = self.settings
        self.in_min_buy = money_field("Min Buy Price", s.min_buy_price, "Alert only on listings priced at or above this")
        self.in_max_buy = money_field("Max Buy Price", s.max_buy_price, "Alert only on listings priced at or below this")
        self.in_min_profit = money_field("Min Profit", s.min_net_profit, "Minimum estimated net profit (after the selling fee) to alert")
        root.addWidget(header_host)
        self._filter_timer = QTimer(self)                 # auto-save the three fields shortly after editing
        self._filter_timer.setSingleShot(True)
        self._filter_timer.timeout.connect(self._apply_main_filters)

        # ---- banner (hidden until a jackpot fires)
        self.banner = QLabel()
        self.banner.setObjectName("banner")
        self.banner.setWordWrap(True)
        self.banner.hide()
        root.addWidget(self.banner)
        self.banner_timer = QTimer(self)
        self.banner_timer.setSingleShot(True)
        self.banner_timer.timeout.connect(self.banner.hide)

        # ---- one light filter row: text search + freshness
        filt_host = QWidget()
        filt = FlowLayout(filt_host)
        filt.setSpacing(10)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Filter by item or seller…")
        self.search.setClearButtonEnabled(True)
        self.search.textChanged.connect(self._apply_filters)
        self.search.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.search.setMinimumWidth(160)
        filt.addWidget(self.search)
        filt.addWidget(QLabel("Show:"))
        self.fresh = QComboBox()
        for label, secs in (("Last 15 min", 900), ("Last 1 h", 3600), ("Last 6 h", 6 * 3600), ("Everything", None)):
            self.fresh.addItem(label, secs)
        self.fresh.setCurrentIndex(1)
        self.fresh.currentIndexChanged.connect(self._apply_filters)
        filt.addWidget(self.fresh)
        root.addWidget(filt_host)

        # ---- the deals table (full width; details open on double-click)
        self.table = QTableView()
        self.model.stale_after_s = float(self.settings.max_listing_age_s)
        self.table.setModel(self.proxy)
        self.table.setSortingEnabled(True)
        self.table.sortByColumn(COLUMNS.index("Net Profit"), Qt.DescendingOrder)   # best fresh deals on top
        self.table.setAlternatingRowColors(True)
        self.table.setSelectionBehavior(QTableView.SelectRows)
        self.table.setSelectionMode(QTableView.SingleSelection)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(34)
        hh = self.table.horizontalHeader()
        hh.setStretchLastSection(False)
        hh.setSectionResizeMode(0, QHeaderView.Stretch)      # Item takes the remaining width
        for name, w in (("Buy Price", 92), ("Sold Price", 92), ("Quick Sale", 92), ("Net Profit", 96), ("ROI %", 70),
                        ("Channel", 80), ("Room / Map", 100), ("Seller", 120), ("Age", 104)):
            i = COLUMNS.index(name)
            hh.setSectionResizeMode(i, QHeaderView.Interactive)
            self.table.setColumnWidth(i, w)
        hh.setMinimumSectionSize(44)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.table.setWordWrap(False)
        self.table.setToolTip("Double-click a row for the full breakdown. Sold Price = verified real sales only (N/A when unknown); "
                              "Quick Sale and Net Profit are estimates.")
        self.table.selectionModel().selectionChanged.connect(self._selection_changed)
        self.table.doubleClicked.connect(lambda _i: self.show_details())
        root.addWidget(self.table, 1)

        # ---- details live in a dialog (opened on double-click), not on the main screen
        self.details_dialog = QDialog(self)
        self.details_dialog.setWindowTitle("Deal details")
        self.details_dialog.setStyleSheet(QSS)
        self.details_dialog.resize(640, 720)
        dv = QVBoxLayout(self.details_dialog)
        dv.setContentsMargins(12, 12, 12, 12)
        dv.setSpacing(8)
        self.details = QTextBrowser()
        self.details.setOpenExternalLinks(True)
        self.details.setHtml(self._placeholder_html())
        dv.addWidget(self.details, 1)
        btns = QHBoxLayout()
        self.btn_copy_shop = QPushButton("📋 Copy seller name")
        self.btn_copy_loc = QPushButton("📋 Copy FM location")
        self.btn_copy_shop.clicked.connect(lambda: self._copy("shop"))
        self.btn_copy_loc.clicked.connect(lambda: self._copy("loc"))
        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.details_dialog.hide)
        btns.addWidget(self.btn_copy_shop)
        btns.addWidget(self.btn_copy_loc)
        btns.addStretch(1)
        btns.addWidget(btn_close)
        dv.addLayout(btns)
        self._restore_layout()

        self.physical = None
        self.spots = None
        if self.settings.physical_features_enabled:
            self.tabs = QTabWidget()
            self.tabs.addTab(central, "Market")
            self.physical = PhysicalScannerTab(self)
            self.tabs.addTab(self.physical, "Physical Scanner")
            self.spots = SpotsTab(self)
            self.tabs.addTab(self.spots, "Free Spot Detector")
            self.setCentralWidget(self.tabs)
        else:
            self.setCentralWidget(central)      # Market Scanner is the whole program
        self.status = self.statusBar()
        self.stats: dict[str, str] = {}
        self.sources_text = ""
        self.status_label = QLabel("Ready — set your three numbers and press Start. Double-click a deal for details.")
        self.status_label.setObjectName("stat")
        self.status_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Preferred)
        self.status.addWidget(self.status_label, 1)
        self._set_state("STOPPED")

    def _placeholder_html(self) -> str:
        return (f"<div style='color:{MUTED};padding:8px'>Double-click a deal in the table to see item stats, verified "
                f"sale data, the pricing explanation and the exact Free Market location.</div>")

    def show_details(self) -> None:
        opp = self._selected()
        if opp is None:
            return
        self.details.setHtml(self.details_html(opp))
        if opp.listing.icon_url:
            self._load_icon(opp.listing.icon_url)
        self.details_dialog.setWindowTitle(f"Deal details — {opp.listing.item_name}")
        self.details_dialog.show()
        self.details_dialog.raise_()

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

    # ------------------------------------------------------------ Friends Edition licence
    def attach_license(self, client) -> None:
        """Re-validate every 10 minutes; lock the window when the trial runs out while the app is open."""
        from .lock_dialog import LockDialog

        self.license_client = client
        self.setWindowTitle(self.windowTitle() + " — Friends Edition")
        self.license_label = QLabel("")
        self.license_label.setStyleSheet(f"color: {ACCENT}; padding: 0 12px; font-weight: 600;")
        self.status.addPermanentWidget(self.license_label)
        self.license_label.setText(client.state().remaining_text)

        def tick() -> None:
            client.refresh()
            st = client.state()
            if st.status in ("trial", "licensed"):
                self.license_label.setText(st.remaining_text)
                self._maybe_offer_update()
                return
            self.engine.stop()
            dlg = LockDialog(client, reason=st.message, parent=self)
            if dlg.exec() and dlg.unlocked:
                self.engine.start()
            else:
                self.quit_app()
        self.license_timer = QTimer(self)
        self.license_timer.timeout.connect(tick)
        self.license_timer.start(10 * 60 * 1000)
        QTimer.singleShot(5000, self._maybe_offer_update)

    def _maybe_offer_update(self) -> None:
        client = getattr(self, "license_client", None)
        if client is None or getattr(self, "_update_offered", False):
            return
        from ..licensing import update as upd

        m = upd.check(client.server, client.public_key)
        if not m:
            return
        self._update_offered = True
        from PySide6.QtWidgets import QMessageBox

        if QMessageBox.question(self, "Update available", f"Version {m['version']} is available (signed by the publisher). "
                                "Download and install now?") != QMessageBox.Yes:
            return
        path = upd.download(m)
        if path is None:
            QMessageBox.warning(self, "Update", "The download could not be verified and was discarded.")
            return
        if upd.apply(path):
            self.quit_app()

    # ------------------------------------------------------------ FM Watcher process
    def autostart_fmwatcher(self) -> None:
        s = self.settings
        if not (s.source_fmwatcher_autostart and s.source_fmwatcher_dir.strip()):
            return
        if not self.settings.physical_features_enabled:
            return
        if getattr(self, "physical", None) is not None and self.physical.stopped_by_user:
            return                                   # the user pressed Stop Scanning: no automatic restart
        if str(self.fmw_launcher.folder or "") != s.source_fmwatcher_dir.strip():
            self.fmw_launcher.stop()
            self.fmw_launcher = FMWatcherLauncher(s.source_fmwatcher_dir, log_dir())
        ok, msg = self.fmw_launcher.start()
        log.info("fmwatcher launcher: %s", msg)
        if ok:
            self.status.showMessage(f"FM Watcher: {msg}", 8000)
        else:
            self.status.showMessage(f"FM Watcher could not start — {msg}", 30000)
            if hasattr(self, "tray"):
                self.tray.showMessage("FM Watcher did not start", msg, QSystemTrayIcon.Warning, 10000)
        self._refresh_fmw_status()

    def _watch_fmwatcher_process(self) -> None:
        """Every 5 s: if a watcher WE started died, record ERROR and restart it (max 3 times per 10 min)."""
        la = self.fmw_launcher
        if not la.started_by_us or la.proc is None or la.proc.poll() is None:
            return
        if getattr(self.physical, "stopped_by_user", False):
            return
        code = la.proc.returncode
        now = time.time()
        hist = [t for t in getattr(self, "_fmw_restarts", []) if now - t < 600]
        a = next((x for x in self.engine.adapters if x.name == "fmwatcher"), None)
        if a is not None:
            a.health.recent_errors.append((now, f"FM Watcher process exited (code {code})"))
            del a.health.recent_errors[:-20]
        log.warning("FM Watcher process exited (code %s); restarts in last 10 min: %d", code, len(hist))
        la.started_by_us, la.proc = False, None
        if len(hist) >= 3:
            if a is not None:
                a._set_status(SourceStatus.ERROR, f"FM Watcher keeps exiting (code {code}) — see logs\\fmwatcher_launch.log; press Start Scanning to retry")
            self.tray.showMessage("FM Watcher stopped", f"It exited {len(hist) + 1} times in 10 minutes (code {code}). Not restarting automatically.",
                                  QSystemTrayIcon.Warning, 10000)
            return
        hist.append(now)
        self._fmw_restarts = hist
        ok, msg = la.start()
        self.status.showMessage(f"FM Watcher restarted automatically ({msg})" if ok else f"FM Watcher restart failed — {msg}", 10000)

    def _refresh_fmw_status(self) -> None:
        if not self.settings.physical_features_enabled or not self.settings.source_fmwatcher_dir.strip():
            self._fmw_status_text = ""
            return
        self._watch_fmwatcher_process()
        probe = getattr(self.physical, "_probe", None)
        if not probe:
            return
        state, text = probe["state"], probe["text"]
        color = {"running": GREEN, "error": RED}.get(state, YELLOW)
        self._fmw_status_text = f"<span style='color:{color}'>{html.escape(text)}</span>"

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
        if not self.isMaximized():
            screen = self._screen_rect()
            wanted = self.geometry() if s.ui_main_window_geometry else QRect(0, 0, min(1480, screen.width() - 40),
                                                                             min(900, screen.height() - 40))
            if not s.ui_main_window_geometry:
                wanted.moveCenter(screen.center())
            self.setGeometry(fit_on_screen(wanted, screen, max_fraction=1.0))

    def save_layout(self) -> None:
        self.settings.ui_main_window_geometry = bytes(self.saveGeometry().toBase64()).decode("ascii")
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
            self.fmw_launcher.stop()
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
        if getattr(self, "spots", None) is not None and self.spots.monitor is not None:
            self.spots.monitor.stop()
        self.fmw_launcher.stop()                  # only stops a watcher WE started
        self.engine.stop()
        QApplication.instance().quit()

    # ------------------------------------------------------------ controls
    def toggle_scanner(self) -> None:
        if self.engine.running:
            self.engine.stop()
            self.btn_start.setText("▶  Start")
            self._set_state("STOPPED")
            self.status_label.setText("Stopped. Press Start to scan again.")
        else:
            self._apply_main_filters()
            self.engine.start()
            self.btn_start.setText("⏹  Stop")
            self._set_state("STARTING")
            self.status_label.setText("Starting — connecting to the market sources…")
            QTimer.singleShot(1500, self._backfill_physical)
        self._refresh_stats()

    # the three main numbers: validated, saved automatically, applied to the live scanner and the table
    def _filters_edited(self, *_):
        self._filter_timer.start(600)

    def _apply_main_filters(self) -> None:
        lo, hi, prof = self.in_min_buy.value(), self.in_max_buy.value(), self.in_min_profit.value()
        if hi < lo:                                   # keep the range sane without nagging dialogs
            hi = lo
            self.in_max_buy.blockSignals(True)
            self.in_max_buy.setValue(hi)
            self.in_max_buy.blockSignals(False)
        changed = (lo, hi, prof) != (self.settings.min_buy_price, self.settings.max_buy_price, self.settings.min_net_profit)
        self.settings.min_buy_price, self.settings.max_buy_price, self.settings.min_net_profit = lo, hi, prof
        if changed:
            try:
                self.settings.save()
            except Exception:  # noqa: BLE001
                log.exception("could not save filters")
            self.engine.update_settings(self.settings)
            self.proxy.settings = self.settings
            self.proxy.invalidateFilter()
            self.status_label.setText(f"Filters saved: buy {lo:,}–{hi:,} mesos, profit ≥ {prof:,}.")

    def _sync_fields_from_settings(self) -> None:
        for sp, val in ((self.in_min_buy, self.settings.min_buy_price), (self.in_max_buy, self.settings.max_buy_price),
                        (self.in_min_profit, self.settings.min_net_profit)):
            sp.blockSignals(True)
            sp.setValue(val)
            sp.blockSignals(False)

    def _backfill_physical(self) -> None:
        if self.physical is None:
            return
        n = self.physical.backfill_valuations()
        if n:
            log.info("physical scanner: re-valuing %d stored scans without a valuation", n)

    def _test_alarm(self) -> None:
        """Play the sound of the highest enabled profit band (the one a JACKPOT-sized profit would trigger)."""
        enabled = [t for t in self.settings.sound_tiers if t.enabled]
        self.notifier.play(enabled[-1].sound if enabled else "jackpot")

    def _toggle_notifications(self, on: bool) -> None:
        self.settings.notifications_enabled = on
        self.settings.save()
        self.engine.update_settings(self.settings)

    def open_settings(self) -> None:
        dlg = SettingsDialog(self.settings, self, notifier=self.notifier, engine=self.engine)
        if not dlg.exec():
            self.settings.save()               # Cancel still remembers the window's size and position
            return
        if True:
            old = self.settings
            new = dlg.result_settings(self.settings)
            self.settings = new
            new.save()
            self.notifier.set_volume(new.alert_volume)
            self.engine.update_settings(new)
            self._sync_fields_from_settings()
            set_run_on_startup(new.run_on_windows_startup)
            self.proxy.settings = new
            self.model.stale_after_s = float(new.max_listing_age_s)
            self.proxy.invalidateFilter()      # existing rows that no longer pass disappear at once (DB keeps them)
            source_changed = any(getattr(old, k) != getattr(new, k) for k in new.model_fields if k.startswith("source_"))
            if (old.source_fmwatcher_dir, old.source_fmwatcher_autostart) != (new.source_fmwatcher_dir, new.source_fmwatcher_autostart):
                if not new.source_fmwatcher_autostart or old.source_fmwatcher_dir != new.source_fmwatcher_dir:
                    self.fmw_launcher.stop()
                QTimer.singleShot(200, self.autostart_fmwatcher)
            if source_changed and self.engine.running:
                self.engine.stop()
                self.engine.start()            # rebuilds adapters with fresh clients from the new settings
                self.status_label.setText("Settings saved — scanner restarted with the new source settings.")
            else:
                self.status_label.setText("Settings saved.")

    def _apply_filters(self) -> None:
        self.proxy.max_age_s = self.fresh.currentData()
        self.proxy.text = self.search.text().strip().lower()
        self.proxy.invalidateFilter()

    def _copy(self, what: str) -> None:
        opp = self._selected()
        if not opp:
            return
        txt = opp.listing.seller if what == "shop" else opp.listing.location
        QGuiApplication.clipboard().setText(txt)
        self.status_label.setText(f"Copied: {txt}")

    # -------------------------------------------------------------- events
    def on_listing(self, lst, kind: str) -> None:
        self._new_listings += 1
        if self.physical is not None:
            self.physical.on_listing(lst, kind)

    def on_opportunity(self, opp: Opportunity) -> None:
        self.model.upsert(opp)
        if self.physical is not None:
            self.physical.on_opportunity(opp)

    def on_alert(self, opp: Opportunity, level: str, title: str, body: str) -> None:
        if opp.listing.source != "test":
            self.model.upsert(opp)            # simulated test alerts never enter the opportunities table
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

    def on_availability(self, opp: Opportunity, status: str, note: str, changed: bool) -> None:
        self.model.upsert(opp)                       # row text/colour; the proxy drops REMOVED/SOLD rows
        if opp.gone:
            self.proxy.invalidateFilter()
        for pop in list(AlertPopup._open):
            if pop.opp.key == opp.key:
                pop.set_availability(status, opp.last_verified_at, note)
        sel = self._selected()
        if sel is not None and sel.key == opp.key and self.details_dialog.isVisible():
            self.details.setHtml(self.details_html(opp))

    def on_spot_alert(self, ev) -> None:
        """Free Spot Detector alert: distinct doorbell sound (profit bands do not apply) + floating card."""
        self.notifier.play(self.settings.spots_sound or "spot")
        log.info("SPOT_ALERT %s %s conf=%.2f", ev.spot.rating, ev.spot.location_text(), ev.confidence)
        pop = SpotPopup(ev, on_mute=lambda: self.notifier.mute_for(60))
        pop.show_stacked()
        if hasattr(self, "tray"):
            self.tray.showMessage(f"{ev.spot.rating} SPOT AVAILABLE", ev.spot.location_text(), QSystemTrayIcon.Critical, 15000)

    def on_health(self, health: list[SourceHealth]) -> None:
        self._refresh_stats(health)

    # ---------------------------------------------------------------- stats
    def _set_state(self, state: str) -> None:
        """User-facing states: LIVE / STOPPED / ERROR (plus STARTING while connecting)."""
        if state == "OFFLINE":
            state = "ERROR" if self.engine.running else "STOPPED"
        elif state == "DEGRADED":
            state = "LIVE"                  # some source down, data still flowing: the status line says which
        color = STATE_COLORS.get(state, MUTED)
        self.state_pill.setText(state)
        self.state_pill.setStyleSheet(f"background:{color}22; color:{color}; border:1px solid {color};")
        if hasattr(self, "tray"):
            self.tray.setIcon(make_icon(color if state != "OFFLINE" else MUTED))

    def _refresh_stats(self, health: Optional[list[SourceHealth]] = None) -> None:
        e = self.engine
        health = health or e.health()
        e._refresh_state()
        self._set_state(e.scanner_state if e.running else "STOPPED")
        online = [h for h in health if h.status.value == "ONLINE"]
        if int(time.time()) % 5 == 0:
            self._refresh_fmw_status()
        self.sources_text = " · ".join(f"{h.label}: {h.status.value}" for h in health) or "none"
        last = max((h.last_success_at or 0) for h in health) if health else 0
        ages = [h.newest_data_age_s for h in online if h.newest_data_age_s is not None]
        m = e.metrics.snapshot()
        s2a = m.get("source_to_alert_avg_s")
        cov = e.coverage()
        self.stats = {
            "sources": self.sources_text,
            "update": fmt_age(time.time() - last) + " ago" if last else "—",
            "age": fmt_age(min(ages)) if ages else "unknown",
            "new": f"{e.metrics.listings_new:,}",
            "latency": f"{s2a:.1f} s (n={e.metrics.alerts})" if s2a is not None else "no alerts yet",
            "analysis": f"{m.get('analysis_ms_avg'):.0f} ms" if m.get("analysis_ms_avg") is not None else "—",
            "opps": f"{sum(1 for o in self.model.rows if o.level != Level.NONE):,}",
            "coverage": f"{cov['items_with_listings']:,} items seen / {cov['catalog_items']:,} catalog · {cov['listings_1h']:,} rows last 1h",
        }
        self.watchdog.mark("stats tick")
        if e.running and not getattr(self, "_backfilled_once", False):
            self._backfilled_once = True
            QTimer.singleShot(3000, self._backfill_physical)
        self.model.refresh_ages()
        if self.physical is not None and int(time.time()) % 3 == 0:
            self.physical.refresh_ages()
        if e.running:
            failed = [h for h in health if h.status.value in ("ERROR", "AUTH REQUIRED", "UNAVAILABLE", "UNSUPPORTED")]
            starting = [h for h in health if h.status.value == "STARTING"]
            if e.scanner_state == "LIVE":
                text = (f"LIVE · {len(online)} source{'s' if len(online) != 1 else ''} online · newest data {self.stats['age']} · "
                        f"{e.metrics.listings_new:,} new listings · {self.stats['opps']} deals · {e.metrics.alerts} alerts")
            elif not online and starting:
                # first poll not finished yet: this is STARTING, not an error (maplestory.watch polls every 3 min)
                text = "STARTING · waiting for the first market data (" + ", ".join(h.label for h in starting) + ")…"
                self._set_state("STARTING")
            elif not online and failed and len(failed) == len(health):
                if all(h.status.value in ("UNSUPPORTED", "AUTH REQUIRED") for h in failed):
                    text = ("NO PERMITTED SOURCE · the market sites currently do not allow apps to read their data "
                            "(maplestory.watch Terms 2026-10-27) — waiting for the developer to obtain permission. Nothing is being polled.")
                    self._set_state("ERROR")
                else:
                    text = "ERROR · " + " · ".join(f"{h.label}: {h.last_error or h.status.value}" for h in failed) + " — click the state pill for details"
            else:
                text = f"{self.state_pill.text()} · {self.sources_text}"
            self.status_label.setText(text)
        self.status_label.setToolTip("\n".join(f"{k}: {v}" for k, v in self.stats.items()))

    # -------------------------------------------------------------- details
    def _selected(self) -> Optional[Opportunity]:
        idx = self.table.selectionModel().selectedRows()
        if not idx:
            return None
        return self.model.opportunity_at(self.proxy.mapToSource(idx[0]).row())

    def _selection_changed(self, *_):
        opp = self._selected()
        if opp and self.details_dialog.isVisible():
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
        row("Sold price (verified)", f"<b>{v.sold_price:,}</b> — {esc(v.sold_basis)}" if v.sold_price
            else f"<span style='color:{MUTED}'>N/A — no reliable completed-sale data (never estimated)</span>")
        row("Reference value", f"{v.reference:,}" if v.reference else "n/a")
        row("Quick sale price", f"{v.quick:,} <span style='color:{MUTED}'>(estimate, not guaranteed)</span>" if v.quick else "n/a")
        row("Conservative sale", f"{v.conservative:,}" if v.conservative else "n/a")
        row("Net profit (quick)", f"<b style='color:{GREEN if (v.net_profit or 0) > 0 else RED}'>{v.net_profit:+,}</b>" if v.net_profit is not None else "n/a")
        row("Net profit (conservative)", f"{v.net_profit_conservative:+,}" if v.net_profit_conservative is not None else "n/a")
        row("ROI / Discount", (f"{v.roi:.0%}" if v.roi is not None else "n/a") + " / " + (f"{v.discount:.0%}" if v.discount is not None else "n/a"))
        conf_color = {"HIGH": GREEN, "MEDIUM": YELLOW}.get(v.confidence_label, RED)
        row("Confidence", f"<b style='color:{conf_color}'>{v.confidence_label} — {v.confidence_score}/100</b>")
        row("Evidence", f"{v.sample_count} price samples from {v.unique_sellers} sellers"
            + (f" · spread ±{v.price_spread * 100:.0f}%" if v.price_spread is not None else "")
            + (f" · data {fmt_age(v.evidence_age_s)} old" if v.evidence_age_s is not None else ""))
        row("Why this grade", esc(v.confidence_reason) if v.confidence_reason else "—")
        row("Liquidity", v.liquidity + (f" · {v.competitors} live competitors (cheapest {v.cheapest_competitor:,})" if v.competitors else " · no live competitors seen"))
        avail_color = {"ACTIVE": GREEN, "SOLD": RED, "REMOVED": RED}.get(opp.availability, MUTED)
        row("Availability", f"<b style='color:{avail_color}'>{opp.availability}</b>"
            + (f" — {esc(opp.availability_note)}" if opp.availability_note else "")
            + ("<br><span style='color:%s'>ACTIVE = last seen by a source; not a promise it is still there</span>" % MUTED if opp.availability == "ACTIVE" else ""))
        row("Last verified", f"{time.strftime('%H:%M:%S', time.localtime(opp.last_verified_at))} ({fmt_age(time.time() - opp.last_verified_at)} ago)"
            if opp.last_verified_at else "never — no source has re-checked this listing yet")
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
