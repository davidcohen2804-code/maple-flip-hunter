"""'Physical Scanner' tab: start/stop FM Watcher (your own screen scans) and watch the rows it produces.
Uses the existing FM Watcher adapter, the existing pricing and the existing alert system — this is only a view and
a process switch. Internet sources are untouched by Start/Stop here."""
from __future__ import annotations

import html
import time
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..detection import passes_user_filters
from ..models import Level, Listing, Opportunity, SourceStatus
from .table_model import fmt_age
from .theme import GREEN, MUTED, RED, YELLOW

COLS = ["Shop", "Item", "Buy", "Market value", "Est. profit", "CH", "Location", "Scanned"]
MAX_ROWS = 2000


class PhysicalScannerTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.win = window                      # MainWindow: engine, settings, fmw_launcher, notifier
        self.items: dict[str, QTableWidgetItem] = {}   # listing_key -> first-column item (row() survives sorting)
        self.listings: dict[str, Listing] = {}
        self.opps: dict[str, Opportunity] = {}
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.status_pill = QLabel("OFFLINE")
        self.status_pill.setObjectName("pill")
        top.addWidget(self.status_pill)
        self.status_text = QLabel("")
        self.status_text.setObjectName("subtitle")
        self.status_text.setWordWrap(True)
        top.addWidget(self.status_text, 1)
        self.btn_start = QPushButton("▶  Start Scanning")
        self.btn_start.setStyleSheet(f"QPushButton {{ background: {GREEN}; color: #0f1419; font-weight: 700; padding: 8px 18px; }}")
        self.btn_start.clicked.connect(self.start_scanning)
        self.btn_stop = QPushButton("⏹  Stop Scanning")
        self.btn_stop.setStyleSheet(f"QPushButton {{ background: {RED}; color: #0f1419; font-weight: 700; padding: 8px 18px; }}")
        self.btn_stop.clicked.connect(self.stop_scanning)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        root.addLayout(top)

        hint = QLabel("FM Watcher reads the shop windows on your screen while you browse the Free Market (open Change Channel "
                      "once, keep the minimap visible, scroll each shop to the bottom). Rows that pass your profit filters are "
                      "green and ring through the normal alert system. History is kept in the database.")
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)

        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setAlternatingRowColors(True)
        self.table.setSortingEnabled(True)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(1, QHeaderView.Stretch)
        for i, w in ((0, 140), (2, 95), (3, 105), (4, 100), (5, 50), (6, 190), (7, 80)):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, w)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.table, 1)

        self.counter = QLabel("0 scanned rows")
        self.counter.setObjectName("subtitle")
        root.addWidget(self.counter)
        self.load_history()

    # ------------------------------------------------------------ process control
    def start_scanning(self) -> None:
        s = self.win.settings
        if not s.source_fmwatcher_dir.strip():
            self.set_status("ERROR", "FM Watcher folder not set — Settings → Sources → FM Watcher folder")
            return
        ok, msg = self.win.fmw_launcher.start()
        if not ok:
            self.set_status("ERROR", msg)
            return
        if not any(a.name == "fmwatcher" for a in self.win.engine.adapters) and self.win.engine.running:
            self.win.engine.stop()            # the adapter is built from settings at start
            self.win.engine.start()
        self.refresh_status()

    def stop_scanning(self) -> None:
        """Stops the physical scan only (FM Watcher process); internet sources keep running."""
        la = self.win.fmw_launcher
        if la.started_by_us:
            la.stop()
        else:
            ext = la.external_pids()
            if ext:
                try:
                    import psutil

                    for pid in ext:
                        p = psutil.Process(pid)
                        for c in p.children(recursive=True):
                            c.terminate()
                        p.terminate()
                except Exception:  # noqa: BLE001
                    pass
        la.error = ""
        self.refresh_status()

    def set_status(self, state: str, text: str) -> None:
        color = {"ONLINE": GREEN, "ERROR": RED}.get(state, MUTED if state == "OFFLINE" else YELLOW)
        self.status_pill.setText(state)
        self.status_pill.setStyleSheet(f"background:{color}22; color:{color}; border:1px solid {color};")
        self.status_text.setText(text)

    def refresh_status(self) -> None:
        state, text = self.win.fmw_launcher.status()
        adapter = next((a for a in self.win.engine.adapters if a.name == "fmwatcher"), None)
        if state == "error":
            self.set_status("ERROR", text)
        elif state == "running":
            detail = f" · {adapter.health.detail}" if adapter and adapter.health.detail else ""
            if adapter and adapter.health.status in (SourceStatus.ONLINE, SourceStatus.STARTING):
                self.set_status("ONLINE", text + detail)
            elif adapter is None:
                self.set_status("ONLINE", text + " · adapter starts with the scanner (Settings → Sources → FM Watcher folder)")
            else:
                self.set_status("ONLINE", f"{text} · {adapter.health.status.value}{detail}")
        else:
            self.set_status("OFFLINE", text if not self.win.settings.source_fmwatcher_dir.strip()
                            else text + " — press Start Scanning")
        self.btn_start.setEnabled(state != "running")
        self.btn_stop.setEnabled(state == "running")

    # ------------------------------------------------------------ rows
    def load_history(self) -> None:
        db = self.win.engine.db
        for o in db.opportunities_by_source("fmwatcher"):
            self.opps[o.key] = o
        for lst in reversed(db.listings_by_source("fmwatcher")):
            self.upsert(lst, from_history=True)

    def on_listing(self, lst: Listing, kind: str) -> None:
        if lst.source == "fmwatcher":
            self.upsert(lst)

    def on_opportunity(self, opp: Opportunity) -> None:
        if opp.listing.source == "fmwatcher":
            self.opps[opp.key] = opp
            self.upsert(opp.listing)

    def _qualifies(self, opp: Optional[Opportunity]) -> bool:
        if opp is None or opp.level not in (Level.JACKPOT, Level.GOOD) or opp.stale or opp.gone:
            return False
        return passes_user_filters(opp.listing, opp.valuation, self.win.settings)[0]

    def upsert(self, lst: Listing, from_history: bool = False) -> None:
        key = lst.listing_key
        self.listings[key] = lst
        opp = self.opps.get(key)
        sort_on = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        if key in self.items:
            r = self.items[key].row()
        else:
            r = 0
            self.table.insertRow(0)
            if self.table.rowCount() > MAX_ROWS:
                last = self.table.rowCount() - 1
                gone = next((k for k, it in self.items.items() if it.row() == last), None)
                self.table.removeRow(last)
                if gone:
                    self.items.pop(gone, None)
                    self.listings.pop(gone, None)
        v = opp.valuation if opp else None
        vals = [lst.seller, lst.item_name, f"{lst.price:,}", f"{v.reference:,}" if v and v.reference else "—",
                f"{v.net_profit:+,}" if v and v.net_profit is not None else "—",
                str(lst.channel) if lst.channel else "?", lst.map_name or lst.location, fmt_age(max(0.0, time.time() - lst.source_seen_at))]
        good = self._qualifies(opp)
        for c, val in enumerate(vals):
            it = QTableWidgetItem(val)
            if c in (2, 3, 4, 5, 7):
                it.setTextAlignment(int(Qt.AlignRight | Qt.AlignVCenter))
            if c == 2:
                it.setData(Qt.UserRole, lst.price)
            if c == 4 and v and v.net_profit is not None:
                it.setForeground(QBrush(QColor(GREEN if v.net_profit > 0 else RED)))
            if c == 7:
                it.setData(Qt.UserRole, -lst.source_seen_at)
            if good:
                it.setBackground(QBrush(QColor(GREEN + "26")))
                if c == 1:
                    it.setForeground(QBrush(QColor(GREEN)))
            elif lst.sold:
                it.setForeground(QBrush(QColor(MUTED)))
            if opp:
                it.setToolTip("\n".join([f"{opp.level.value} · {opp.availability}"] + v.explanation + opp.reasons))
            self.table.setItem(r, c, it)
            if c == 0:
                self.items[key] = it
        self.table.setSortingEnabled(sort_on)
        self.counter.setText(f"{self.table.rowCount():,} scanned rows · {sum(1 for k in self.items if self._qualifies(self.opps.get(k))):,} pass your filters")

    def refresh_ages(self) -> None:
        now = time.time()
        for key, first in self.items.items():
            lst = self.listings.get(key)
            it = self.table.item(first.row(), 7)
            if lst and it:
                it.setText(fmt_age(max(0.0, now - lst.source_seen_at)))
        self.refresh_status()

    def row_for_key(self, key: str) -> Optional[int]:
        it = self.items.get(key)
        return it.row() if it else None

    @staticmethod
    def escape(t: str) -> str:
        return html.escape(t)
