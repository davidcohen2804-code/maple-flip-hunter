"""Home check for the FM Watcher source: python tools\\check_fmwatcher.py "C:\\path\\to\\FMWatcher"
Prints what Maple Flip Hunter would read from that folder right now (no network, nothing written)."""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from maple_flip_hunter.adapters.fmwatcher import CSV_FILE, STORE_FILE, FMWatcherAdapter  # noqa: E402

folder = sys.argv[1] if len(sys.argv) > 1 else ""
if not folder:
    print("usage: check_fmwatcher.py <FM Watcher folder>"); sys.exit(2)
for f in (STORE_FILE, CSV_FILE, "start.bat", "watcher.log"):
    p = os.path.join(folder, f)
    print(f"{f:22s} {'found' if os.path.exists(p) else 'MISSING':8s}" + (f"  (updated {time.time() - os.path.getmtime(p):.0f} s ago)" if os.path.exists(p) else ""))
a = FMWatcherAdapter(directory=folder, max_age_s=24 * 3600)
try:
    rows = asyncio.new_event_loop().run_until_complete(a.poll())
except Exception as exc:  # noqa: BLE001
    print("ERROR:", exc); sys.exit(1)
print(f"\n{len(rows)} listings from your scans (last 24 h), newest first:")
for r in sorted(rows, key=lambda r: -r.source_seen_at)[:15]:
    print(f"  {time.strftime('%H:%M:%S', time.localtime(r.source_seen_at))}  {r.item_name:32s} {r.price:>10,}  {r.location:14s} shop {r.seller}"
          + ("  SOLD" if r.sold else ""))
print("\nOK — point Settings → Sources → FM Watcher folder at this folder." if rows else
      "\nNo recent rows: run start.bat with the game open, open a few shops in the FM, then run this again.")
