from __future__ import annotations

import time
from typing import Any, Optional

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QSortFilterProxyModel, Qt
from PySide6.QtGui import QBrush, QColor

from ..collector.normalize import stats_signature
from ..detection import passes_user_filters
from ..models import Level, Opportunity
from .theme import GREEN, LEVEL_COLORS, MUTED, RED

# The table shows what is needed to decide at a glance; the full breakdown opens on double-click.
# Three different prices are shown and never mixed up:
#   Sold Price  = a VERIFIED completed-sale price (N/A when no reliable sale data exists — never invented)
#   Quick Sale  = our ESTIMATE of a fast resale price (not guaranteed)
#   market asking prices appear only in the details / tooltip, labelled as asks
COLUMNS = ["Item", "Buy Price", "Sold Price", "Quick Sale", "Net Profit", "ROI %", "Channel", "Room / Map", "Seller", "Age"]
COLUMN_TOOLTIPS = {"Item": "Item name — green: fresh & graded (JACKPOT/GOOD FLIP), orange: UNCERTAIN, grey: stale",
                   "Buy Price": "Listing price in the shop (mesos)",
                   "Sold Price": "VERIFIED completed-sale price (median of real sales). N/A = no reliable sale data; nothing is invented",
                   "Quick Sale": "ESTIMATED quick-selling price (not guaranteed; not a real sale)",
                   "Net Profit": "Estimated net profit after the selling fee (quick sale − fee − buy). Hover for deal confidence",
                   "ROI %": "Estimated net profit as a percentage of the buy price",
                   "Channel": "Game channel of the shop", "Room / Map": "Free Market room (or map name)",
                   "Seller": "Shop owner's name", "Age": "Time since the source observed this listing (updates live)"}
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
            n = len(self.rows) - 1
            age_col = COLUMNS.index("Age")
            self.dataChanged.emit(self.index(0, age_col), self.index(n, age_col), [Qt.DisplayRole])   # Age text
            self.dataChanged.emit(self.index(0, 0), self.index(n, 0), [Qt.ForegroundRole])           # fresh/stale colour

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
        old = o.stale or o.gone or age_s > self.stale_after_s
        if role == Qt.UserRole:  # sort key
            net = v.net_profit if v.net_profit is not None else -10**12
            return {"Item": l.item_name.lower(), "Buy Price": l.price, "Sold Price": v.sold_price or 0,
                    "Quick Sale": v.quick or 0,
                    # default order: fresh deals by profit, stale/gone ones below them
                    "Net Profit": net - (10**11 if old else 0),
                    "ROI %": v.roi if v.roi is not None else -1e9,
                    "Channel": l.channel or 0, "Room / Map": l.fm_room if l.fm_room is not None else 10**6,
                    "Seller": l.seller.lower(), "Age": -l.source_seen_at}[col]
        if role == Qt.DisplayRole:
            if col == "Item":
                return l.item_name
            if col == "Buy Price":
                return f"{l.price:,}"
            if col == "Sold Price":
                return f"{v.sold_price:,}" if v.sold_price else "N/A"
            if col == "Quick Sale":
                return f"{v.quick:,}" if v.quick else "N/A"
            if col == "Net Profit":
                return f"{v.net_profit:+,}" if v.net_profit is not None else "N/A"
            if col == "ROI %":
                return f"{v.roi * 100:.0f}%" if v.roi is not None else "N/A"
            if col == "Channel":
                return f"CH {l.channel}" if l.channel is not None else "?"
            if col == "Room / Map":
                return f"FM {l.fm_room}" if l.fm_room is not None else (l.map_name or "?")
            if col == "Seller":
                return l.seller
            if col == "Age":
                return fmt_age(age_s) + (" · gone" if o.gone else " · stale" if old else "")
        if role == Qt.ForegroundRole:
            if col in ("Net Profit", "ROI %") and v.net_profit is not None and not old:
                return QBrush(QColor(GREEN if v.net_profit > 0 else RED))
            if col == "Sold Price" and not v.sold_price:
                return QBrush(QColor(MUTED))
            if old:
                return QBrush(QColor(MUTED))                       # old / gone listing: grey
            if o.level == Level.UNCERTAIN:
                return QBrush(QColor(ORANGE))                      # unverified pricing: orange
            if col in ("Item", "Age"):
                return QBrush(QColor(GREEN))                       # fresh & graded: green
        if role == Qt.BackgroundRole and not old:
            if o.level == Level.JACKPOT:
                return QBrush(QColor(LEVEL_COLORS["JACKPOT"] + "1a"))   # faint tint behind JACKPOT rows
            if o.level == Level.GOOD:
                return QBrush(QColor(GREEN + "12"))                     # very soft green for good deals
        if role == Qt.TextAlignmentRole and col in ("Buy Price", "Sold Price", "Quick Sale", "Net Profit", "ROI %", "Age"):
            return int(Qt.AlignRight | Qt.AlignVCenter)
        if role == Qt.ToolTipRole:
            return self.tooltip_for(o)
        return None

    @staticmethod
    def tooltip_for(o: Opportunity) -> str:
        """Compact confidence + pricing explanation (the same text on every cell of the row)."""
        l, v = o.listing, o.valuation
        ver = (f" · {o.availability}" + (f" (verified {fmt_age(time.time() - o.last_verified_at)} ago)" if o.last_verified_at else ""))
        head = (f"{o.level.value}{' (stale)' if o.stale else ''}{ver} · confidence {v.confidence_label} {v.confidence_score}/100 "
                f"({v.sample_count} samples, {v.unique_sellers} sellers)")
        sold = (f"Sold price: {v.sold_price:,} — {v.sold_basis}" if v.sold_price else
                "Sold price: N/A — no reliable completed-sale data (nothing is invented)")
        ref = f"Reference value: {v.reference:,}" if v.reference else "Reference value: n/a"
        quick = f"Quick sale (estimate): {v.quick:,}" if v.quick else "Quick sale: n/a"
        if not v.explanation and not v.reference:
            why = ["No pricing: insufficient comparable transactions for this item."]
        else:
            why = v.explanation
        return "\n".join([head, sold, ref, quick, f"Shop: {l.seller} · {l.location}"] + why + [f"risk: {r}" for r in v.risks] + o.reasons)


class OpportunityFilter(QSortFilterProxyModel):
    def __init__(self) -> None:
        super().__init__()
        self.show_levels = {Level.JACKPOT, Level.GOOD, Level.UNCERTAIN}
        self.max_age_s: Optional[float] = None
        self.text = ""
        self.settings = None            # Settings; when set, rows must pass the user's profitability filters
        self.show_gone = False
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
        if o.gone and not self.show_gone:
            return False                      # verified REMOVED / SOLD: out of the active table (kept in the DB)
        return True
