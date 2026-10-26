"""Free Spot Detector tab: Start/Stop Monitoring, status, scope settings (map / room / channels), a live
screenshot where you click to mark PRIME / GOOD spots, the spot list with observed statistics and a heat overlay,
recent free spots and the detection history. Capture: QScreen.grabWindow on the GUI thread (≈10 ms), analysis in
the monitor's background thread; frame rate bounded by Settings (default 1 fps)."""
from __future__ import annotations

import time
from typing import Optional

import numpy as np
from PySide6.QtCore import QObject, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
                               QMenu, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from ..spots import MonitorConfig, SpotMonitor, SpotRegistry
from ..spots.monitor import SpotEvent
from .table_model import fmt_age
from .theme import ACCENT, GREEN, MUTED, RED, YELLOW

RATING_COLOR = {"PRIME": RED, "GOOD": ACCENT, "NORMAL": MUTED}
COLS = ["Spot", "Rating", "State", "Last seen", "Vacated ×", "Avg occupied", "Avg retake", "Note"]


class _Relay(QObject):
    event = Signal(object)


class SpotView(QLabel):
    """Shows the latest frame scaled to fit; click = mark/rate the spot under the cursor."""

    def __init__(self, tab: "SpotsTab") -> None:
        super().__init__()
        self.tab = tab
        self.setMinimumHeight(240)
        self.setAlignment(Qt.AlignCenter)
        self.setStyleSheet("background:#0b0f14; border:1px solid #2a3442; border-radius:6px;")
        self._frame: Optional[np.ndarray] = None
        self._scale = 1.0
        self._off = (0, 0)
        self.show_heat = True

    def set_frame(self, frame: np.ndarray) -> None:
        self._frame = frame
        self._render()

    def _render(self) -> None:
        if self._frame is None:
            return
        h, w = self._frame.shape[:2]
        rgb = np.ascontiguousarray(self._frame[..., ::-1])
        img = QImage(rgb.data, w, h, 3 * w, QImage.Format_RGB888).copy()
        pix = QPixmap.fromImage(img)
        painter = QPainter(pix)
        mon = self.tab.monitor
        reg = self.tab.registry
        dx, dy = (mon._cam_dx, mon._cam_dy) if mon else (0.0, 0.0)
        for s in reg.in_scope(self.tab.cfg.map_name, self.tab.cfg.room, self.tab.cfg.channel):
            px, py = int(s.x + dx), int(s.y + dy)
            color = QColor(RATING_COLOR.get(s.rating, MUTED))
            if self.show_heat and s.vacate_count:
                heat = QColor(255, 80, 0, min(160, 30 + 25 * s.vacate_count))
                painter.setPen(Qt.NoPen)
                painter.setBrush(heat)
                painter.drawEllipse(px - 55, py - 110, 110, 150)
            pen = QPen(color, 3 if s.rating != "NORMAL" else 1)
            painter.setPen(pen)
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(px - 50, py - 105, 100, 140)
            painter.drawText(px - 48, py - 110, f"{s.rating} · {s.state}")
        painter.end()
        scaled = pix.scaled(self.width() - 4, self.height() - 4, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        self._scale = scaled.width() / max(1, w)
        self._off = ((self.width() - scaled.width()) // 2, (self.height() - scaled.height()) // 2)
        self.setPixmap(scaled)

    def resizeEvent(self, e) -> None:  # noqa: N802
        super().resizeEvent(e)
        self._render()

    def mousePressEvent(self, e) -> None:  # noqa: N802
        if self._frame is None:
            return
        x = int((e.position().x() - self._off[0]) / self._scale)
        y = int((e.position().y() - self._off[1]) / self._scale)
        mon = self.tab.monitor
        dx, dy = (mon._cam_dx, mon._cam_dy) if mon else (0.0, 0.0)
        sx, sy = int(x - dx), int(y - dy)
        menu = QMenu(self)
        for rating in ("PRIME", "GOOD", "NORMAL"):
            a = menu.addAction(f"Mark as {rating}")
            a.triggered.connect(lambda _=False, r=rating: self.tab.mark_spot(sx, sy, r))
        existing = self.tab.registry.find(self.tab.cfg.map_name, self.tab.cfg.room, self.tab.cfg.channel, sx, sy)
        if existing:
            d = menu.addAction("Remove this spot")
            d.triggered.connect(lambda: self.tab.remove_spot(existing.spot_id))
        menu.exec(e.globalPosition().toPoint())


class SpotsTab(QWidget):
    def __init__(self, window) -> None:
        super().__init__()
        self.win = window
        self.registry = SpotRegistry(window.engine.db.conn, window.engine.db._lock)
        self.monitor: Optional[SpotMonitor] = None
        self.relay = _Relay()
        self.relay.event.connect(self._on_event)
        self.recent: list[SpotEvent] = []
        s = window.settings
        self.cfg = MonitorConfig(map_name=s.spots_map_name, room=s.spots_room, channel=s.spots_channel,
                                 alert_ratings=tuple(s.spots_alert_ratings), fps=s.spots_fps,
                                 vacate_confirm_s=s.spots_vacate_confirm_s, scopes=list(s.spots_scopes))
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 8, 0, 0)
        root.setSpacing(10)

        top = QHBoxLayout()
        self.status_pill = QLabel("OFFLINE")
        self.status_pill.setObjectName("pill")
        top.addWidget(self.status_pill)
        self.status_text = QLabel("Press Start Monitoring with the game visible on screen.")
        self.status_text.setObjectName("subtitle")
        self.status_text.setWordWrap(True)
        top.addWidget(self.status_text, 1)
        self.btn_start = QPushButton("▶  Start Monitoring")
        self.btn_start.setStyleSheet(f"QPushButton {{ background: {GREEN}; color: #0f1419; font-weight: 700; padding: 8px 18px; }}")
        self.btn_start.clicked.connect(self.start_monitoring)
        self.btn_stop = QPushButton("⏹  Stop Monitoring")
        self.btn_stop.setStyleSheet(f"QPushButton {{ background: {RED}; color: #0f1419; font-weight: 700; padding: 8px 18px; }}")
        self.btn_stop.clicked.connect(self.stop_monitoring)
        self.btn_stop.setEnabled(False)
        top.addWidget(self.btn_start)
        top.addWidget(self.btn_stop)
        root.addLayout(top)

        scope = QHBoxLayout()
        scope.addWidget(QLabel("Map"))
        self.map_edit = QLineEdit(self.cfg.map_name)
        self.map_edit.setPlaceholderText("Free Market / Kerning City Construction Site")
        scope.addWidget(self.map_edit, 2)
        scope.addWidget(QLabel("FM room"))
        self.room_spin = QSpinBox()
        self.room_spin.setRange(0, 99)
        self.room_spin.setSpecialValueText("—")
        self.room_spin.setValue(self.cfg.room or 0)
        scope.addWidget(self.room_spin)
        scope.addWidget(QLabel("Channel"))
        self.ch_spin = QSpinBox()
        self.ch_spin.setRange(0, 99)
        self.ch_spin.setSpecialValueText("—")
        self.ch_spin.setValue(self.cfg.channel or 0)
        scope.addWidget(self.ch_spin)
        scope.addWidget(QLabel("Alert on"))
        self.cb_prime = QCheckBox("PRIME")
        self.cb_prime.setChecked("PRIME" in self.cfg.alert_ratings)
        self.cb_good = QCheckBox("GOOD")
        self.cb_good.setChecked("GOOD" in self.cfg.alert_ratings)
        scope.addWidget(self.cb_prime)
        scope.addWidget(self.cb_good)
        scope.addWidget(QLabel("Channels to watch"))
        self.channels_edit = QLineEdit(self._scopes_to_text())
        self.channels_edit.setPlaceholderText("e.g. 1-5, 7, 12  (empty = all)")
        scope.addWidget(self.channels_edit, 1)
        self.btn_apply = QPushButton("Apply")
        self.btn_apply.clicked.connect(self.apply_scope)
        scope.addWidget(self.btn_apply)
        root.addLayout(scope)

        split = QSplitter(Qt.Vertical)
        self.view = SpotView(self)
        split.addWidget(self.view)
        bottom = QWidget()
        bl = QHBoxLayout(bottom)
        bl.setContentsMargins(0, 0, 0, 0)
        self.table = QTableWidget(0, len(COLS))
        self.table.setHorizontalHeaderLabels(COLS)
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.Stretch)
        for i, w in ((1, 70), (2, 90), (3, 80), (4, 70), (5, 95), (6, 90), (7, 160)):
            hh.setSectionResizeMode(i, QHeaderView.Fixed)
            self.table.setColumnWidth(i, w)
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._table_menu)
        bl.addWidget(self.table, 3)
        right = QVBoxLayout()
        lbl = QLabel("RECENT FREE SPOTS / HISTORY")
        lbl.setObjectName("section")
        right.addWidget(lbl)
        self.filter = QComboBox()
        for f in ("All", "PRIME", "GOOD", "NORMAL"):
            self.filter.addItem(f)
        self.filter.currentIndexChanged.connect(self.refresh_lists)
        right.addWidget(self.filter)
        self.history = QTableWidget(0, 3)
        self.history.setHorizontalHeaderLabels(["When", "Event", "Spot"])
        self.history.verticalHeader().setVisible(False)
        self.history.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.history.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        right.addWidget(self.history, 1)
        rw = QWidget()
        rw.setLayout(right)
        bl.addWidget(rw, 2)
        split.addWidget(bottom)
        split.setSizes([360, 300])
        root.addWidget(split, 1)
        hint = QLabel("Click a hut in the picture to mark that exact spot PRIME / GOOD / NORMAL (marks are saved). Alerts fire only after a spot "
                      "was occupied, then not seen for the confirmation time on a still camera, same map, with no window covering it. "
                      "Nothing is sent to the game.")
        hint.setObjectName("subtitle")
        hint.setWordWrap(True)
        root.addWidget(hint)
        self.refresh_lists()
        self.ui_timer = QTimer(self)
        self.ui_timer.timeout.connect(self._tick)
        self.ui_timer.start(1000)

    # ------------------------------------------------------------------ scope
    def _scopes_to_text(self) -> str:
        chans: list[int] = []
        for sc in self.cfg.scopes:
            chans += sc.get("channels") or []
        return ", ".join(str(c) for c in sorted(set(chans)))

    @staticmethod
    def parse_channels(text: str) -> list[int]:
        out: set[int] = set()
        for part in text.replace(";", ",").split(","):
            part = part.strip()
            if not part:
                continue
            if "-" in part:
                a, b = part.split("-", 1)
                if a.strip().isdigit() and b.strip().isdigit():
                    out.update(range(int(a), int(b) + 1))
            elif part.isdigit():
                out.add(int(part))
        return sorted(out)

    def apply_scope(self) -> None:
        self.cfg.map_name = self.map_edit.text().strip() or "Free Market"
        self.cfg.room = self.room_spin.value() or None
        self.cfg.channel = self.ch_spin.value() or None
        ratings = tuple(r for r, cb in (("PRIME", self.cb_prime), ("GOOD", self.cb_good)) if cb.isChecked())
        self.cfg.alert_ratings = ratings or ("PRIME",)
        chans = self.parse_channels(self.channels_edit.text())
        self.cfg.scopes = [{"map": self.cfg.map_name, "room": self.cfg.room, "channels": chans, "enabled": True}] if chans else []
        s = self.win.settings
        s.spots_map_name, s.spots_room, s.spots_channel = self.cfg.map_name, self.cfg.room, self.cfg.channel
        s.spots_alert_ratings, s.spots_scopes = list(self.cfg.alert_ratings), list(self.cfg.scopes)
        try:
            s.save()
        except Exception:  # noqa: BLE001
            pass
        if self.monitor:
            self.monitor.cfg = self.cfg
        self.refresh_lists()
        self.status_text.setText(f"Scope: {self.cfg.map_name} · FM {self.cfg.room or '—'} · CH {self.cfg.channel or '—'} · "
                                 f"alert on {', '.join(self.cfg.alert_ratings)}" + (f" · channels {chans}" if chans else ""))

    # ------------------------------------------------------------------ capture
    def capture(self) -> Optional[np.ndarray]:
        """Called from the monitor thread: grab on the GUI thread via a blocking queued call is complex; instead the
        GUI grabs every tick into self._latest and the monitor consumes it."""
        return getattr(self, "_latest", None)

    def _grab(self) -> None:
        try:
            from PySide6.QtGui import QGuiApplication

            scr = QGuiApplication.primaryScreen()
            if scr is None:
                return
            rect = self.win.settings.spots_capture_rect
            if rect and len(rect) == 4:
                pix = scr.grabWindow(0, rect[0], rect[1], rect[2], rect[3])
            else:
                pix = scr.grabWindow(0)
            img = pix.toImage().convertToFormat(QImage.Format_RGB888)
            w, h = img.width(), img.height()
            ptr = img.constBits()
            arr = np.frombuffer(ptr, np.uint8, h * img.bytesPerLine()).reshape(h, img.bytesPerLine())[:, :w * 3].reshape(h, w, 3)
            self._latest = np.ascontiguousarray(arr[..., ::-1])      # BGR for cv2
        except Exception as exc:  # noqa: BLE001
            if self.monitor:
                self.monitor.state, self.monitor.error = "ERROR", f"screen capture failed: {exc}"

    # ------------------------------------------------------------------ start / stop
    def start_monitoring(self) -> None:
        self.apply_scope()
        if self.monitor is None:
            self.monitor = SpotMonitor(self.registry, self.cfg, capture=self.capture, on_event=self.relay.event.emit)
        self.monitor.cfg = self.cfg
        self.monitor.start()
        self.btn_start.setEnabled(False)
        self.btn_stop.setEnabled(True)
        self._set_status("ONLINE", "watching the screen — walk to the spots you care about and keep the camera still")

    def stop_monitoring(self) -> None:
        if self.monitor:
            self.monitor.stop()
        self.btn_start.setEnabled(True)
        self.btn_stop.setEnabled(False)
        self._set_status("OFFLINE", "monitoring stopped")

    def _set_status(self, state: str, text: str) -> None:
        color = {"ONLINE": GREEN, "ERROR": RED}.get(state, MUTED)
        self.status_pill.setText(state)
        self.status_pill.setStyleSheet(f"background:{color}22; color:{color}; border:1px solid {color};")
        self.status_text.setText(text)

    def _tick(self) -> None:
        m = self.monitor
        if m is None or not m.running:
            if m is not None and m.state == "ERROR":
                self._set_status("ERROR", m.error)
            return
        self._grab()
        if m.state == "ERROR":
            self._set_status("ERROR", m.error)
        else:
            dets = len(m.last_detections)
            self._set_status("ONLINE", f"{m.context_note} · {dets} huts in view · frame {m.last_frame_ms:.0f} ms · {m.frames} frames")
        if m.last_frame is not None and self.isVisible():
            self.view.set_frame(m.last_frame)
        if m.frames % 5 == 0:
            self.refresh_lists()

    # ------------------------------------------------------------------ spots
    def mark_spot(self, x: int, y: int, rating: str) -> None:
        s = self.registry.add(self.cfg.map_name, self.cfg.room, self.cfg.channel, x, y, rating=rating, manual=True)
        self.registry.set_rating(s.spot_id, rating)
        self.refresh_lists()
        self.view._render()

    def remove_spot(self, spot_id: str) -> None:
        self.registry.delete(spot_id)
        self.refresh_lists()
        self.view._render()

    def _table_menu(self, pos) -> None:
        row = self.table.rowAt(pos.y())
        if row < 0:
            return
        sid = self.table.item(row, 0).data(Qt.UserRole)
        menu = QMenu(self)
        for rating in ("PRIME", "GOOD", "NORMAL"):
            a = menu.addAction(f"Rate {rating}")
            a.triggered.connect(lambda _=False, r=rating, k=sid: (self.registry.set_rating(k, r), self.refresh_lists(), self.view._render()))
        d = menu.addAction("Remove")
        d.triggered.connect(lambda: self.remove_spot(sid))
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def refresh_lists(self) -> None:
        want = self.filter.currentText()
        spots = [s for s in self.registry.in_scope(self.cfg.map_name, self.cfg.room, self.cfg.channel) if want == "All" or s.rating == want]
        spots.sort(key=lambda s: ({"PRIME": 0, "GOOD": 1}.get(s.rating, 2), -s.vacate_count))
        self.table.setRowCount(len(spots))
        now = time.time()
        for r, s in enumerate(spots):
            vals = [f"({s.x},{s.y}) {s.location_text()}", s.rating, s.state,
                    fmt_age(now - s.last_seen_at) + " ago" if s.last_seen_at else "never", str(s.vacate_count),
                    fmt_age(s.avg_occupied_s) if s.avg_occupied_s else "— (need 3+)",
                    fmt_age(s.avg_retake_s) if s.avg_retake_s else "— (need 2+)", s.note]
            for c, v in enumerate(vals):
                it = QTableWidgetItem(v)
                if c == 0:
                    it.setData(Qt.UserRole, s.spot_id)
                if c == 1:
                    it.setForeground(QColor(RATING_COLOR.get(s.rating, MUTED)))
                if c == 2:
                    it.setForeground(QColor({"FREE": GREEN, "OCCUPIED": YELLOW}.get(s.state, MUTED)))
                self.table.setItem(r, c, it)
        evs = [e for e in self.registry.events(100) if want == "All" or (self.registry.spots.get(e["spot_id"]) and self.registry.spots[e["spot_id"]].rating == want)]
        self.history.setRowCount(len(evs))
        for r, e in enumerate(evs):
            sp = self.registry.spots.get(e["spot_id"])
            self.history.setItem(r, 0, QTableWidgetItem(time.strftime("%H:%M:%S", time.localtime(e["at"]))))
            self.history.setItem(r, 1, QTableWidgetItem(e["kind"].upper() + (f" ({e['detail']})" if e["detail"] else "")))
            self.history.setItem(r, 2, QTableWidgetItem(f"{sp.rating} ({sp.x},{sp.y}) {sp.location_text()}" if sp else e["spot_id"]))

    # ------------------------------------------------------------------ events
    def _on_event(self, ev: SpotEvent) -> None:
        self.recent.insert(0, ev)
        del self.recent[50:]
        if ev.kind == "ALERT" and ev.spot is not None:
            self.win.on_spot_alert(ev)
        self.refresh_lists()
