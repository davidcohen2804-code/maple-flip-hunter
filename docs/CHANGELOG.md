# Changelog

## 0.5.0 — 2026-10-25 (alert filters)
* Settings: **Minimum purchase price** (default 2,500) and **Maximum purchase price** (default now 10,000). The scanner still
  ingests and stores the whole market; only listings inside the band are evaluated for alerts.
* Settings: **QUICK FLIPS ONLY** mode — JACKPOT / GOOD FLIP alerts require net profit at the CONSERVATIVE sale price after
  fee ≥ 10,000, confidence ≥ 0.80, listing *and* reference data ≤ 5 min old, HIGH liquidity (configurable); missing data
  ⇒ no alert (shown as UNCERTAIN).
* One shared filter (`detection.passes_user_filters`) now drives both the alerts and the **opportunity table**: price band
  + minimum net profit (conservative net in quick mode). It applies to rows already loaded from the database and
  re-applies the moment Settings are saved. Nothing is deleted from the database.
* Tests: band limits, quick-flip gates (profit, confidence, listing age, reference age, liquidity, missing data), table
  filter following settings changes, settings changes while LIVE. 60 unit tests.

## 0.4.2 — 2026-10-25
* Stop now cancels adapter tasks after a 1.5 s grace (long lookup loops and back-off sleeps no longer delay shutdown); loops check the stop flag; clients are closed before stop() returns. Tests for stop during a long poll and during an in-flight request. 55 unit tests.

## 0.4.1 — 2026-10-25 (restart fix)
* Fixed: scanner stayed OFFLINE after Stop → Settings → Start (adapters reused a closed HTTP client). Adapters are rebuilt/reset on every start; stop cancels in-flight work and closes clients; UI restarts the scanner when source settings change. New unit + Windows restart tests. See docs/FIX_REPORT_RESTART.md.

## 0.4.0 — 2026-10-25 (data-speed round)
* Research: cozy.markets blocks the app (403) also from home — permission request drafted; Club FM snapshot/live formats documented; Kerning Exchange, EvilEyeFM, meso.watch audited (docs/SOURCE_AUDIT.md, docs/ACCESS_REQUESTS.md).
* New Club FM adapter (columnar snapshot decoder, sales + active asks, evidence) — **disabled until written permission is recorded** in Settings; optional bearer token for the live feed.
* cozy.markets adapter accepts an operator-issued bearer token (Settings). No bot-protection circumvention anywhere.
* 50 unit tests.

## 0.3.1 — 2026-10-25 (Windows CI round)
* Fixes found by the Windows runner: coarse-clock time budget, headless state capture, chunked maplestory.watch draining (30 s chunks, 2 s follow-up), UTF-8 report.
* CI gates: cozy 403 recorded as BLOCKED (never bypassed), scanner must stay LIVE/DEGRADED with a blocked source, no JACKPOT on stale rows; audio tests skip with an explicit notice when no device exists.
* Hebrew home-test instructions (HOME_TEST_HE.md).

## 0.3.0 — 2026-10-25 (second review round)
* cozy: no hard shop cap — safety valve keeps the watermark and resumes the walk next cycle; time-budgeted queue drain; all pages of a shop's listings are read; deals valve keeps its watermark.
* Latency distributions (min/median/p95) for source→alert and source→first sight in metrics and the verification report.
* 47 unit tests.

## 0.2.0 — 2026-10-25 (review fixes)
* cozy: persistent shop queue + pagination of shops and deals; nothing dropped when more shops change than one cycle fetches; backlog in health.
* DB: repricing inferred only across scans; same-scan multi-unit rows are independent listings.
* Pricing: stale reference statistics (>6 h) cap confidence; maplestory.watch candidate backlog exposed, cap raised to 60.
* CI: hard gates (Windows runtime audio/notification tests, live tests, packaged-EXE smoke run) before a VERIFIED artifact; `run_live_check.bat` for local verification; tools/ci_report.py.
* 42 unit tests, 6 Windows runtime tests, 3 live tests. See docs/V2_FIX_REPORT.md.

## 0.1.0 — 2026-10-25
* Source audit of six candidate sites + meowdb; two integrated (cozy.markets primary, maplestory.watch secondary).
* Asynchronous collector with incremental shop polling, deals safety net, rate limiting, back-off, health states.
* SQLite store: catalog, listings, events, sales, opportunities, notifications, source status, retention cleanup.
* Pricing engine (reference / quick / conservative / net / ROI / confidence / liquidity) with competitive capping
  and equipment-variant handling.
* Detector with JACKPOT / GOOD FLIP / UNCERTAIN levels, stale suppression, duplicate-alert prevention, repricing.
* Synthesized alarm sounds, Qt Multimedia / winsound playback, tray toasts, notification history.
* PySide6 dark dashboard: status strip, opportunity table with filters, details panel with icon, settings dialog,
  system tray, jackpot banner, copy buttons, optional Windows startup.
* 33 synthetic unit tests, live integration tests, headless measurement runner, PyInstaller spec, build script,
  GitHub Actions Windows build.
