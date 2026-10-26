"""Your own Club FM *FM Watcher* scans, read from its local files — a real second feed that needs no permission.

FM Watcher (free, by Club FM; source published at github.com/maplefm/maplefm.github.io, download/source) reads Free
Market shop windows off YOUR screen while you browse and keeps every listing it saw in its folder:
  * `watcher_data_v2.json` — its live store, rewritten atomically within ~30 s of each new read
      {"shop": {"[day, seller, item, price, qty]": {"first": ts, "last": ts, "reads": n, "nu": open_count,
                "so": sold_flag, "ns"/"sb": sold counts, "r": "FM3", "c": channel, "x": {stat: value}, "xj": job}}}
  * `my_scans.csv` — written by its export.bat ("Reviewing your own data", README): Date, Time, Source, Channel,
      Room, Seller, Item, Price, Bundle, Each, Status, Stats
This adapter polls those files (every few seconds, by modification time). It never reads the game, never talks to
Club FM's servers, and does not change what FM Watcher publishes. Set `source_fmwatcher_dir` to the FM Watcher
folder (where start.bat lives)."""
from __future__ import annotations

import csv
import json
import logging
import os
import time
from pathlib import Path
from typing import Any, Optional

from ..collector.normalize import item_key, listing_key, parse_fm_room
from ..models import Listing, SourceStatus
from .base import BaseAdapter, FetchError

log = logging.getLogger(__name__)

STORE_FILE = "watcher_data_v2.json"
CSV_FILE = "my_scans.csv"


def _stats(x: Any) -> Optional[dict[str, Any]]:
    if not isinstance(x, dict):
        return None
    out = {}
    for k, v in x.items():
        if str(k).startswith("_"):
            continue
        try:
            out[str(k).lower()] = int(v)
        except (TypeError, ValueError):
            out[str(k).lower()] = v
    return out or None


_KNOWN_MAPS = ["Kerning City Construction Site", "Free Market Entrance", "Henesys Market", "Perion Street Corner",
               "Ellinia Station", "Lith Harbor", "Sleepywood"]


def _room_text(raw: Any) -> str:
    """FM rooms come as 'FM3'; town maps may lose their first letter in OCR ('erning City Construction Site')."""
    t = str(raw or "").strip()
    if not t:
        return ""
    low = t.lower()
    for name in _KNOWN_MAPS:
        if low == name.lower() or (len(low) >= 6 and name.lower().endswith(low)):
            return name
    return t


