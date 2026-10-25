from __future__ import annotations

from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QDoubleSpinBox, QFormLayout, QGroupBox,
                               QHBoxLayout, QLabel, QLineEdit, QSpinBox, QVBoxLayout)

from ..config import Settings


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


class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings — Maple Flip Hunter")
        self.setMinimumWidth(560)
        s = settings
        root = QVBoxLayout(self)
        root.setContentsMargins(20, 20, 20, 20)
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
        note = QLabel("Source changes take effect after Stop → Start.")
        note.setObjectName("subtitle")
        f4.addRow(note)
        root.addWidget(g4)

        bb = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        bb.accepted.connect(self.accept)
        bb.rejected.connect(self.reject)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(bb)
        root.addLayout(row)

    def result_settings(self, base: Settings) -> Settings:
        d = base.model_dump()
        d.update(
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
        )
        return Settings.model_validate(d)
