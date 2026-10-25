# Test results

## Synthetic unit tests — executed 2026-10-25 (Linux x86-64, Python 3.13.17, PySide6 6.12.0, QT_QPA_PLATFORM=offscreen)

Command: `python -m pytest tests/unit -q` → **47 passed, 0 failed** (V3, 3.4 s); Windows runtime suite (6) and live suite (3) skipped off-Windows / without MFH_LIVE.

| Spec case (§15) | Test | Result |
|---|---|---|
| 1. Scroll worth 120K listed for 16K | `test_1_scroll_120k_listed_16k_is_jackpot` — reference 120,000, quick 114,000, net 94,580, JACKPOT | PASS |
| 2. 50K item listed for 25K | `test_2_50k_item_listed_25k_is_good_flip` — quick 47,500, net 21,075, GOOD FLIP | PASS |
| 3. Equipment with different stats | `test_3_equipment_with_different_stats_is_not_compared` — distinct variant keys; unknown stats → UNCERTAIN, never JACKPOT; same-variant comps → JACKPOT | PASS |
| 4. Stale listing | `test_4_stale_listing_is_not_alerted` — 1 h old row classified but no alert | PASS |
| 5. Many cheaper competitors | `test_5_many_cheaper_competitors_cap_the_quick_price` — ten asks at 35K cap reference to 36K, quick 33,950 (not 100K) | PASS |
| 6. Little sales history | `test_6_little_sales_history_lowers_confidence` — ask-derived reference, liquidity UNKNOWN, UNCERTAIN; no data → no valuation | PASS |
| 7. New listing price reduction | `test_7_price_reduction_creates_fresh_alert_same_price_does_not` | PASS |
| 8. Duplicate data from two sources | `test_8_duplicate_from_second_source_is_merged` — one alert, duplicate counted | PASS |
| 9. Failed API request | `test_9_failed_api_request_marks_source_unavailable_without_crashing` — 3 attempts, UNAVAILABLE, loop survives | PASS |
| 10. Rate-limited source | `test_10_rate_limited_source_backs_off` — 429 + Retry-After honoured, no requests during back-off | PASS |
| 11. Rapid consecutive updates | `test_11_rapid_consecutive_updates` — 20 rows < 5 s, p95 analysis < 2 s | PASS |
| 12. Selling-fee calculation | `test_12_selling_fee_calculation` — 0 %, 3 %, 10 % | PASS |
| 13. Alert deduplication | `test_13_alert_deduplication_across_restarts` — persisted in SQLite | PASS |
| 14. Windows notification behaviour | `test_alert_history_and_toast_callback`, `test_windows_audio_backend` (backend selection; playback exercised on the Windows CI runner), sound synthesis | PASS (silent backend on Linux) |
| 15. Database recovery / persistence | `test_15_database_persistence_and_recovery` — reopen file DB, counts, retention cleanup | PASS |
| V2 | collection coverage: 60 changed shops / 25 per cycle → all fetched; shop pagination; deals pagination; backlog reporting; msw candidate carry-over; same-scan multi-unit rows independent; stale reference stats cap confidence | PASS |
| extra | dropped-digit guard (real pattern: same shop 74,999 / 7,499 → UNCERTAIN), unknown shop name caps confidence (GOOD max), adapter parsing of recorded real responses (cozy shops/listings/deals/item, msw index/item), normalization, settings round-trip, token bucket, prefilter, sold rows → sales, UNCERTAIN hidden | PASS |

Measured in `test_11`: 20 evaluations in well under 1 s total; per-evaluation local analysis 0–3 ms (no network in tests).

## Live integration tests (`tests/live`, real sources)
Skipped unless `MFH_LIVE=1`. They could **not** run inside the development container (its egress proxy returns
HTTP 403 for cozy.markets and maplestory.watch — verified with curl and with the packaged binary). They run on the
GitHub Actions **windows-latest** job defined in `.github/workflows/windows-build.yml`, which stores
`tests/live/last_live_measurements.json`, `live-results.xml` and `exe_headless_result.json` as artifacts.

Status: **see the "CI evidence" section below — filled in only after a Windows run actually completed.**

## Windows executable
Local Linux build of the same PyInstaller spec: `dist/MapleFlipHunter` (293 MB one-folder), binary launched with
`--headless --seconds 3` and exited 0 (sources 403 from the container proxy, as expected). The Windows build is
produced by the CI job; it has **not** been claimed as tested on Windows until the CI evidence below exists.

## CI evidence (Windows) — GitHub Actions windows-latest (Windows Server 2025, Python 3.12.10), 2026-10-25
Runs 1–9 of `windows-build` (https://github.com/davidcohen2804-code/maple-flip-hunter/actions). Bugs found and fixed by CI:
clock-resolution bug in the queue time budget (run 1), headless state reported after stop (run 4), maplestory.watch first
poll too long with a 621-candidate backlog (run 6), report encoding on Windows consoles (run 8).

Run 8/9 results:
* Unit tests: 47 passed on Windows.
* Windows runtime: tray notification, main window + JACKPOT banner, startup registry key — **PASS**; audio playback —
  **NOT VERIFIED** (GitHub runners have no audio device even after installing a virtual one; tests skipped explicitly).
* Live: maplestory.watch — **PASS** (1,348-item index, real listings). cozy.markets — **BLOCKED (HTTP 403)** from the
  GitHub datacenter network (Cloudflare). Not bypassed. Must be re-tested from a home PC (`run_live_check.bat`).
* Packaged EXE, 90 s headless on the runner: **LIVE**, 1,063 rows ingested, 1,021 evaluated, avg analysis 26 ms,
  0 alerts — the maplestory.watch candidate rows averaged ~5 h old and the stale guard correctly refused to alarm.
  Scanner kept running with one source blocked. Stale-JACKPOT gate passed.
* Overall: **PARTIALLY VERIFIED** — see `VERIFICATION_REPORT.md` inside the Windows package.
