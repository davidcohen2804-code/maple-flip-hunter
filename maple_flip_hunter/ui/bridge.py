"""Marshals engine callbacks (background thread) onto the Qt main thread via signals."""
from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class EngineBridge(QObject):
    listing = Signal(object, str)
    opportunity = Signal(object)
    alert = Signal(object, str, str, str)
    health = Signal(object)

    def __init__(self, engine):
        super().__init__()
        engine.on("listing", lambda lst, kind: self.listing.emit(lst, kind))
        engine.on("opportunity", lambda opp: self.opportunity.emit(opp))
        engine.on("alert", lambda opp, level, title, body: self.alert.emit(opp, level, title, body))
        engine.on("health", lambda h: self.health.emit(h))
