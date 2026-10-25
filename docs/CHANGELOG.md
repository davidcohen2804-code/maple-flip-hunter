# Changelog

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
