"""Spot registry: where shops stand (per map / FM room / channel), how attractive each spot is, and what was
observed there. Persisted in the main SQLite database (tables `spots`, `spot_events`)."""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Optional

Rating = str   # "PRIME" | "GOOD" | "NORMAL"
RATINGS = ("PRIME", "GOOD", "NORMAL")

SCHEMA = """
CREATE TABLE IF NOT EXISTS spots (
    spot_id TEXT PRIMARY KEY, map TEXT, room INTEGER, channel INTEGER, x INTEGER, y INTEGER,
    rating TEXT, manual INTEGER, state TEXT, last_seen_at REAL, last_change_at REAL, vacate_count INTEGER,
    occupied_s_total REAL, occupied_runs INTEGER, retake_s_total REAL, retake_runs INTEGER, note TEXT, created_at REAL
);
CREATE TABLE IF NOT EXISTS spot_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT, at REAL, spot_id TEXT, kind TEXT, detail TEXT, confidence REAL
);
CREATE INDEX IF NOT EXISTS idx_spot_events_at ON spot_events(at);
"""


@dataclass
class Spot:
    spot_id: str
    map: str
    room: Optional[int]
    channel: Optional[int]
    x: int                      # map-anchored px (screen px + accumulated camera offset at registration)
    y: int
    rating: Rating = "NORMAL"
    manual: bool = False
    state: str = "UNVERIFIED"   # OCCUPIED | FREE | UNVERIFIED
    last_seen_at: float = 0.0   # last time a hut was seen here
    last_change_at: float = 0.0
    vacate_count: int = 0
    occupied_s_total: float = 0.0
    occupied_runs: int = 0
    retake_s_total: float = 0.0
    retake_runs: int = 0
    note: str = ""
    created_at: float = field(default_factory=time.time)

    @property
    def avg_occupied_s(self) -> Optional[float]:
        return self.occupied_s_total / self.occupied_runs if self.occupied_runs >= 3 else None

    @property
    def avg_retake_s(self) -> Optional[float]:
        return self.retake_s_total / self.retake_runs if self.retake_runs >= 2 else None

    def location_text(self) -> str:
        parts = [self.map or "?"]
        if self.room is not None:
            parts.append(f"FM {self.room}")
        if self.channel is not None:
            parts.append(f"CH {self.channel}")
        return " · ".join(parts)


class SpotRegistry:
    MATCH_RADIUS = 45   # px: a detection this close to a known spot is that spot

    def __init__(self, conn: sqlite3.Connection, lock: Optional[threading.RLock] = None) -> None:
        self.conn = conn
        self.lock = lock or threading.RLock()
        with self.lock:
            self.conn.executescript(SCHEMA)
        self.spots: dict[str, Spot] = {}
        self._load()

    # ----------------------------------------------------------------- persistence
    def _load(self) -> None:
        with self.lock:
            for r in self.conn.execute("SELECT * FROM spots"):
                d = dict(r)
                d["manual"] = bool(d["manual"])
                self.spots[d["spot_id"]] = Spot(**{k: d[k] for k in Spot.__dataclass_fields__ if k in d})

    def save(self, s: Spot) -> None:
        d = asdict(s)
        d["manual"] = int(s.manual)
        with self.lock:
            self.conn.execute(
                "INSERT OR REPLACE INTO spots(spot_id, map, room, channel, x, y, rating, manual, state, last_seen_at, last_change_at, "
                "vacate_count, occupied_s_total, occupied_runs, retake_s_total, retake_runs, note, created_at) VALUES "
                "(:spot_id,:map,:room,:channel,:x,:y,:rating,:manual,:state,:last_seen_at,:last_change_at,:vacate_count,"
                ":occupied_s_total,:occupied_runs,:retake_s_total,:retake_runs,:note,:created_at)", d)
            self.conn.commit()

    def event(self, spot_id: str, kind: str, detail: str = "", confidence: float = 0.0) -> None:
        with self.lock:
            self.conn.execute("INSERT INTO spot_events(at, spot_id, kind, detail, confidence) VALUES (?,?,?,?,?)",
                              (time.time(), spot_id, kind, detail, confidence))
            self.conn.commit()

    def events(self, limit: int = 200) -> list[sqlite3.Row]:
        with self.lock:
            return self.conn.execute("SELECT * FROM spot_events ORDER BY at DESC LIMIT ?", (limit,)).fetchall()

    def delete(self, spot_id: str) -> None:
        self.spots.pop(spot_id, None)
        with self.lock:
            self.conn.execute("DELETE FROM spots WHERE spot_id=?", (spot_id,))
            self.conn.commit()

    # ----------------------------------------------------------------- lookup
    @staticmethod
    def scope_key(map_name: str, room: Optional[int], channel: Optional[int]) -> str:
        return f"{(map_name or '?').lower()}|{room if room is not None else '-'}|{channel if channel is not None else '-'}"

    def in_scope(self, map_name: str, room: Optional[int], channel: Optional[int]) -> list[Spot]:
        key = self.scope_key(map_name, room, channel)
        return [s for s in self.spots.values() if self.scope_key(s.map, s.room, s.channel) == key]

    def find(self, map_name: str, room: Optional[int], channel: Optional[int], x: int, y: int) -> Optional[Spot]:
        best, bd = None, self.MATCH_RADIUS + 1
        for s in self.in_scope(map_name, room, channel):
            d = ((s.x - x) ** 2 + (s.y - y) ** 2) ** 0.5
            if d < bd:
                best, bd = s, d
        return best

    def add(self, map_name: str, room: Optional[int], channel: Optional[int], x: int, y: int,
            rating: Rating = "NORMAL", manual: bool = False, note: str = "") -> Spot:
        existing = self.find(map_name, room, channel, x, y)
        if existing is not None:
            if manual:
                existing.rating, existing.manual, existing.note = rating, True, note or existing.note
                self.save(existing)
            return existing
        sid = f"{self.scope_key(map_name, room, channel)}|{x}|{y}"
        s = Spot(spot_id=sid, map=map_name, room=room, channel=channel, x=x, y=y, rating=rating, manual=manual, note=note)
        self.spots[sid] = s
        self.save(s)
        return s

    def set_rating(self, spot_id: str, rating: Rating) -> None:
        s = self.spots[spot_id]
        s.rating, s.manual = rating, True
        self.save(s)
        self.event(spot_id, "rated", rating)
