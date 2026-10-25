# REVIEW_FOR_CHATGPT — Maple Flip Hunter v0.1.0

This file is the hand-off for an independent code review. Everything stated here is backed by code in this
ZIP or by measurements recorded in `docs/` and `tests/live/last_live_measurements.json`. Where something was not
verified, it says so.

## 1. What was actually built
A complete Windows desktop application (Python 3.12+, PySide6, httpx, pydantic, SQLite, PyInstaller):
* `maple_flip_hunter/adapters/` — two working source adapters (cozy.markets, maplestory.watch) with rate limiting,
  exponential back-off, 429/403 handling and per-source health (`ONLINE / STALE / RATE LIMITED / AUTH REQUIRED /
  UNAVAILABLE / UNSUPPORTED`).
* `collector/engine.py` — background asyncio collector; normalization → SQLite upsert (new / repriced / seen /
  duplicate) → prefilter → evidence merge (primary source + secondary source + local DB) → pricing → classification →
  alert dispatch; metrics for every timestamp the spec asks for.
* `pricing/engine.py`, `detection/detector.py` — reference / quick / conservative prices, net profit with configurable
  fee, explainable confidence and liquidity, JACKPOT / GOOD FLIP / UNCERTAIN, stale suppression, duplicate-alert
  prevention with repricing support.
* `notifications/` — synthesized WAV alarms (no external assets), Qt Multimedia playback with volume (winsound
  fallback), tray toasts, history table.
* `ui/` — dark dashboard (status strip, filterable table, details panel with icon and pricing explanation, settings
  dialog, tray, jackpot banner, copy buttons, optional Windows startup).
* `tests/` — 33 synthetic unit tests (all pass) + live integration tests + headless measurement runner.
* `build/windows/` — PyInstaller spec + `build_exe.bat`; `.github/workflows/windows-build.yml` builds and smoke-runs
  `MapleFlipHunter.exe` on a Windows runner.

## 2. Verified market sources
| Source | Verified | Role |
|---|---|---|
| cozy.markets | yes — HTTP 200 JSON, fields and freshness measured (docs/SOURCE_AUDIT.md) | primary (listings, shops, deals, item stats, variant comps, icons) |
| maplestory.watch | yes — HTTP 200 JSON, measured | secondary (14-day sale medians, candidate listings) |

## 3. Exact endpoints used
cozy.markets (GET): `/api/shops?page=1&size=100&sort=last_at&dir=desc`, `/api/shops/{shop}/listings?page=1&size=100`,
`/api/deals?page=1&size=100&kind=under-fm&seen=1h&sort=at&dir=desc`, `/api/items?page=N&size=100`,
`/api/items/{id}`, `/api/items/{id}/sightings?page=1&size=100&since=<unix>&stats=watk:56,slots:7`, `/api/icons/{key}.png?v=3`.
maplestory.watch (GET): `/api/items`, `/api/item?name=<name>`.
All unauthenticated, honest User-Agent, self-imposed limits (3 req/s and 1.5 req/s). No HTML scraping anywhere.

## 4. Integrations that failed / were excluded and why
* freemarketclassic.com — ToS forbid automation of the website; no API. Excluded.
* maplemarket.fyi — paid; ToS forbid scraping/republishing. Excluded.
* maplefm.gg — ToS require written permission (they say they usually grant it). Not integrated; recommended follow-up.
* beta.meso.watch — automated access refused. Excluded.
* meowdb.com — only a manual price list; its figure arrives through cozy's `meow` field.
None of these were "attempted and broken": they were excluded on access-rule grounds before writing code.

## 5. Actual source freshness measurements (2026-10-25, see docs/SOURCE_AUDIT.md / DATA_FRESHNESS.md)
* cozy `/api/shops` newest scan age: 5 s, 30 s. `/api/deals` newest rows: 20–60 s. CDN cache 5 s / 15 s.
* maplestory.watch index age 104 s, newest listing 126 s; cache 203 s / 300 s.
* Seller publication time: not available from any source (displayed as unknown).
* End-to-end `source→alert` numbers are produced by the live tests / CI (`metrics.source_to_alert_avg_s`).

## 6. Whole-market discovery
Yes. No watchlist exists. cozy `/api/shops` (sorted by last scan) tells us which shops changed; every row of each
changed shop is ingested (≤25 shops per 10 s cycle; the deals feed covers the rest of the under-reference rows).
Catalog: 1,377 items from cozy + 1,348 from maplestory.watch, growing with any new name. Coverage is displayed as
"items seen / catalog · rows last hour" and is explicitly limited to what community scanners uploaded.

