from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QByteArray, QRect, Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (QCheckBox, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout,
                               QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QMessageBox, QPushButton,
                               QScrollArea, QSlider, QSpinBox, QVBoxLayout, QWidget)

from ..config import Settings, SoundTier, validate_tiers
from ..notifications.sounds import sound_labels

NO_MAX = 100_000_000      # spin-box value that means "no upper bound"
MAX_HEIGHT_FRACTION = 0.80   # initial height ≤ 80 % of the available screen height


def fit_on_screen(wanted: QRect, screen: QRect, max_fraction: float = MAX_HEIGHT_FRACTION) -> QRect:
    """Pure geometry rule (unit-tested for 1366×768 / 1920×1080 / scaled screens): shrink to the screen, cap the
    height at `max_fraction` of the available height, and move the window fully inside the visible area."""
    w = min(wanted.width(), screen.width())
    h = min(wanted.height(), int(screen.height() * max_fraction))
    x = min(max(wanted.x(), screen.left()), screen.right() - w + 1)
    y = min(max(wanted.y(), screen.top()), screen.bottom() - h + 1)
    return QRect(x, y, w, h)


def _mesos(v: int, maximum: int = 50_000_000) -> QSpinBox:
    sb = QSpinBox()
    sb.setRange(0, maximum)
    sb.setSingleStep(1000)
    sb.setGroupSeparatorShown(True)
    sb.setValue(v)
    sb.setMinimumWidth(160)
    return sb


def _pct(v: float, maximum: float = 1.0) -> QDoubleSpinBox:
    sb = QDoubleSpinBox()
    sb.setRange(0.0, maximum * 100)
    sb.setDecimals(1)
    sb.setSuffix(" %")
    sb.setValue(v * 100)
    sb.setMinimumWidth(120)
    return sb


