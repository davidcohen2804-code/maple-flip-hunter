"""SQLite persistence (stdlib sqlite3, WAL mode). One connection, used from the collector thread
and the UI thread behind a lock."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any, Iterable, Optional

from ..models import Listing, Opportunity, SourceHealth

log = logging.getLogger(__name__)

SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
CREATE TABLE IF NOT EXISTS items (
    item_key TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    category TEXT, subcategory TEXT,
    source_ids TEXT,                -- json {source: id}
    icon_url TEXT,
    first_seen REAL, last_seen REAL,
    listings_seen INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_items_name ON items(name);
CREATE TABLE IF NOT EXISTS listings (
    listing_key TEXT PRIMARY KEY,
    cross_key TEXT,
    source TEXT NOT NULL,
    item_key TEXT NOT NULL,
    item_name TEXT NOT NULL,
    seller TEXT, shop_title TEXT, channel INTEGER, fm_room INTEGER, map_name TEXT,
    price INTEGER NOT NULL, qty INTEGER, stock INTEGER,
    stats TEXT,                     -- json
    sold INTEGER DEFAULT 0,
    source_seen_at REAL, source_first_seen_at REAL, local_first_seen_at REAL, last_seen_at REAL,
    source_ref_price INTEGER, source_ref_basis TEXT,
    payload TEXT                    -- json of the Listing model
);
CREATE INDEX IF NOT EXISTS idx_listings_item ON listings(item_key, sold, source_seen_at);
CREATE INDEX IF NOT EXISTS idx_listings_cross ON listings(cross_key);
CREATE INDEX IF NOT EXISTS idx_listings_seller ON listings(seller, item_key);
CREATE INDEX IF NOT EXISTS idx_listings_seen ON listings(source_seen_at);
CREATE TABLE IF NOT EXISTS listing_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL, listing_key TEXT, item_key TEXT, seller TEXT,
    kind TEXT NOT NULL,             -- new | repriced | seen | sold | gone
    old_price INTEGER, new_price INTEGER, source TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_at ON listing_events(at);
CREATE INDEX IF NOT EXISTS idx_events_item ON listing_events(item_key, kind, at);
CREATE TABLE IF NOT EXISTS sales (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    item_key TEXT NOT NULL, unit_price INTEGER NOT NULL, at REAL NOT NULL, source TEXT, seller TEXT,
    stats TEXT, UNIQUE(item_key, unit_price, at, seller)
);
CREATE INDEX IF NOT EXISTS idx_sales_item ON sales(item_key, at);
CREATE TABLE IF NOT EXISTS price_stats (
    item_key TEXT PRIMARY KEY, computed_at REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS opportunities (
    listing_key TEXT PRIMARY KEY,
    item_key TEXT, item_name TEXT, level TEXT, buy INTEGER, reference INTEGER, quick INTEGER,
    net_profit INTEGER, roi REAL, confidence REAL, detected_at REAL, notified_at REAL,
    analysis_ms REAL, payload TEXT
);
CREATE INDEX IF NOT EXISTS idx_opps_detected ON opportunities(detected_at);
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at REAL NOT NULL, listing_key TEXT, level TEXT, title TEXT, body TEXT, sound TEXT
);
CREATE INDEX IF NOT EXISTS idx_notif_at ON notifications(at);
CREATE TABLE IF NOT EXISTS source_status (
    name TEXT PRIMARY KEY, at REAL, payload TEXT
);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""


class Database:
    def __init__(self, path: Path | str = ":memory:"):
        self.path = str(path)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None)
        self.conn.row_factory = sqlite3.Row
        with self._lock:
            self.conn.executescript(SCHEMA)

    # ------------------------------------------------------------ helpers
    def _exec(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def close(self) -> None:
        with self._lock:
            try:
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error:
                pass
            self.conn.close()

    # ------------------------------------------------------------ listings
    def upsert_listing(self, lst: Listing, cross_key: str) -> tuple[str, Optional[int]]:
        """Insert or refresh a listing. Returns (event_kind, old_price) where kind is
        'new' | 'repriced' | 'seen' | 'duplicate' (same row seen by another source)."""
        now = time.time()
        with self._lock:
            row = self.conn.execute("SELECT price, source FROM listings WHERE listing_key=?",
                                    (lst.listing_key,)).fetchone()
            if row:
                self.conn.execute(
                    "UPDATE listings SET last_seen_at=?, source_seen_at=MAX(source_seen_at, ?), stock=?, sold=? "
                    "WHERE listing_key=?",
                    (now, lst.source_seen_at, lst.stock, int(lst.sold), lst.listing_key))
                self._event(now, lst, "seen", row["price"], lst.price)
                return "seen", row["price"]

            # same physical row already known from another source?
            dup = self.conn.execute("SELECT listing_key, source FROM listings WHERE cross_key=? AND sold=0 LIMIT 1",
                                    (cross_key,)).fetchone()
            # previous price of the same seller+item (repricing)
            prev = self.conn.execute(
                "SELECT listing_key, price FROM listings WHERE item_key=? AND lower(seller)=lower(?) "
                "AND listing_key!=? AND sold=0 ORDER BY last_seen_at DESC LIMIT 1",
                (lst.item_key, lst.seller, lst.listing_key)).fetchone()
            self.conn.execute(
                """INSERT INTO listings(listing_key, cross_key, source, item_key, item_name, seller, shop_title,
                   channel, fm_room, map_name, price, qty, stock, stats, sold, source_seen_at, source_first_seen_at,
                   local_first_seen_at, last_seen_at, source_ref_price, source_ref_basis, payload)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (lst.listing_key, cross_key, lst.source, lst.item_key, lst.item_name, lst.seller, lst.shop_title,
                 lst.channel, lst.fm_room, lst.map_name, lst.price, lst.qty, lst.stock,
                 json.dumps(lst.stats) if lst.stats else None, int(lst.sold), lst.source_seen_at,
                 lst.source_first_seen_at, lst.local_first_seen_at, now, lst.source_ref_price, lst.source_ref_basis,
                 lst.model_dump_json()))
            self._touch_item(lst, now)
            if dup and dup["source"] != lst.source:
                self._event(now, lst, "duplicate", None, lst.price)
                return "duplicate", None
            if prev and prev["price"] != lst.price:
                self.conn.execute("UPDATE listings SET sold=0, stock=0, last_seen_at=? WHERE listing_key=?",
                                  (now, prev["listing_key"]))  # old row superseded
                self._event(now, lst, "repriced", prev["price"], lst.price)
                return "repriced", prev["price"]
            self._event(now, lst, "new", None, lst.price)
            return "new", None

    def _event(self, at: float, lst: Listing, kind: str, old: Optional[int], new: Optional[int]) -> None:
        self.conn.execute(
            "INSERT INTO listing_events(at, listing_key, item_key, seller, kind, old_price, new_price, source) "
            "VALUES (?,?,?,?,?,?,?,?)", (at, lst.listing_key, lst.item_key, lst.seller, kind, old, new, lst.source))

    def _touch_item(self, lst: Listing, now: float) -> None:
        row = self.conn.execute("SELECT source_ids FROM items WHERE item_key=?", (lst.item_key,)).fetchone()
        ids = json.loads(row["source_ids"]) if row and row["source_ids"] else {}
        if lst.source_item_id:
            ids[lst.source] = lst.source_item_id
        if row:
            self.conn.execute(
                "UPDATE items SET last_seen=?, listings_seen=listings_seen+1, source_ids=?, "
                "category=COALESCE(category, ?), icon_url=COALESCE(icon_url, ?) WHERE item_key=?",
                (now, json.dumps(ids), lst.category, lst.icon_url, lst.item_key))
        else:
            self.conn.execute(
                "INSERT INTO items(item_key, name, category, subcategory, source_ids, icon_url, first_seen, last_seen, "
                "listings_seen) VALUES (?,?,?,?,?,?,?,?,1)",
                (lst.item_key, lst.item_name, lst.category, lst.subcategory, json.dumps(ids), lst.icon_url, now, now))

    def record_sale(self, item_key: str, unit_price: int, at: float, source: str, seller: Optional[str],
                    stats: Optional[dict[str, Any]]) -> None:
        self._exec("INSERT OR IGNORE INTO sales(item_key, unit_price, at, source, seller, stats) VALUES (?,?,?,?,?,?)",
                   (item_key, unit_price, at, source, seller, json.dumps(stats) if stats else None))

    def active_asks(self, item_key: str, since: float, exclude_key: Optional[str] = None) -> list[sqlite3.Row]:
        return self._exec(
            "SELECT listing_key, seller, channel, fm_room, price, stats, source_seen_at, source FROM listings "
            "WHERE item_key=? AND sold=0 AND source_seen_at>=? AND listing_key!=? ORDER BY price ASC LIMIT 50",
            (item_key, since, exclude_key or "")).fetchall()

    def sales_for(self, item_key: str, since: float) -> list[sqlite3.Row]:
        return self._exec("SELECT unit_price, at, seller, stats FROM sales WHERE item_key=? AND at>=? ORDER BY at DESC",
                          (item_key, since)).fetchall()

    def item_count(self) -> int:
        return int(self._exec("SELECT COUNT(*) FROM items").fetchone()[0])

    def listing_count(self, since: Optional[float] = None) -> int:
        if since is None:
            return int(self._exec("SELECT COUNT(*) FROM listings").fetchone()[0])
        return int(self._exec("SELECT COUNT(*) FROM listings WHERE local_first_seen_at>=?", (since,)).fetchone()[0])

    # -------------------------------------------------------- opportunities
    def save_opportunity(self, opp: Opportunity) -> None:
        v, l = opp.valuation, opp.listing
        self._exec(
            """INSERT OR REPLACE INTO opportunities(listing_key, item_key, item_name, level, buy, reference, quick,
               net_profit, roi, confidence, detected_at, notified_at, analysis_ms, payload)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (l.listing_key, l.item_key, l.item_name, opp.level.value, l.price, v.reference, v.quick, v.net_profit,
             v.roi, v.confidence, opp.detected_at, opp.notified_at, opp.analysis_ms, opp.model_dump_json()))

    def was_alerted(self, listing_key: str) -> bool:
        row = self._exec("SELECT notified_at FROM opportunities WHERE listing_key=?", (listing_key,)).fetchone()
        return bool(row and row["notified_at"])

    def recent_opportunities(self, since: float, limit: int = 500) -> list[Opportunity]:
        rows = self._exec("SELECT payload FROM opportunities WHERE detected_at>=? ORDER BY detected_at DESC LIMIT ?",
                          (since, limit)).fetchall()
        out = []
        for r in rows:
            try:
                out.append(Opportunity.model_validate_json(r["payload"]))
            except Exception:  # noqa: BLE001
                continue
        return out

    def opportunity_count(self) -> int:
        return int(self._exec("SELECT COUNT(*) FROM opportunities WHERE level!='NONE'").fetchone()[0])

    # --------------------------------------------------------- notifications
    def add_notification(self, listing_key: str, level: str, title: str, body: str, sound: str) -> None:
        self._exec("INSERT INTO notifications(at, listing_key, level, title, body, sound) VALUES (?,?,?,?,?,?)",
                   (time.time(), listing_key, level, title, body, sound))
        self._exec("UPDATE opportunities SET notified_at=? WHERE listing_key=?", (time.time(), listing_key))

    def notifications(self, limit: int = 200) -> list[sqlite3.Row]:
        return self._exec("SELECT * FROM notifications ORDER BY at DESC LIMIT ?", (limit,)).fetchall()

    # ---------------------------------------------------------- misc / kv
    def save_source_status(self, h: SourceHealth) -> None:
        self._exec("INSERT OR REPLACE INTO source_status(name, at, payload) VALUES (?,?,?)",
                   (h.name, time.time(), h.model_dump_json()))

    def kv_get(self, key: str, default: Optional[str] = None) -> Optional[str]:
        row = self._exec("SELECT value FROM kv WHERE key=?", (key,)).fetchone()
        return row["value"] if row else default

    def kv_set(self, key: str, value: str) -> None:
        self._exec("INSERT OR REPLACE INTO kv(key, value) VALUES (?,?)", (key, value))

    def cleanup(self, listings_days: int = 7, events_days: int = 30, notifications_days: int = 30) -> dict[str, int]:
        now = time.time()
        out = {}
        with self._lock:
            out["listings"] = self.conn.execute("DELETE FROM listings WHERE last_seen_at < ?",
                                                (now - listings_days * 86400,)).rowcount
            out["events"] = self.conn.execute("DELETE FROM listing_events WHERE at < ?",
                                              (now - events_days * 86400,)).rowcount
            out["notifications"] = self.conn.execute("DELETE FROM notifications WHERE at < ?",
                                                     (now - notifications_days * 86400,)).rowcount
            out["opportunities"] = self.conn.execute("DELETE FROM opportunities WHERE detected_at < ?",
                                                     (now - events_days * 86400,)).rowcount
            out["sales"] = self.conn.execute("DELETE FROM sales WHERE at < ?", (now - 60 * 86400,)).rowcount
        return out
