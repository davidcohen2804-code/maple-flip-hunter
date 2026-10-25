# V2 fix report (response to the ChatGPT review of V1)

Date: 2026-10-25. Scope: the four review items plus the two extra concerns (per-cycle item limits, stale price data).

## What was fixed

### 1. CozyAdapter.poll could lose shops when >25 shops changed in one cycle — FIXED
Old behaviour: the watermark advanced to the newest `last_at` of the page while only the first 25 fresh shops were
fetched; the rest fell behind the watermark and were never fetched.
New behaviour (`adapters/cozy.py`):
* `_discover_changed_shops()` walks `/api/shops` newest-first and puts **every** changed shop into a persistent
  pending queue (`_pending`), paginating (≤5 pages = 500 shops) while a whole page is newer than the watermark.
* `_drain_pending()` fetches up to `max_shops_per_cycle` queued shops per cycle, newest first; the rest stay queued.
  A shop that is re-scanned while queued keeps its newest timestamp. A shop whose listing fetch fails is retried and
  dropped after 3 failures (logged).
* `_poll_deals()` paginates the under-reference feed (≤5 pages) until rows older than the deals watermark appear,
  so bursts of >100 cheap rows in one cycle are not truncated either.
* Backlog is exposed (`adapter.backlog`, health detail "shop queue N") and logged when it exceeds 2× the per-cycle cap.
Load tests (`tests/unit/test_collection_coverage.py`): 60 changed shops with a 25/cycle cap → all 60 fetched exactly
once over 3 cycles, nothing re-fetched; 150 fresh shops → pagination used, 150 listings; backlog reported and newest
shops served first; re-scanned queued shop promoted; failing shop retried then dropped; 130 deal rows across 2 pages
all ingested.

### 2. Repricing inferred only from seller+item — FIXED (within what the sources allow)
Neither source exposes a per-listing id (cozy rows carry the *item* id only; maplestory.watch rows carry none), so a
stable listing id cannot be used. The inference was tightened instead (`db/database.py::upsert_listing`):
* a reprice is recognised only when the earlier row of the same seller / item **variant** / source comes from an
  **older scan** (gap > 90 s) *and* that earlier price is no longer present in the current scan;
* rows observed in the same scan at different prices (several units) are stored as independent listings and the
  earlier row is no longer flagged as superseded;
* the superseded row keeps `sold=0` (history) and only its stock is zeroed.
Tests: `test_same_seller_several_units_at_different_prices_in_one_scan_are_independent` (3 units at 3 prices → 3
"new", 0 "repriced", all 3 remain active competitors; later cheaper scan → 1 "repriced"), `test_7` updated.
Note: independent cheap units of the same shop count as live competitors in the valuation — deliberately
conservative.

### Extra concern A — per-cycle item limit on maplestory.watch — FIXED/VERIFIED
Unfetched candidates were already carried over (their `seen` watermark is only advanced after a fetch); this is now
tested (`test_msw_unfetched_candidates_remain_candidates`: 50 candidates, 20/cycle → all 50 fetched over 3 cycles),
the backlog is exposed in health, and the default cap was raised to 60.

### Extra concern B — stale price data in profit calculations — FIXED
`pricing/engine.py`: if the reference statistics behind the valuation are older than `max_evidence_age_s` (6 h,
configurable) a risk line is added and confidence is capped at 0.50 (GOOD FLIP at most, never JACKPOT). Completed-sale
comparables are already restricted to 7 days and competing asks to 24 h; the evidence cache TTL is 5 minutes.
Test: `test_stale_reference_statistics_cap_confidence`.

### 4. CI gating — FIXED
`.github/workflows/windows-build.yml` now: unit tests → build EXE → upload **UNVERIFIED** artifact → Windows runtime
checks (real audio via winsound and Qt Multimedia, tray notification, window + banner, startup registry key) → live
source tests (120 s) → packaged-EXE headless smoke run gated by `tools/check_headless_result.py` (must be LIVE/DEGRADED
with rows ingested) → `VERIFICATION_REPORT.md` → **VERIFIED** artifact. Any failure stops the job before the
VERIFIED artifact exists. The same sequence runs locally with `run_live_check.bat`.

## What is still NOT verified (honest status)
### 3. Real connection tests on Windows — NOT YET RUN
The development environment cannot reach cozy.markets / maplestory.watch (egress proxy returns 403) and has no
Windows. The live tests, the Windows runtime tests and the packaged-EXE smoke run therefore have **no recorded
results yet**. Two ways to obtain them, both already wired:
1. Push the repository to GitHub → the `windows-build` workflow runs on `windows-latest` and attaches
   `reports/` + the VERIFIED zip (only if everything passed).
2. On any Windows 10/11 PC with Python: run `run_live_check.bat` → `reports\VERIFICATION_REPORT.md`.
Until one of these has run, every latency figure in the docs is a measurement of the *sources* (made from a browser
on 2026-10-25) plus synthetic engine timings — **not** an end-to-end Windows measurement, and the release must be
treated as UNVERIFIED.

## Test count
Unit suite: **42 passed** (was 33). Windows suite: 6 tests (skipped off-Windows). Live suite: 3 tests (skipped unless
`MFH_LIVE=1`).

---

# V3 additions (second review round)

### 500-shop cap and extreme bursts — FIXED
* The page valve is now a pure safety valve (200 pages = 20,000 shops per cycle, 50 pages = 5,000 deal rows). When it
  is hit, the **watermark is not advanced** and the walk **resumes next cycle from the page where it stopped** (one
  page of overlap, after a quick look at page 1 for brand-new scans), so no shop can fall behind the watermark.
* The drain of the shop queue is now time-budgeted (80 % of the poll interval, at least `max_shops_per_cycle`
  shops): under load the collector fetches as many queued shops as fit instead of a fixed 25, so the queue cannot grow
  unbounded while the per-cycle floor still bounds the request rate in quiet periods.
* Tests: 1,200 shops changed in one cycle (12 pages) with a strict cap → every shop fetched exactly once, nothing lost;
  valve forced to 2 pages on 400 changed shops → resumes over following cycles until all 400 are collected; time budget
  drains a 100-shop backlog in one cycle; deals valve keeps its watermark and the remaining rows arrive next cycle
  without duplicates.
* Remaining, documented start-up rule: on first start only shops scanned in the last 5 minutes are processed
  (older rows are stale by definition and never alerted anyway).

### Shop scans limited to the first 100 rows — FIXED
`_fetch_shop_rows()` walks every page of `/api/shops/{shop}/listings` (rows are newest-first; it stops at the page
whose rows are older than what was already processed for that shop). Test: a shop with 250 rows → 3 pages, 250 rows.

### Live latency measurement — instrumented, NOT YET RUN
`metrics.source_to_alert_s` now records the distribution (n/min/median/p95/max/avg) of
*notified_at − source observation timestamp*, and `source_age_at_ingest_s` the same for observation → first sight.
Both appear in `VERIFICATION_REPORT.md`. **No Windows run has been executed yet** (see §3 above): the GitHub
repository `davidcohen2804-code/maple-flip-hunter` does not exist at the time of writing, and the development
environment cannot reach the sources. Unit suite: **47 passed**.
