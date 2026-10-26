"""Scanner Health: one row per source with status, last update, latency, rows, errors and a Restart button that
touches only that source; Copy diagnostics puts everything (incl. recent errors and UI stalls) on the clipboard."""
from __future__ import annotations

import time

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (QApplication, QDialog, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QTextEdit, QVBoxLayout)

from ..models import SourceStatus
from .table_model import fmt_age
from .theme import GREEN, MUTED, QSS, RED, YELLOW

COLS = ["Source", "Status", "Last update", "Latency", "Rows", "Errors", "Detail", ""]
COLOR = {SourceStatus.ONLINE: GREEN, SourceStatus.IDLE: GREEN, SourceStatus.STALE: YELLOW, SourceStatus.RATE_LIMITED: YELLOW,
         SourceStatus.ERROR: RED, SourceStatus.UNAVAILABLE: RED, SourceStatus.AUTH_REQUIRED: RED,
         SourceStatus.UNSUPPORTED: MUTED, SourceStatus.STARTING: MUTED}


class HealthDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.win = window
        self.setWindowTitle("Scanner Health — Maple Flip Hunter")
        self.setStyleSheet(QSS)
        self.setMinimumSize(900, 460)
        v = QVBoxLayout(self)
        v.setContentsMargins(20, 20, 20, 20)
        v.setSpacing(12)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(6, QHeaderView.Stretch)
        for i, w in ((0, 180), (1, 110), (2, 110), (3, 80), (4, 70), (5, 60), (7, 90)):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, w)
        v.addWidget(self.table, 2)
        v.addWidget(QLabel("Recent errors (newest last) · UI stalls · process usage"))
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        v.addWidget(self.log, 1)
        row = QHBoxLayout()
        self.btn_copy = QPushButton("📋 Copy diagnostics")
        self.btn_copy.clicked.connect(self.copy_diag)
        self.btn_close = QPushButton("Close")
        self.btn_close.clicked.connect(self.accept)
        row.addWidget(self.btn_copy)
        row.addStretch(1)
        row.addWidget(self.btn_close)
        v.addLayout(row)
        self.refresh()
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.refresh)
        self.timer.start(2000)

    def refresh(self) -> None:
        eng = self.win.engine
        hs = eng.health()
        self.table.setRowCount(len(hs))
        for r, h in enumerate(hs):
            vals = [h.label, h.status.value,
                    fmt_age(time.time() - h.last_success_at) + " ago" if h.last_success_at else "never",
                    f"{h.last_latency_ms:.0f} ms" if h.last_latency_ms else "—", f"{h.listings_received:,}",
                    str(h.errors), h.detail or (h.last_error or "")]
            for c, val in enumerate(vals):
                it = QTableWidgetItem(val)
                if c == 1:
                    it.setForeground(__import__("PySide6.QtGui", fromlist=["QBrush"]).QBrush(
                        __import__("PySide6.QtGui", fromlist=["QColor"]).QColor(COLOR.get(h.status, MUTED))))
                if c == 6:
                    it.setToolTip("\n".join(f"{time.strftime('%H:%M:%S', time.localtime(at))} {m}" for at, m in h.recent_errors[-10:]))
                self.table.setItem(r, c, it)
            btn = QPushButton("Restart")
            btn.setEnabled(eng.running and h.status != SourceStatus.UNSUPPORTED)
            btn.clicked.connect(lambda _=False, name=h.name: self.restart(name))
            self.table.setCellWidget(r, 7, btn)
        m = eng.metrics.snapshot()
        lines = []
        for h in hs:
            for at, msg in h.recent_errors[-5:]:
                lines.append(f"{time.strftime('%H:%M:%S', time.localtime(at))} [{h.name}] {msg}")
        wd = getattr(self.win, "watchdog", None)
        if wd is not None:
            lines.append(f"UI: worst stall {wd.worst_ms:.0f} ms · stalls {m.get('ui_stalls', 0)} · last lag {wd.last_lag_ms:.0f} ms")
        lines.append(f"process: RSS {m.get('rss_mb', 0):.0f} MB · CPU {m.get('cpu_percent', 0):.0f}% · "
                     f"ingested {m.get('listings_ingested', 0):,} · evaluations {m.get('evaluations', 0):,} · alerts {m.get('alerts', 0)}")
        self.log.setPlainText("\n".join(lines) or "no errors recorded")

    def restart(self, name: str) -> None:
        ok = self.win.engine.restart_source(name)
        self.win.status.showMessage(f"{name}: restart {'requested' if ok else 'not possible (scanner stopped?)'}", 5000)
        QTimer.singleShot(500, self.refresh)

    def copy_diag(self) -> None:
        QApplication.clipboard().setText(self.win.engine.diagnostics_text())
        self.btn_copy.setText("✓ Copied")
