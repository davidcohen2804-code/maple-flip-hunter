from __future__ import annotations

import time
from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor

from ..collector.normalize import stats_signature
from ..detection import passes_user_filters
from ..models import Level, Opportunity
from .theme import LEVEL_COLORS, MUTED

# The table shows only what is needed to decide at a glance; everything else lives in the details panel.
COLUMNS = ["Item", "Buy", "Market Value", "Quick Sale"]
COLUMN_TOOLTIPS = {"Item": "Item name — colour shows the level (red JACKPOT, green GOOD FLIP, yellow UNCERTAIN); grey = stale",
                   "Buy": "Listing price in the shop (mesos)", "Market Value": "Estimated market value (reference price)",
                   "Quick Sale": "Estimated quick-selling price (not guaranteed)"}
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
        if self.rows:   # the Item colour turns grey when a row goes stale
            self.dataChanged.emit(self.index(0, 0), self.index(len(self.rows) - 1, 0))

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
        if role == Qt.UserRole:  # sort key
            return {"Item": l.item_name.lower(), "Buy": l.price, "Market Value": v.reference or 0,
                    "Quick Sale": v.quick or 0}[col]
        if role == Qt.DisplayRole:
            if col == "Item":
                return l.item_name
            if col == "Buy":
                return f"{l.price:,}"
            if col == "Market Value":
                return f"{v.reference:,}" if v.reference else "n/a"
            if col == "Quick Sale":
                return f"{v.quick:,}" if v.quick else "n/a"
        if role == Qt.ForegroundRole:
            if o.stale:
                return QBrush(QColor(MUTED))
            if col == "Item":                      # the subtle level indicator: coloured item name
                return QBrush(QColor(LEVEL_COLORS.get(o.level.value, MUTED)))
            if col == "Quick Sale" and (v.net_profit or 0) > 0:
                return QBrush(QColor("#3fb950"))
        if role == Qt.BackgroundRole and o.level == Level.JACKPOT and not o.stale:
            return QBrush(QColor(LEVEL_COLORS["JACKPOT"] + "1a"))   # faint red tint behind JACKPOT rows
        if role == Qt.TextAlignmentRole and col in ("Buy", "Market Value", "Quick Sale"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            head = f"{o.level.value}{' (stale)' if o.stale else ''} · net {v.net_profit:+,} · {l.location} · {l.seller}" if v.net_profit is not None \
                else f"{o.level.value} · {l.location} · {l.seller}"
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
