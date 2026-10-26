"""'Physical Scanner' tab: start/stop FM Watcher (your own screen scans) and watch the rows it produces.
Uses the existing FM Watcher adapter, the existing pricing and the existing alert system — this is only a view and
a process switch. Internet sources are untouched by Start/Stop here."""
from __future__ import annotations

import html
import time
from typing import Optional

import threading

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QHeaderView, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QVBoxLayout, QWidget)

from ..detection import passes_user_filters
from ..models import Level, Listing, Opportunity, SourceStatus
from .table_model import fmt_age
from .theme import GREEN, MUTED, RED, YELLOW

COLS = ["Shop", "Item", "Buy", "Market value", "Est. profit", "CH", "Location", "Scanned"]
MAX_ROWS = 2000


class _AfterSignal(__import__("PySide6.QtCore", fromlist=["QObject"]).QObject):
    sig = __import__("PySide6.QtCore", fromlist=["Signal"]).Signal(object)

    def emit(self, payload) -> None:
        self.sig.emit(payload)


class PhysicalScannerTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.win = window                      # MainWindow: engine, settings, fmw_launcher, notifier
        self.items: dict[str, QTableWidgetItem] = {}   # listing_key -> first-column item (row() survives sorting)
        self.listings: dict[str, Listing] = {}
        self.opps: dict[str, Opportunity] = {}
        self.vals: dict[str, tuple] = {}       # listing_key -> (Valuation, Level) for every evaluated row
        self.stopped_by_user = False
        self._pending_revalue: set[str] = set()
        # incremental UI: incoming rows are queued and flushed in one batch (sorting/painting paused)
        self._pending: dict[str, Listing] = {}
        self._flush_timer = QTimer(self)
        self._flush_timer.setSingleShot(True)
        self._flush_timer.timeout.connect(self.flush)
        # the process/log probe runs in a background thread (psutil process_iter is slow on Windows)
        self._after = _AfterSignal()
        self._after.sig.connect(self._on_after)
        self._probe_lock = threading.Lock()
        self._probe = {"state": "stopped", "text": "FM Watcher not running", "tail": "", "at": 0.0}
        self._probe_running = False
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
        for i, w in ((0, 130), (2, 90), (3, 150), (4, 95), (5, 44), (6, 170), (7, 70)):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, w)
        self.table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        root.addWidget(self.table, 1)

        self.counter = QLabel("0 scanned rows")
        self.counter.setObjectName("subtitle")
        root.addWidget(self.counter)
        self.load_history()

    # ------------------------------------------------------------ process control
    def _adapter(self):
        return next((a for a in self.win.engine.adapters if a.name == "fmwatcher"), None)

    def start_scanning(self) -> None:
        """Responds at once: the process start (0.5 s + a process scan) runs on a worker thread."""
        s = self.win.settings
        if not s.source_fmwatcher_dir.strip():
            self.set_status("ERROR", "FM Watcher folder not set — Settings → Sources → FM Watcher folder")
            return
        if getattr(self, "_busy", False):
            return
        self._busy = True
        self.stopped_by_user = False
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.set_status("ONLINE", "starting FM Watcher…")
        a = self._adapter()
        if a is not None:
            a.paused = False                            # resume reading the scan file
        elif self.win.engine.running:
            self.win.engine.stop()                      # adapter is built from settings at start
            self.win.engine.start()
        la = self.win.fmw_launcher

        def work() -> None:
            ok, msg = la.start()                        # never a second copy (checks ours + external)
            self._after.emit(("start", ok, msg))
        threading.Thread(target=work, name="mfh-fmw-start", daemon=True).start()

    def _on_after(self, payload) -> None:
        what, ok, msg = payload
        self._busy = False
        if what == "start" and not ok:
            self.set_status("ERROR", msg)
            self.btn_start.setEnabled(True)
            return
        self.probe_now()
        QTimer.singleShot(300, self.refresh_status)
        self.refresh_status()

    def stop_scanning(self) -> None:
        """Stops the physical scan only: FM Watcher process (ours or started separately) AND the local file
        ingestion. Internet sources keep running; nothing restarts automatically. Responds at once; the
        process-tree kill runs on a worker thread."""
        if getattr(self, "_busy", False):
            return
        self._busy = True
        self.stopped_by_user = True
        a = self._adapter()
        if a is not None:
            a.paused = True
            a._set_status(SourceStatus.UNAVAILABLE, "stopped by you (Physical Scanner → Stop Scanning)")
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(False)
        self.set_status("OFFLINE", "stopping FM Watcher…")
        la = self.win.fmw_launcher

        def work() -> None:
            la.stop(force_external=True)
            la.error = ""
            self._after.emit(("stop", True, ""))
        threading.Thread(target=work, name="mfh-fmw-stop", daemon=True).start()

    def set_status(self, state: str, text: str) -> None:
        color = {"ONLINE": GREEN, "ERROR": RED}.get(state, MUTED if state == "OFFLINE" else YELLOW)
        self.status_pill.setText(state)
        self.status_pill.setStyleSheet(f"background:{color}22; color:{color}; border:1px solid {color};")
        self.status_text.setText(text)

    def _activity_text(self, adapter) -> str:
        """Distinguish 'running, nothing new' from 'stuck': last file write, rows in the last read, watcher.log tail."""
        if adapter is None:
            return ""
        parts = []
        if adapter.last_file_write:
            age = time.time() - adapter.last_file_write
            parts.append(f"scan file written {fmt_age(age)} ago")
        parts.append(f"{adapter.rows_last_read} rows in the last read · {adapter.rows_total_session} this session")
        tail = self._watcher_log_tail()
        if tail:
            parts.append(f"FM Watcher: “{tail}”")
        if adapter.last_file_write and time.time() - adapter.last_file_write > 180:
            parts.append("no new scans for 3+ min — if you are opening shops now, FM Watcher is not reading the game "
                         "(is the game window visible? did you open Change Channel once?)")
        return " · ".join(parts)

    def _watcher_log_tail(self) -> str:
        return self._probe.get("tail", "")

    @staticmethod
    def _read_log_tail(folder: str) -> str:
        try:
            import os

            p = os.path.join(folder, "watcher.log")
            with open(p, "rb") as f:
                f.seek(0, 2)
                f.seek(max(0, f.tell() - 4000))
                lines = [ln.strip() for ln in f.read().decode("utf-8", "replace").splitlines() if ln.strip()]
            return lines[-1][:160] if lines else ""
        except OSError:
            return ""

    def probe_now(self, wait: bool = False) -> None:
        """Refresh the process state + log tail off the UI thread (psutil.process_iter can take 100s of ms)."""
        if self._probe_running:
            return
        self._probe_running = True
        la, folder = self.win.fmw_launcher, self.win.settings.source_fmwatcher_dir.strip()

        def work() -> None:
            try:
                state, text = la.status()
                tail = self._read_log_tail(folder) if folder else ""
                with self._probe_lock:
                    self._probe = {"state": state, "text": text, "tail": tail, "at": time.time()}
            finally:
                self._probe_running = False
        t = threading.Thread(target=work, name="mfh-fmw-probe", daemon=True)
        t.start()
        if wait:
            t.join(5)

    def refresh_status(self) -> None:
        if time.time() - self._probe.get("at", 0) > 5:
            self.probe_now()
        with self._probe_lock:
            state, text = self._probe["state"], self._probe["text"]
        adapter = self._adapter()
        if state == "error":
            self.set_status("ERROR", text)
        elif state == "running" and not self.stopped_by_user:
            act = self._activity_text(adapter)
            if adapter is None:
                self.set_status("ONLINE", text + " · adapter starts with the scanner (Settings → Sources → FM Watcher folder)")
            elif adapter.health.status in (SourceStatus.ONLINE, SourceStatus.STARTING):
                self.set_status("ONLINE", f"{text}\n{act}")
            else:
                self.set_status("ONLINE", f"{text} · {adapter.health.status.value}: {adapter.health.detail}\n{act}")
        elif state == "running" and self.stopped_by_user:
            self.set_status("OFFLINE", "FM Watcher is still running (could not be stopped) — close its process manually")
        else:
            self.set_status("OFFLINE", text if not self.win.settings.source_fmwatcher_dir.strip()
                            else text + " — press Start Scanning")
        self.btn_start.setEnabled(state != "running" or self.stopped_by_user)
        self.btn_stop.setEnabled(state == "running" and not self.stopped_by_user)

    # ------------------------------------------------------------ rows
    def load_history(self) -> None:
        db = self.win.engine.db
        for o in db.opportunities_by_source("fmwatcher"):
            self.opps[o.key] = o
        try:
            self.vals.update(db.valuations_for_source("fmwatcher"))       # valuations persisted with the rows
        except Exception:  # noqa: BLE001
            pass
        self.table.setUpdatesEnabled(False)
        self.table.setSortingEnabled(False)
        try:
            for lst in reversed(db.listings_by_source("fmwatcher")):
                self.upsert(lst, from_history=True, batch=True)
        finally:
            self.table.setSortingEnabled(True)
            self.table.setUpdatesEnabled(True)
        self._update_counter()

    def _queue(self, lst: Listing) -> None:
        self._pending[lst.listing_key] = lst
        if not self._flush_timer.isActive():
            self._flush_timer.start(250)

    def flush(self) -> None:
        """Apply all queued rows in one go: one sort, one repaint — not one per row."""
        if not self._pending:
            return
        pending, self._pending = self._pending, {}
        self.table.setUpdatesEnabled(False)
        sort_on = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        try:
            for lst in pending.values():
                self.upsert(lst, batch=True)
        finally:
            self.table.setSortingEnabled(sort_on)
            self.table.setUpdatesEnabled(True)
        self._update_counter()

    def on_listing(self, lst: Listing, kind: str) -> None:
        if lst.source == "fmwatcher":
            self._queue(lst)

    def on_opportunity(self, opp: Opportunity) -> None:
        if opp.listing.source == "fmwatcher":
            self.opps[opp.key] = opp
            self._queue(opp.listing)

    def on_valued(self, lst: Listing, valuation, level) -> None:
        if lst.source == "fmwatcher":
            self.vals[lst.listing_key] = (valuation, level)
            self._pending_revalue.discard(lst.listing_key)
            self._queue(lst)

    def backfill_valuations(self, max_age_s: float = 24 * 3600) -> int:
        """Rows that never received a valuation (history from before a restart, or rows scanned while the scanner
        was stopped) are sent back through the pricing engine. Returns how many were queued."""
        now = time.time()
        todo = [lst for k, lst in self.listings.items()
                if k not in self.vals and k not in self._pending_revalue and lst.price >= 1000 and not lst.sold
                and now - lst.source_seen_at < max_age_s]
        if not todo or not self.win.engine.revalue(todo):
            return 0
        for lst in todo:
            self._pending_revalue.add(lst.listing_key)
            self._queue(lst)
        return len(todo)

    def _update_counter(self) -> None:
        self.counter.setText(f"{self.table.rowCount():,} scanned rows · {sum(1 for k in self.items if self._qualifies(self.opps.get(k))):,} pass your filters")

    def _no_price_reason(self, lst: Listing) -> str:
        v = self.vals.get(lst.listing_key)
        if v is not None and v[0].reference:
            return ""
        if v is not None:
            risks = v[0].risks + v[0].explanation
            if any("Unknown item" in r or "no reference" in r.lower() for r in risks):
                return "no price data: item not in the market index"
            return (risks[0][:60] if risks else "no reference price")
        if lst.price < 1000:
            return "not valued: under 1,000 mesos"
        if lst.listing_key in self._pending_revalue:
            return "valuing…"
        if not self.win.engine.running:
            return "not valued yet (scanner idle)"
        return "not valued yet"

    def _qualifies(self, opp: Optional[Opportunity]) -> bool:
        if opp is None or opp.level not in (Level.JACKPOT, Level.GOOD) or opp.stale or opp.gone:
            return False
        return passes_user_filters(opp.listing, opp.valuation, self.win.settings)[0]

    def upsert(self, lst: Listing, from_history: bool = False, batch: bool = False) -> None:
        key = lst.listing_key
        self.listings[key] = lst
        opp = self.opps.get(key)
        sort_on = self.table.isSortingEnabled() if not batch else False
        if not batch:
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
        v = opp.valuation if opp else (self.vals[key][0] if key in self.vals else None)
        reason = self._no_price_reason(lst) if not (v and v.reference) else ""
        vals = [lst.seller, lst.item_name, f"{lst.price:,}", f"{v.reference:,}" if v and v.reference else (reason or "—"),
                f"{v.net_profit:+,}" if v and v.net_profit is not None else "—",
                str(lst.channel) if lst.channel else "?", lst.map_name or lst.location, fmt_age(max(0.0, time.time() - lst.source_seen_at))]
        good = self._qualifies(opp)
        for c, val in enumerate(vals):
            it = QTableWidgetItem(val)
            if c in (2, 3, 4, 5, 7):
                it.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            if c == 2:
                it.setData(Qt.UserRole, lst.price)
            if c == 4 and v and v.net_profit is not None:
                it.setForeground(QBrush(QColor(GREEN if v.net_profit > 0 else RED)))
            if c == 3 and reason:
                it.setForeground(QBrush(QColor(MUTED)))
                it.setToolTip(reason)
            if c == 7:
                it.setData(Qt.UserRole, -lst.source_seen_at)
            if good:
                it.setBackground(QBrush(QColor(GREEN + "26")))
                if c == 1:
                    it.setForeground(QBrush(QColor(GREEN)))
            elif lst.sold or time.time() - lst.source_seen_at > self.win.settings.max_listing_age_s:
                it.setForeground(QBrush(QColor(MUTED)))          # old scan (older than the stale threshold): grey
            if opp:
                it.setToolTip("\n".join([f"{opp.level.value} · {opp.availability}"] + v.explanation + opp.reasons))
            elif v is not None and c != 3:
                it.setToolTip("\n".join(v.explanation + [f"risk: {r}" for r in v.risks]) or "valued: not an opportunity")
            self.table.setItem(r, c, it)
            if c == 0:
                self.items[key] = it
        if not batch:
            self.table.setSortingEnabled(sort_on)
            self._update_counter()

    def refresh_ages(self) -> None:
        """Called every few seconds from the UI tick; must stay cheap (no sorting, no painting per cell)."""
        now = time.time()
        sort_on = self.table.isSortingEnabled()
        self.table.setSortingEnabled(False)
        self.table.setUpdatesEnabled(False)
        try:
            for key, first in self.items.items():
                lst = self.listings.get(key)
                it = self.table.item(first.row(), 7)
                if lst and it:
                    txt = fmt_age(max(0.0, now - lst.source_seen_at))
                    if it.text() != txt:
                        it.setText(txt)
        finally:
            self.table.setUpdatesEnabled(True)
            self.table.setSortingEnabled(sort_on)
        self.refresh_status()

    def row_for_key(self, key: str) -> Optional[int]:
        it = self.items.get(key)
        return it.row() if it else None

    @staticmethod
    def escape(t: str) -> str:
        return html.escape(t)