## 7. How item statistics are compared
`collector/normalize.py`: equipment item key = normalized name + signature of value stats
(`watk, matk, str, dex, int, luk, wdef, mdef, acc, avoid, speed, jump, slots`). Different signatures never share a key.
For a listing with stats, cozy comparables are fetched with the server-side `stats=` filter and the item-wide sold
median is replaced by the same-variant sale median. Equipment with unknown stats gets key `name#?`, confidence capped
at 0.40 → UNCERTAIN only. Scroll percentages are part of the item name ("Intermediate" = 60 %, "Greater" = 10 %).

## 8. How quick-sale prices are estimated
`pricing/engine.py`: reference from completed sales (≥5) → thin sales → 90 % of asks (≥5) → 90 % of secondary;
capped by the median of the 5 cheapest live competitors when they sit <75 % of the reference. Quick =
min(0.95 × reference, 0.97 × cheapest live competitor). Conservative = 0.90 × quick, capped by the 3rd-cheapest ask
and the ask p25. Net = quick × (1 − fee) − buy. Details in docs/PRICING_ENGINE.md.

## 9. JACKPOT / GOOD FLIP classification
Budget ≤ 75,000 and net ≥ 15,000 (defaults). JACKPOT: net ≥ 50,000 or (ROI ≥ 100 % and net ≥ 25,000), confidence
≥ 0.60, liquidity HIGH/MEDIUM → siren + banner + toast. GOOD FLIP: confidence ≥ 0.45, liquidity HIGH/MEDIUM → chime +
toast. UNCERTAIN: shown, silent. Stale (>15 min) never alerts. Same row never alerts twice; repricing creates a new key.

## 10. Test results, including failures
* Unit: 33 passed / 0 failed (docs/TEST_RESULTS.md has the per-case table).
* Live tests: could not run in the development container (egress proxy 403). They run in the Windows CI job;
  results are attached as CI artifacts and summarized in docs/TEST_RESULTS.md "CI evidence" once available.
  **If that section is still "Pending", treat live behaviour as unverified by automated tests.**
* During development three tests initially failed and were fixed: (a) rapid-update test used one item for 20
  sellers, so earlier rows correctly became competitors — test rewritten with distinct items; (b) equipment with
  unknown stats scored 0.75 — engine changed to cap at 0.40; (c) back-off made the failure test exceed its timeout —
  `retry_base_delay` made configurable.

## 11. Actual performance measurements
Synthetic: per-candidate analysis 0–3 ms CPU; one cold evidence fetch ≈ 300–450 ms (measured HTTP round-trip);
20 candidates evaluated in < 1 s. Source freshness: §5. Memory/CPU: reported by `metrics.snapshot()` (psutil) and
written in the headless result / CI artifact. Expected source→alert ≈ 5–20 s after a row reaches cozy.markets
(5 s CDN + ≤10 s poll + fetch); confirm against `last_live_measurements.json`.

## 12. How to run
`run_dev.bat` (creates venv, installs, starts) or `python -m maple_flip_hunter`. Headless measurement:
`python -m maple_flip_hunter.headless --seconds 120`. Tests: `python -m pytest tests/unit`; live: `set MFH_LIVE=1 && python -m pytest tests/live -rA`.

## 13. How to build the Windows EXE
`build\windows\build_exe.bat` → `dist\MapleFlipHunter\MapleFlipHunter.exe` (one-folder). Or push to GitHub: the
workflow builds it on windows-latest and uploads `MapleFlipHunter-win64.zip`.

## 14. Known bugs and limitations
docs/LIMITATIONS.md. Highlights: community-scanned coverage; no seller timestamps; equipment rows without stats
are never alarmed; the EXE is unsigned (SmartScreen warning); the Windows build was verified on a CI runner only.

## 15. What ChatGPT should examine first
1. `collector/engine.py::ingest / gather_evidence / evaluate` — the hot path and the evidence merge.
2. `pricing/engine.py` — reference selection order, competitive cap, confidence table.
3. `adapters/cozy.py::poll` — watermark logic (could a repriced row be missed if a shop updates twice within one cycle?)
   and `fetch_evidence` (variant filter, exclusion of the candidate from its own competitors).
4. `db/database.py::upsert_listing` — repricing detection uses "latest unsold row of same seller+item"; review the
   edge case of a seller listing the same item twice at different prices (second row is treated as a reprice).
5. `detection/detector.py` — thresholds vs. spec §9.
6. `tests/live/test_live_sources.py` and the CI artifacts — are the measured latencies acceptable?

## 16. Next technical improvements
* Ask maplefm.gg for data permission and add a third adapter (sales-based, 1-minute updates).
* Persist evidence cache to SQLite to cut cold-fetch latency after restart.
* Per-category fee/thresholds (NX vs scrolls vs equipment) and a learned liquidity model from our own `sales` table.
* Background pre-fetch of `/api/items/{id}` for items with frequent under-reference rows (warm cache → sub-100 ms analysis).
* Code-sign the EXE; add an installer (Inno Setup) with an optional startup task.
* Optional Discord/Telegram push for alerts while away from the PC.