class FMWatcherAdapter(BaseAdapter):
    name = "fmwatcher"
    label = "FM Watcher (your own scans)"
    base_url = ""
    interval_s = 5.0
    rate = 100.0
    burst = 100

    def __init__(self, *args: Any, directory: str = "", max_age_s: float = 1800.0, **kwargs: Any):
        super().__init__(*args, **kwargs)
        self.directory = Path(directory).expanduser() if directory else None
        self.max_age_s = max_age_s
        self.polls = 0
        self.paused = False                 # Physical Scanner "Stop Scanning": no file reads, status UNAVAILABLE
        self.last_file_write = 0.0
        self.rows_last_read = 0
        self.rows_total_session = 0
        log.info("fmwatcher: adapter created, folder=%s interval=%ss max_age=%ss", self.directory, self.interval_s, max_age_s)
        self._store_mtime = 0.0
        self._csv_mtime = 0.0
        self._seen: dict[str, float] = {}       # listing_key -> last 'last' timestamp handed out
        self._latest: dict[str, tuple[str, float, str]] = {}   # listing_key -> (status, at, note) from the last file read
        self.file_used = ""
        self.verify_supported = True
        self.verify_limit_note = "only shops you re-open are re-checked; others stay UNVERIFIED"
        if self.directory is None:
            self._set_status(SourceStatus.UNSUPPORTED, "FM Watcher folder not set (Settings → Sources)")

    def _make_client(self):  # no HTTP at all
        import httpx

        return httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(404)))

    # ------------------------------------------------------------------ files
    def _paths(self) -> tuple[Optional[Path], Optional[Path]]:
        if self.directory is None:
            return None, None
        return self.directory / STORE_FILE, self.directory / CSV_FILE

    def _rows_from_store(self, path: Path) -> list[Listing]:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        shop = data.get("shop") or {}
        now = time.time()
        out: list[Listing] = []
        for key, o in shop.items():
            try:
                day, seller, item, price, qty = json.loads(key)
            except (TypeError, ValueError):
                continue
            if not isinstance(o, dict) or not item or not price:
                continue
            last = float(o.get("last") or o.get("first") or 0)
            if now - last > self.max_age_s:
                continue
            open_count = int(o.get("nu", 0) or 0)
            sold = bool(o.get("so")) and open_count == 0
            if open_count <= 0 and not sold:
                qty0 = int(qty or 1) or 1
                unit0 = int(round(float(price) / qty0))
                st0 = _stats(o.get("x"))
                lk0 = listing_key(self.name, item_key(str(item), "Equip" if st0 or o.get("xj") else None, st0), str(seller),
                                  int(o["c"]) if o.get("c") else None, unit0)
                self._latest[lk0] = ("REMOVED", last, "your latest scan of this shop no longer shows it")
                continue
            qty = int(qty or 1) or 1
            unit = int(round(float(price) / qty))
            if unit <= 0:
                continue
            stats = _stats(o.get("x"))
            category = "Equip" if stats or o.get("xj") else None
            ik = item_key(str(item), category, stats)
            ch = int(o["c"]) if o.get("c") else None
            room_txt = _room_text(o.get("r"))
            room = parse_fm_room(room_txt)
            lk = listing_key(self.name, ik, str(seller), ch, unit)
            self._latest[lk] = (("SOLD", last, "your scan saw the slot sold") if sold
                                else ("ACTIVE", last, "open in your latest scan of this shop"))
            if self._seen.get(lk) == last:
                continue            # nothing new since we last handed this row out
            self._seen[lk] = last
            out.append(Listing(
                listing_key=lk, source=self.name, item_name=str(item), item_key=ik, source_item_id=str(item),
                category=category, stats=stats, seller=str(seller), channel=ch, fm_room=room,
                map_name=room_txt or None, price=unit, qty=qty, stock=open_count or None, sold=sold,
                source_seen_at=last, source_first_seen_at=float(o.get("first") or last),
                source_url="https://maplefm.gg/",
            ))
        if len(self._seen) > 50_000:
            cutoff = now - self.max_age_s
            self._seen = {k: t for k, t in self._seen.items() if t > cutoff}
        return out

    async def verify(self, listing: Listing) -> Optional[tuple[str, float, str]]:
        hit = self._latest.get(listing.listing_key)
        if hit is None:
            return None
        status, at, note = hit
        if status == "ACTIVE" and at <= listing.source_seen_at:
            return "ACTIVE", at, note          # same observation we alerted on: confirms nothing newer
        return status, at, note

    def _rows_from_csv(self, path: Path) -> list[Listing]:
        now = time.time()
        out: list[Listing] = []
        with open(path, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                try:
                    ts = time.mktime(time.strptime(f"{r['Date']} {r['Time']}", "%Y-%m-%d %H:%M"))
                except (KeyError, ValueError):
                    continue
                if now - ts > self.max_age_s or (r.get("Source") or "shop") != "shop":
                    continue
                status = (r.get("Status") or "").strip()
                if status == "buying":
                    continue
                try:
                    unit = int(float(r.get("Each") or 0)) or int(float(r.get("Price") or 0))
                    qty = int(float(r.get("Bundle") or 1)) or 1
                except ValueError:
                    continue
                if unit <= 0:
                    continue
                stats = None
                if r.get("Stats"):
                    parts = r["Stats"].split()
                    stats = {parts[i].lower(): int(parts[i + 1]) for i in range(0, len(parts) - 1, 2) if parts[i + 1].lstrip("-").isdigit()} or None
                ik = item_key(r["Item"], "Equip" if stats else None, stats)
                ch = int(r["Channel"]) if (r.get("Channel") or "").strip().isdigit() else None
                lk = listing_key(self.name, ik, r.get("Seller") or "?", ch, unit)
                if self._seen.get(lk) == ts:
                    continue
                self._seen[lk] = ts
                out.append(Listing(listing_key=lk, source=self.name, item_name=r["Item"], item_key=ik, source_item_id=r["Item"],
                                   category="Equip" if stats else None, stats=stats, seller=r.get("Seller") or "?",
                                   channel=ch, fm_room=parse_fm_room(r.get("Room") or ""), map_name=r.get("Room") or None,
                                   price=unit, qty=qty, sold=status == "sold", source_seen_at=ts, source_url="https://maplefm.gg/"))
        return out

    # ------------------------------------------------------------------ poll
    async def poll(self) -> list[Listing]:
        self.polls += 1
        if self.paused:
            self._set_status(SourceStatus.UNAVAILABLE, "stopped by you (Physical Scanner → Stop Scanning)")
            return []
        store, csv_path = self._paths()
        if store is None:
            self._set_status(SourceStatus.UNSUPPORTED, "FM Watcher folder not set (Settings → Sources)")
            return []
        if not self.directory.is_dir():
            raise FetchError(f"FM Watcher folder not found: {self.directory}")
        if store.exists():
            path, m, self.file_used = store, os.path.getmtime(store), STORE_FILE
            if m == self._store_mtime:
                return self._idle(m)
            try:
                rows = self._rows_from_store(store)
            except (OSError, ValueError) as exc:   # mid-write: the watcher replaces the file atomically, retry next poll
                raise FetchError(f"could not read {STORE_FILE}: {exc}") from exc
            self._store_mtime = m
        elif csv_path.exists():
            path, m, self.file_used = csv_path, os.path.getmtime(csv_path), CSV_FILE
            if m == self._csv_mtime:
                return self._idle(m)
            rows = self._rows_from_csv(csv_path)
            self._csv_mtime = m
        else:
            raise FetchError(f"neither {STORE_FILE} nor {CSV_FILE} in {self.directory} — is FM Watcher running / exported?")
        self._last_file_mtime = m
        self.last_file_write = m
        self.rows_last_read = len(rows)
        self.rows_total_session += len(rows)
        newest = max((r.source_seen_at for r in rows), default=None)
        now = time.time()
        # file age = how fresh the scanner's last write is; that is what ONLINE/STALE should reflect
        self.mark_success(max(0.0, now - m), len(rows))
        self.health.detail = (f"reading {self.file_used} (written {time.strftime('%H:%M:%S', time.localtime(m))}); "
                              f"{len(rows)} new/updated rows" + (f", newest scan {now - newest:.0f}s ago" if newest else ""))
        log.info("fmwatcher: poll #%d read %s (mtime %s): %d new/updated listings%s", self.polls, path,
                 time.strftime("%H:%M:%S", time.localtime(m)), len(rows),
                 f", newest {now - newest:.0f}s ago" if newest else "")
        return rows

    def _idle(self, mtime: float) -> list[Listing]:
        """File unchanged since the last read: still ONLINE (the scanner just has nothing new), STALE after 10 min."""
        self.last_file_write = mtime
        age = time.time() - mtime
        if age > 600:
            self._set_status(SourceStatus.STALE, f"{self.file_used} not updated for {age/60:.0f} min — is FM Watcher running and are you in the FM?")
        else:
            self.mark_success(age, 0)
            self.health.detail = f"reading {self.file_used}; no new scans in the last {age:.0f}s"
        if self.polls % 60 == 0:
            log.info("fmwatcher: %s unchanged for %.0fs (status %s)", self.file_used, age, self.health.status.value)
        return []

    def online_detail(self) -> str:
        return f"reading {self.file_used}" if self.file_used else ""

    def stale_after_s(self) -> float:
        return 600.0