class TierRow:
    """Widgets of one profit band: [enabled] from [__] to [__] sound [v] (Preview) (Remove)."""

    def __init__(self, tier: SoundTier, labels: dict[str, str], preview, remove) -> None:
        self.enabled = QCheckBox(tier.name or "Tier")
        self.enabled.setChecked(tier.enabled)
        self.lo = _mesos(tier.min_profit, NO_MAX)
        self.hi = _mesos(NO_MAX if tier.max_profit is None else tier.max_profit, NO_MAX)
        self.hi.setSpecialValueText("")
        self.hi.setToolTip("Set to the maximum (scroll to the top) for 'no upper bound'")
        self.sound = QComboBox()
        for sid, label in labels.items():
            self.sound.addItem(label, sid)
        idx = self.sound.findData(tier.sound)
        self.sound.setCurrentIndex(idx if idx >= 0 else 0)
        self.preview = QPushButton("▶ Preview")
        self.preview.clicked.connect(lambda: preview(self.sound.currentData()))
        self.remove = QPushButton("✕")
        self.remove.setFixedWidth(32)
        self.remove.clicked.connect(lambda: remove(self))
        self.widgets = [self.enabled, self.lo, QLabel("to"), self.hi, self.sound, self.preview, self.remove]

    def to_tier(self) -> SoundTier:
        hi = self.hi.value()
        return SoundTier(name=self.enabled.text(), min_profit=self.lo.value(), max_profit=None if hi >= NO_MAX else hi,
                         sound=self.sound.currentData(), enabled=self.enabled.isChecked())


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None, notifier=None, engine=None):
        super().__init__(parent)
        self.notifier = notifier
        self.engine = engine
        self.setWindowTitle("Settings — Maple Flip Hunter")
        self.setMinimumSize(560, 360)
        self.setSizeGripEnabled(True)           # resizable + draggable like any normal window
        s = settings
        self._settings_in = settings
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QScrollArea.NoFrame)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.verticalScrollBar().setSingleStep(24)      # smooth wheel steps
        body = QWidget()
        self.scroll.setWidget(body)
        outer.addWidget(self.scroll, 1)
        root = QVBoxLayout(body)
        root.setContentsMargins(20, 20, 24, 20)
        root.setSpacing(14)

        g1 = QGroupBox("Profitability (applies to ALL items automatically)")
        f1 = QFormLayout(g1)
        f1.setContentsMargins(16, 20, 16, 16)
        f1.setSpacing(12)
        self.min_buy = _mesos(s.min_buy_price)
        self.max_buy = _mesos(s.max_buy_price)
        self.min_profit = _mesos(s.min_net_profit)
        self.fee = _pct(s.selling_fee, 0.5)
        self.val_min = _mesos(s.preferred_value_min)
        self.val_max = _mesos(s.preferred_value_max)
        f1.addRow("Minimum purchase price", self.min_buy)
        f1.addRow("Maximum purchase price", self.max_buy)
        f1.addRow("Minimum net profit", self.min_profit)
        f1.addRow("Selling fee", self.fee)
        f1.addRow("Preferred market value (min)", self.val_min)
        f1.addRow("Preferred market value (max)", self.val_max)
        root.addWidget(g1)

        g2 = QGroupBox("Classification")
        f2 = QFormLayout(g2)
        f2.setContentsMargins(16, 20, 16, 16)
        f2.setSpacing(12)
        self.jp_profit = _mesos(s.jackpot_min_profit)
        self.jp_roi_profit = _mesos(s.jackpot_roi_profit)
        self.jp_roi = _pct(s.jackpot_roi, 10)
        self.jp_conf = _pct(s.jackpot_min_confidence)
        self.good_conf = _pct(s.good_min_confidence)
        self.max_age = QSpinBox()
        self.max_age.setRange(60, 24 * 3600)
        self.max_age.setSuffix(" s")
        self.max_age.setValue(s.max_listing_age_s)
        f2.addRow("JACKPOT: net profit ≥", self.jp_profit)
        f2.addRow("JACKPOT: or ROI ≥", self.jp_roi)
        f2.addRow("   …with net profit ≥", self.jp_roi_profit)
        f2.addRow("JACKPOT: minimum confidence", self.jp_conf)
        f2.addRow("GOOD FLIP: minimum confidence", self.good_conf)
        f2.addRow("Treat listings older than", self.max_age)
        root.addWidget(g2)

        gq = QGroupBox("QUICK FLIPS ONLY (stricter gate for JACKPOT / GOOD FLIP alerts)")
        fq = QFormLayout(gq)
        fq.setContentsMargins(16, 20, 16, 16)
        fq.setSpacing(12)
        self.cb_quick = QCheckBox("Enable — alert only on fast, well-evidenced resales")
        self.cb_quick.setChecked(s.quick_flips_only)
        self.q_profit = _mesos(s.quick_flip_min_net_profit)
        self.q_conf = _pct(s.quick_flip_min_confidence)
        self.q_age = QSpinBox()
        self.q_age.setRange(30, 3600)
        self.q_age.setSuffix(" s")
        self.q_age.setValue(s.quick_flip_max_data_age_s)
        self.cb_q_liq = QCheckBox("Require HIGH liquidity (else MEDIUM is enough)")
        self.cb_q_liq.setChecked(s.quick_flip_require_high_liquidity)
        fq.addRow(self.cb_quick)
        fq.addRow("Min net profit at CONSERVATIVE sale (after fee)", self.q_profit)
        fq.addRow("Min confidence", self.q_conf)
        fq.addRow("Max age of listing and reference data", self.q_age)
        fq.addRow(self.cb_q_liq)
        root.addWidget(gq)

        g3 = QGroupBox("Alerts & behaviour")
        v3 = QVBoxLayout(g3)
        v3.setContentsMargins(16, 20, 16, 16)
        self.cb_notif = QCheckBox("Notifications enabled")
        self.cb_notif.setChecked(s.notifications_enabled)
        self.cb_jp = QCheckBox("Alarm for JACKPOT")
        self.cb_jp.setChecked(s.alert_jackpot)
        self.cb_good = QCheckBox("Sound for GOOD FLIP")
        self.cb_good.setChecked(s.alert_good)
        self.cb_unc = QCheckBox("Show UNCERTAIN opportunities in the table (never alarmed)")
        self.cb_unc.setChecked(s.show_uncertain)
        self.cb_min = QCheckBox("Start minimized to tray")
        self.cb_min.setChecked(s.start_minimized)
        self.cb_startup = QCheckBox("Run when Windows starts (current user only)")
        self.cb_startup.setChecked(s.run_on_windows_startup)
        for cb in (self.cb_notif, self.cb_jp, self.cb_good, self.cb_unc, self.cb_min, self.cb_startup):
            v3.addWidget(cb)
        root.addWidget(g3)

        gs = QGroupBox("Sound alerts by NET PROFIT (the profit the app computed, not the JACKPOT/GOOD label)")
        vs = QVBoxLayout(gs)
        vs.setContentsMargins(16, 20, 16, 16)
        hint = QLabel("Each band plays its own melodic sound. Bands may not overlap; a disabled band alerts silently. "
                      "Upper bound at maximum = unlimited.")
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        vs.addWidget(hint)
        self.tier_grid = QGridLayout()
        self.tier_grid.setHorizontalSpacing(8)
        self.tier_grid.setVerticalSpacing(6)
        grid_host = QWidget()
        grid_host.setLayout(self.tier_grid)
        vs.addWidget(grid_host)
        self.tier_rows: list[TierRow] = []
        self._labels = sound_labels()
        for t in s.sound_tiers:
            self._add_tier_row(t)
        row_btns = QHBoxLayout()
        self.btn_add_tier = QPushButton("+ Add band")
        self.btn_add_tier.clicked.connect(self._add_tier_clicked)
        row_btns.addWidget(self.btn_add_tier)
        self.btn_test_auto = QPushButton("🧪 Test Automatic Alert")
        self.btn_test_auto.setToolTip("Runs the real alert path from a background thread (like the scanner): "
                                      "profit band → sound → floating card. No market data is created or changed.")
        self.btn_test_auto.clicked.connect(self._test_automatic_alert)
        row_btns.addWidget(self.btn_test_auto)
        row_btns.addStretch(1)
        row_btns.addWidget(QLabel("Volume"))
        self.volume = QSlider(Qt.Horizontal)
        self.volume.setRange(0, 100)
        self.volume.setValue(int(s.alert_volume * 100))
        self.volume.setFixedWidth(160)
        self.volume.valueChanged.connect(self._volume_changed)
        row_btns.addWidget(self.volume)
        self.vol_label = QLabel(f"{self.volume.value()} %")
        row_btns.addWidget(self.vol_label)
        vs.addLayout(row_btns)
        root.addWidget(gs)

        g4 = QGroupBox("Sources")
        f4 = QFormLayout(g4)
        f4.setContentsMargins(16, 20, 16, 16)
        f4.setSpacing(12)
        self.cb_cozy = QCheckBox("cozy.markets (primary, ~10 s polling)")
        self.cb_cozy.setChecked(s.source_cozy_enabled)
        self.cozy_int = QDoubleSpinBox()
        self.cozy_int.setRange(5, 600)
        self.cozy_int.setSuffix(" s")
        self.cozy_int.setValue(s.source_cozy_interval_s)
        self.cb_msw = QCheckBox("maplestory.watch (secondary, ~3 min index)")
        self.cb_msw.setChecked(s.source_msw_enabled)
        self.msw_int = QDoubleSpinBox()
        self.msw_int.setRange(60, 3600)
        self.msw_int.setSuffix(" s")
        self.msw_int.setValue(s.source_msw_interval_s)
        f4.addRow(self.cb_cozy, self.cozy_int)
        self.cozy_token = QLineEdit(s.source_cozy_token)
        self.cozy_token.setPlaceholderText("access token, only if cozy.markets issues one")
        f4.addRow("   cozy.markets token", self.cozy_token)
        f4.addRow(self.cb_msw, self.msw_int)
        self.cb_clubfm = QCheckBox("Club FM (maplefm.gg) — needs their written permission")
        self.cb_clubfm.setChecked(s.source_clubfm_permission_granted)
        self.clubfm_token = QLineEdit(s.source_clubfm_token)
        self.clubfm_token.setPlaceholderText("token from Club FM (optional, for the live feed)")
        f4.addRow(self.cb_clubfm, self.clubfm_token)
        warn = QLabel("Tick Club FM only after they agreed in writing (Terms of Use §2). See docs/ACCESS_REQUESTS.md.")
        warn.setObjectName("subtitle")
        f4.addRow(warn)
        self.fmw_dir = QLineEdit(s.source_fmwatcher_dir)
        self.fmw_dir.setPlaceholderText(r"e.g. C:\Users\you\Documents\FMWatcher  (folder with start.bat)")
        fmw_row = QHBoxLayout()
        fmw_row.addWidget(self.fmw_dir, 1)
        self.btn_fmw_browse = QPushButton("Browse…")
        self.btn_fmw_browse.clicked.connect(self._browse_fmwatcher)
        fmw_row.addWidget(self.btn_fmw_browse)
        fmw_host = QWidget()
        fmw_host.setLayout(fmw_row)
        f4.addRow("FM Watcher folder (your own scans)", fmw_host)
        fmw_note = QLabel("Run Club FM's FM Watcher while you browse the FM; its local watcher_data_v2.json / my_scans.csv "
                          "become a live source here. Nothing is sent anywhere.")
        fmw_note.setObjectName("subtitle")
        fmw_note.setWordWrap(True)
        f4.addRow(fmw_note)
        note = QLabel("Source changes take effect after Stop → Start.")
        note.setObjectName("subtitle")
        f4.addRow(note)
        root.addWidget(g4)

        # button bar OUTSIDE the scroll area: always visible at the bottom
        self.button_bar = QWidget()
        self.button_bar.setObjectName("buttonBar")
        self.button_bar.setStyleSheet("QWidget#buttonBar { border-top: 1px solid #2a3442; }")
        row = QHBoxLayout(self.button_bar)
        row.setContentsMargins(20, 12, 20, 12)
        self.bb = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        self.bb.accepted.connect(self.accept)
        self.bb.rejected.connect(self.reject)
        row.addStretch(1)
        row.addWidget(self.bb)
        outer.addWidget(self.button_bar, 0)
        self._restore_geometry()

    # ------------------------------------------------------------ geometry
    def _screen_rect(self) -> QRect:
        scr = self.screen() or QGuiApplication.primaryScreen()
        return scr.availableGeometry() if scr else QRect(0, 0, 1366, 768)

    def _restore_geometry(self) -> None:
        saved = self._settings_in.ui_settings_dialog_geometry
        if saved:
            try:
                self.restoreGeometry(QByteArray.fromBase64(saved.encode("ascii")))
            except Exception:  # noqa: BLE001
                saved = ""
        screen = self._screen_rect()
        wanted = self.geometry() if saved else QRect(0, 0, 640, self.sizeHint().height() + 40)
        if not saved:   # centre on the screen the first time
            wanted.moveCenter(screen.center())
        self.setGeometry(fit_on_screen(wanted, screen))

    def saved_geometry(self) -> str:
        return bytes(self.saveGeometry().toBase64()).decode("ascii")

    def done(self, result: int) -> None:  # noqa: D401 - remember size/position on Save AND Cancel
        self._settings_in.ui_settings_dialog_geometry = self.saved_geometry()
        super().done(result)

    # ---------------------------------------------------------------- tiers
    def _add_tier_row(self, tier: SoundTier) -> None:
        r = TierRow(tier, self._labels, self._preview, self._remove_tier)
        self.tier_rows.append(r)
        self._relayout_tiers()

    def _relayout_tiers(self) -> None:
        while self.tier_grid.count():
            item = self.tier_grid.takeAt(0)
            if item.widget() is not None:
                item.widget().setParent(None)
        for i, r in enumerate(self.tier_rows):
            for c, w in enumerate(r.widgets):
                self.tier_grid.addWidget(w, i, c)

    def _add_tier_clicked(self) -> None:
        tiers = [r.to_tier() for r in self.tier_rows]
        start = max([t.max_profit for t in tiers if t.max_profit is not None] + [0]) + 1
        new = SoundTier(name=f"Band {len(tiers) + 1}", min_profit=start, max_profit=start + 9_999, sound="chime")
        self._add_tier_row(new)

    def _remove_tier(self, row: TierRow) -> None:
        self.tier_rows.remove(row)
        for w in row.widgets:
            w.setParent(None)
            w.deleteLater()
        self._relayout_tiers()

    def _preview(self, sound_id: str) -> None:
        if self.notifier is not None:
            self.notifier.play(sound_id, volume=self.volume.value() / 100)

    def _browse_fmwatcher(self) -> None:
        from PySide6.QtWidgets import QFileDialog

        d = QFileDialog.getExistingDirectory(self, "FM Watcher folder (contains start.bat)", self.fmw_dir.text() or "")
        if d:
            self.fmw_dir.setText(d)

    def _test_automatic_alert(self) -> None:
        if self.engine is not None:
            self.engine.simulate_alert()

    def _volume_changed(self, v: int) -> None:
        self.vol_label.setText(f"{v} %")

    def tiers(self) -> list[SoundTier]:
        return validate_tiers([r.to_tier() for r in self.tier_rows])

    def accept(self) -> None:  # noqa: D401
        try:
            self.tiers()
        except ValueError as exc:
            QMessageBox.warning(self, "Sound bands overlap", f"{exc}\n\nAdjust the bands so no two overlap.")
            return
        super().accept()

    def result_settings(self, base: Settings) -> Settings:
        d = base.model_dump()
        d.update(
            ui_settings_dialog_geometry=self.saved_geometry(),
            sound_tiers=[t.model_dump() for t in self.tiers()], alert_volume=self.volume.value() / 100,
            min_buy_price=self.min_buy.value(), max_buy_price=self.max_buy.value(), min_net_profit=self.min_profit.value(),
            quick_flips_only=self.cb_quick.isChecked(), quick_flip_min_net_profit=self.q_profit.value(),
            quick_flip_min_confidence=self.q_conf.value() / 100, quick_flip_max_data_age_s=self.q_age.value(),
            quick_flip_require_high_liquidity=self.cb_q_liq.isChecked(),
            selling_fee=self.fee.value() / 100, preferred_value_min=self.val_min.value(),
            preferred_value_max=self.val_max.value(), jackpot_min_profit=self.jp_profit.value(),
            jackpot_roi_profit=self.jp_roi_profit.value(), jackpot_roi=self.jp_roi.value() / 100,
            jackpot_min_confidence=self.jp_conf.value() / 100, good_min_confidence=self.good_conf.value() / 100,
            max_listing_age_s=self.max_age.value(), notifications_enabled=self.cb_notif.isChecked(),
            alert_jackpot=self.cb_jp.isChecked(), alert_good=self.cb_good.isChecked(),
            show_uncertain=self.cb_unc.isChecked(), start_minimized=self.cb_min.isChecked(),
            run_on_windows_startup=self.cb_startup.isChecked(), source_cozy_enabled=self.cb_cozy.isChecked(),
            source_cozy_interval_s=self.cozy_int.value(), source_msw_enabled=self.cb_msw.isChecked(),
            source_msw_interval_s=self.msw_int.value(), source_cozy_token=self.cozy_token.text().strip(),
            source_clubfm_permission_granted=self.cb_clubfm.isChecked(),
            source_clubfm_token=self.clubfm_token.text().strip(),
            source_fmwatcher_dir=self.fmw_dir.text().strip(),
        )
        return Settings.model_validate(d)
