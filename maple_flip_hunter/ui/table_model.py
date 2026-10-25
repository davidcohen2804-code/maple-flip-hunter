from __future__ import annotations

import time
from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor

from ..collector.normalize import stats_signature
from ..detection import passes_user_filters
from ..models import Level, Opportunity
from .theme import GREEN, LEVEL_COLORS, MUTED, RED

# The table shows only what is needed to decide at a glance; everything else lives in the details panel.
COLUMNS = ["Item", "Buy", "Quick Sale", "Net Profit", "Age", "Location"]
COLUMN_TOOLTIPS = {"Item": "Item name — green: fresh & verified (JACKPOT/GOOD FLIP), orange: UNCERTAIN, grey: stale",
                   "Buy": "Listing price in the shop (mesos)", "Quick Sale": "Estimated quick-selling price (not guaranteed)",
                   "Net Profit": "Estimated net profit after the selling fee (quick sale − fee − buy)",
                   "Age": "Time since the source observed this listing (updates live)",
                   "Location": "Where the shop is: channel · Free Market room"}
ORANGE = "#e3893b"
LEVEL_RANK = {Level.JACKPOT: 0, Level.GOOD: 1, Level.UNCERTAIN: 2, Level.NONE: 3}


def fmt_age(seconds: float) -> str:
    if seconds < 90:
        return f"{seconds:.0f}s"
    if seconds < 5400:
        return f"{seconds/60:.0f}m"
    return f"{seconds/3600:.1f}h"


class OpportunityModel(QAbstractTableModel):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[Opportunity] = []
        self._index: dict[str, int] = {}
        self.stale_after_s = 900.0        # MainWindow keeps this equal to Settings.max_listing_age_s

    def upsert(self, opp: Opportunity) -> None:
        i = self._index.get(opp.key)
        if i is None:
            self.beginInsertRows(QModelIndex(), 0, 0)
            self.rows.insert(0, opp)
            self._reindex()
            self.endInsertRows()
            if len(self.rows) > 2000:
                self.beginRemoveRows(QModelIndex(), 2000, len(self.rows) - 1)
                del self.rows[2000:]
                self._reindex()
                self.endRemoveRows()
        else:
            self.rows[i] = opp
            self.dataChanged.emit(self.index(i, 0), self.index(i, len(COLUMNS) - 1))

    def refresh_ages(self) -> None:
        """Called by the UI timer every second: Age text and the fresh/stale colours update without a rescan."""
        if self.rows:
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, len(COLUMNS) - 1))

    def _reindex(self) -> None:
        self._index = {o.key: i for i, o in enumerate(self.rows)}

    def opportunity_at(self, row: int) -> Optional[Opportunity]:
        return self.rows[row] if 0 <= row < len(self.rows) else None

    # Qt API --------------------------------------------------------------
    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return 0 if parent.isValid() else len(self.rows)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: N802
        return len(COLUMNS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.DisplayRole) -> Any:  # noqa: N802
        if orientation == Qt.Horizontal:
            if role == Qt.DisplayRole:
                return COLUMNS[section]
            if role == Qt.ToolTipRole:
                return COLUMN_TOOLTIPS.get(COLUMNS[section])
        return None

    def data(self, index: QModelIndex, role: int = Qt.DisplayRole) -> Any:
        if not index.isValid():
            return None
        o = self.rows[index.row()]
        l, v = o.listing, o.valuation
        col = COLUMNS[index.column()]
        age_s = max(0.0, time.time() - l.source_seen_at)
        if role == Qt.UserRole:  # sort key
            return {"Item": l.item_name.lower(), "Buy": l.price, "Quick Sale": v.quick or 0,
                    "Net Profit": v.net_profit if v.net_profit is not None else -10**12,
                    "Age": -l.source_seen_at,                       # ascending = freshest first
                    "Location": (l.channel or 0) * 1000 + (l.fm_room or 0)}[col]
        if role == Qt.DisplayRole:
            if col == "Item":
                return l.item_name
            if col == "Buy":
                return f"{l.price:,}"
            if col == "Quick Sale":
                return f"{v.quick:,}" if v.quick else "n/a"
            if col == "Net Profit":
                return f"{v.net_profit:+,}" if v.net_profit is not None else "n/a"
            if col == "Age":
                return fmt_age(age_s)
            if col == "Location":
                return l.location
        if role == Qt.ForegroundRole:
            if col == "Net Profit" and v.net_profit is not None and not o.stale:
                return QBrush(QColor(GREEN if v.net_profit > 0 else RED))
            if o.stale or age_s > self.stale_after_s:
                return QBrush(QColor(MUTED))                       # old listing: grey
            if o.level == Level.UNCERTAIN:
                return QBrush(QColor(ORANGE))                      # unverified: orange
            if col in ("Item", "Age"):
                return QBrush(QColor(GREEN))                       # fresh & verified: green
        if role == Qt.BackgroundRole and o.level == Level.JACKPOT and not o.stale:
            return QBrush(QColor(LEVEL_COLORS["JACKPOT"] + "1a"))   # faint tint behind JACKPOT rows
        if role == Qt.TextAlignmentRole and col in ("Buy", "Quick Sale", "Net Profit", "Age"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            head = f"{o.level.value}{' (stale)' if o.stale else ''} · shop {l.seller} · market value " + (f"{v.reference:,}" if v.reference else "n/a")
            return "\n".join([head] + v.explanation + [f"risk: {r}" for r in v.risks] + o.reasons)
        return None


class OpportunityFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.show_levels = {Level.JACKPOT, Level.GOOD, Level.UNCERTAIN}
        self.max_age_s: Optional[float] = None
        self.text = ""
        self.settings = None            # Settings; when set, rows must pass the user's profitability filters
        self.setSortRole(Qt.UserRole)
        self.setDynamicSortFilter(True)

    def filterAcceptsRow(self, row: int, parent: QModelIndex) -> bool:  # noqa: N802
        o = self.sourceModel().opportunity_at(row)  # type: ignore[union-attr]
        if o is None or o.level not in self.show_levels:
            return False
        if self.max_age_s is not None and time.time() - o.listing.source_seen_at > self.max_age_s:
            return False
        if self.text and self.text not in o.listing.item_name.lower() and self.text not in o.listing.seller.lower():
            return False
        if self.settings is not None and not passes_user_filters(o.listing, o.valuation, self.settings)[0]:
            return False
        return True
