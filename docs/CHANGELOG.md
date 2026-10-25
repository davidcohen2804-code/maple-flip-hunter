# Changelog

## 0.6.1 — 2026-10-25 (always-on-top alert card, ingest→sound latency)
* **Alert card above other windows** for every JACKPOT / GOOD FLIP alert that passed all filters: item, buy price,
  *estimated* net profit (labelled "not guaranteed", with the quick-sale price it assumes), exact location
  (CH · FM · shop), confidence, source age and the measured alert latency. Buttons: **Copy location** (one click →
  clipboard), **Mute** (stops the sound and silences alerts for 60 s), **OK** (the card stays until pressed). Cards
  stack top-right and never take keyboard focus from the game. Windows limit: a game in *exclusive full-screen*
  mode hides all other windows — the sound and tray toast still fire; windowed/borderless games show the card.
* **Ingest→sound latency** measured per alert (listing first seen locally → sound handed to the audio backend),
  shown in the status bar next to the source→alert latency.
* Tests: unit (sound fires from the scanner path, not only Test alarm; stale and unverified rows stay silent; card
  contents, copy, mute, stacking, no auto-hide) + Windows runtime (real Qt/winsound backend reached automatically
  from an engine ingest, latency < 1 s, card is top-most). Filters unchanged.


## 0.6.0 — 2026-10-25 (profit-band sounds, Club FM live feed)
* **Sound alerts by net profit.** Settings → "Sound alerts by NET PROFIT": editable bands, each with its own melodic
  sound, enable/disable, ▶ Preview, volume slider, "+ Add band". Defaults: 10,000–19,999 gentle bell; 20,000–49,999
  happy melody; ≥ 50,000 festive JACKPOT fanfare. Overlapping bands are refused at save time (and a corrupt settings
  file falls back to defaults). The band is chosen from the **net profit the pricing engine computed** (the figure in
  the table), not from the JACKPOT / GOOD FLIP label; a disabled band, or a profit outside every band, still shows the
  row and the Windows toast but plays nothing.
* **All sounds re-synthesized as pleasant bell tones** built from major-scale notes (no sirens, no sweeps, no partial
  above 2.2 kHz, peak capped at 80 %). Library: bell, melody, jackpot, chime, harp, soft. Unit test checks the
  spectrum of every generated sound. Test alarm plays the highest enabled band's sound.
* **Club FM**: `/data/live?after=` with bearer token is used when it answers; 401/403 → snapshot-only for 10 min, then
  retried. Club FM completed sales and live asks now feed the valuation of rows that came from other sources (so
  QUICK FLIPS ONLY can reach its evidence bar). Cross-source duplicates are matched even when one side lacks a channel
  (within 15 min); the first stored row is kept and its observation time advanced to the freshest sighting. Real
  requests remain OFF until `source_clubfm_permission_granted` is ticked in Settings.
* Tests: 69 unit tests (was 60) incl. settings-dialog band editor, sound spectrum, profit-band selection,
  Club FM live/snapshot switch, Club FM evidence → QUICK FLIPS gate, channel-less cross-source dedupe.


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
