"""Always-on-top alert card. Stays visible until the user presses OK; never steals keyboard focus from the game.

Windows note: a top-most window is drawn over normal windows and over games running in *windowed* or *borderless*
mode. A game in *exclusive full-screen* mode owns the display and Windows does not draw other windows over it;
the sound and the tray notification still happen, and the card is waiting as soon as the game is alt-tabbed."""
from __future__ import annotations

import html
import time
from typing import Callable, Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import QApplication, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ..models import Opportunity
from .theme import ACCENT, BORDER, GREEN, LEVEL_COLORS, MUTED, PANEL, TEXT


class AlertPopup(QWidget):
    """One card per alert. Several alerts stack from the top-right corner of the primary screen."""

    _open: list["AlertPopup"] = []

    def __init__(self, opp: Opportunity, level: str, on_mute: Optional[Callable[[], None]] = None,
                 latency_ms: Optional[float] = None, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.opp = opp
        self.setWindowFlags(Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_DeleteOnClose, True)
        self.setFixedWidth(440)
        color = LEVEL_COLORS.get(level, ACCENT)
        self.setStyleSheet(f"QWidget {{ background: {PANEL}; color: {TEXT}; font-family: 'Segoe UI', Arial; font-size: 13px; }}"
                           f"QWidget#card {{ border: 2px solid {color}; border-radius: 10px; }}"
                           f"QLabel#head {{ color: {color}; font-size: 18px; font-weight: 800; }}"
                           f"QLabel#muted {{ color: {MUTED}; }}"
                           f"QPushButton {{ background: #1f2733; border: 1px solid {BORDER}; border-radius: 6px; padding: 8px 14px; }}"
                           f"QPushButton:hover {{ border-color: {color}; }}"
                           f"QPushButton#ok {{ background: {color}; color: #0f1419; font-weight: 700; }}")
        card = QWidget(self)
        card.setObjectName("card")
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.addWidget(card)
        v = QVBoxLayout(card)
        v.setContentsMargins(18, 16, 18, 16)
        v.setSpacing(8)

        l, val = opp.listing, opp.valuation
        head = QLabel(f"{level} — {html.escape(l.item_name)}")
        head.setObjectName("head")
        head.setWordWrap(True)
        v.addWidget(head)
        net = f"{val.net_profit:+,}" if val.net_profit is not None else "n/a"
        v.addWidget(QLabel(f"Buy <b>{l.price:,}</b> mesos &nbsp;·&nbsp; estimated net profit <b style='color:{GREEN}'>{net}</b> "
                           f"(quick sale {val.quick:,}; not guaranteed)" if val.quick else f"Buy <b>{l.price:,}</b> mesos"))
        self.location_text = f"{l.location} · Shop: {l.seller}"
        loc = QLabel(f"📍 <b>{html.escape(self.location_text)}</b>")
        loc.setWordWrap(True)
        v.addWidget(loc)
        meta = QLabel(f"confidence {val.confidence_label} ({val.confidence:.2f}) · seen by source "
                      f"{max(0.0, time.time() - l.source_seen_at):.0f}s ago"
                      + (f" · alert in {latency_ms:.0f} ms" if latency_ms is not None else ""))
        meta.setObjectName("muted")
        meta.setWordWrap(True)
        v.addWidget(meta)

        row = QHBoxLayout()
        row.setSpacing(8)
        self.btn_copy = QPushButton("📋 Copy location")
        self.btn_copy.clicked.connect(self.copy_location)
        self.btn_mute = QPushButton("🔇 Mute")
        self.btn_mute.clicked.connect(lambda: on_mute() if on_mute else None)
        self.btn_ok = QPushButton("OK")
        self.btn_ok.setObjectName("ok")
        self.btn_ok.clicked.connect(self.close)
        row.addWidget(self.btn_copy)
        row.addWidget(self.btn_mute)
        row.addStretch(1)
        row.addWidget(self.btn_ok)
        v.addLayout(row)

    # ------------------------------------------------------------------ api
    def copy_location(self) -> None:
        QApplication.clipboard().setText(self.location_text)
        self.btn_copy.setText("✓ Copied")

    def show_stacked(self) -> None:
        AlertPopup._open.append(self)
        self.adjustSize()
        scr = QGuiApplication.primaryScreen()
        geo = scr.availableGeometry() if scr else None
        if geo is not None:
            y = geo.top() + 24
            for p in AlertPopup._open[:-1]:
                y += p.height() + 10
            self.move(geo.right() - self.width() - 24, y)
        self.show()
        self.raise_()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self in AlertPopup._open:
            AlertPopup._open.remove(self)
        super().closeEvent(event)

    @classmethod
    def open_count(cls) -> int:
        return len(cls._open)
