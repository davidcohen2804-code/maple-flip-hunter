# Verification report — GitHub Actions run 9 (windows-latest), 2026-10-25 17:37 UTC
Full report with per-test lists: artifact `reports` of run 40216504642 and `VERIFICATION_REPORT.md` inside `MapleFlipHunter-win64.zip`.
https://github.com/davidcohen2804-code/maple-flip-hunter/actions/runs/40216504642

## Overall status: PARTIALLY VERIFIED
* cozy.markets: **BLOCKED** — HTTP 403 for https://cozy.markets/api/shops from the GitHub datacenter network (Cloudflare). Not bypassed. Must be re-tested from a home PC (`run_live_check.bat`).
* maplestory.watch: **OK** — 1,352 items, index age 144 s, newest listing 151 s (real data, Windows).
* audio playback: **NOT VERIFIED** — the runner has no audio output device (3 audio tests skipped with notice). Tray notification, main window + JACKPOT banner, startup registry key: **PASS**.
* Environment: Windows Server 2025 (10.0.26100), Python 3.12.10.

## Test suites on Windows
* unit-results.xml: 47 tests, 0 failed, 0 skipped.
* windows-results.xml: 4 passed, 3 skipped (audio — no device).
* live-results.xml: maplestory.watch freshness PASS; end-to-end 120 s run PASS; cozy skipped as BLOCKED.

## Packaged EXE (MapleFlipHunter.exe) — 90 s headless run on the runner
* scanner state: **LIVE** (kept scanning with cozy blocked)
* sources: cozy.markets = AUTH REQUIRED (HTTP 403); maplestory.watch = ONLINE (newest data age 144 s)
* rows ingested: 1,180 (new 1,180, repriced 0, cross-source duplicates 0)
* evaluations: 1,122; alerts: 0; duplicate alerts suppressed: 0
* avg local analysis: 22.6 ms (p95 6.2 ms)
* source observation → our first sight (seconds): n=200, min 184, median 14,557, p95 42,542, max 46,616, avg 15,675
  — maplestory.watch candidate rows are hours old on average; this is the SOURCE's age, not our delay.
* **source→alert: no alerts fired** — correct: every candidate row was older than the 15-minute stale limit, so no JACKPOT/GOOD alarm was allowed. The stale-JACKPOT gate passed (0 violations). The seconds-to-alert figure for cozy (the fast source) can only be measured from a network cozy accepts (home PC).
* poll interval (msw, chunked): 52.7 s; memory RSS 121 MB; CPU ~0 %
* coverage: 1,352 catalog items; 68 items with listings; 1,180 rows in the last hour (maplestory.watch only)
