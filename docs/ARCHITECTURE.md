# Architecture

```
maple_flip_hunter/
  adapters/        1. market source adapters (cozy.py, maplestory_watch.py, base.py: rate limit / retry / health)
  collector/
    normalize.py   3. normalization: item keys, stat signatures, FM-room parsing, listing / cross-source keys
    engine.py      2. async collector + 7–11: evidence merge, pricing, detection, alert dispatch, 13. health, metrics
  db/database.py   4–6. SQLite: items (catalog), listings (current + history), listing_events, sales, opportunities,
                   notifications, source_status, kv; retention cleanup
  pricing/engine.py   8. pricing engine (reference / quick / conservative / net / confidence / liquidity)
  detection/detector.py  9–10. classification + duplicate-alert suppression
  notifications/   11. synthesized WAV alarms, Qt Multimedia / winsound playback, toast callback, history
  ui/              12. PySide6 dashboard: status strip, opportunity table, details, settings, tray, jackpot banner
  metrics.py       14/18. rolling latency + throughput measurements, process CPU/RSS
  config.py        settings (pydantic → %APPDATA%\MapleFlipHunter\settings.json)
  headless.py      engine without GUI (CI measurements, diagnostics)
  app.py / __main__.py  bootstrap
```

## Threads
* **Qt main thread** — UI only. Never does network I/O (icons are fetched with `QNetworkAccessManager`, async).
* **market-engine thread** — one asyncio loop; one task per adapter (`BaseAdapter.run`) + housekeeping.
  Adapter failures are isolated: a task that raises logs, marks its source UNAVAILABLE and keeps polling.
* Engine → UI communication goes through `ui/bridge.py` (Qt signals, queued connection) so the dashboard
  never freezes on network latency. SQLite is shared behind an `RLock` (WAL mode).

## Pipeline for one observed row
```
adapter.poll() → Listing(s)
  → engine.ingest: record sale | upsert listing (new / repriced / seen / duplicate)
  → prefilter (within budget and ≥15 % under the source's own reference)
  → gather_evidence: primary adapter (item stats + variant-filtered comps) + secondary index + local DB asks/sales
  → PricingEngine.value → Detector.classify → save opportunity → emit to UI
  → Detector.should_alert (dedupe, level filters, stale) → Notifier (sound + toast) → metrics + DB notification
```
Target: < 2 s local processing after a complete row — measured 0–3 ms CPU plus one cached evidence fetch (≈300 ms)
when cold.

## Incremental collection
* cozy: `/api/shops?sort=last_at` is a cheap "what changed" list (paginated while a whole page is new); every changed
  shop enters a persistent queue and ≤25 are fetched per 10 s cycle, newest first — the rest wait, never dropped.
  `/api/deals seen=1h` is paginated until the watermark. Per-shop watermarks prevent re-emitting rows.
* maplestory.watch: one index fetch per 3 min; item pages only for items whose `lowest` is ≥20 % under
  `typical` and whose `seen` moved (≤40 per cycle).
* Evidence (`/api/items/{id}`, sightings) is cached 5 min per item/variant.

## Whole-market discovery
No watchlist exists anywhere in the code. Every row from every changed shop is normalized and stored; the item
catalog is built from cozy's `/api/items` (1,377 items) and maplestory.watch's index (1,348 items) and grows with
any new name that appears in a shop.

## Security
Outbound HTTPS only to the two sources; no credentials, no game process access, no automation of the game.
Settings/DB/logs live under `%APPDATA%\MapleFlipHunter`. "Run at startup" writes only the per-user `HKCU\…\Run` key.
