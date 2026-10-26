# Changelog

## 0.7.4 — 2026-10-26 (Physical Scanner tab)
* New **Physical Scanner** tab: green **Start Scanning** (starts FM Watcher hidden, no console) / red **Stop Scanning**
  (stops only the FM Watcher process; maplestory.watch and the other internet sources keep running), status pill
  ONLINE / OFFLINE / ERROR with the reason, and a live table of your own scans: shop, item, buy, market value,
  estimated profit, channel, location, scan time (ages refresh automatically).
* Rows that pass your profit filters are highlighted green; they ring through the existing alert system (nothing new
  in pricing/alerts — the tab is a view over the existing FM Watcher adapter and engine).
* Scan history is kept in the database and reloaded into the tab on the next start. On quit, FM Watcher is stopped
  only if Maple Flip Hunter started it.
* Tests: start/stop isolation from internet sources, table contents and highlight, alert fired, history reload,
  error state. 100 unit tests.


## 0.7.3 — 2026-10-26 (FIX: FM Watcher stuck on STARTING · FM Watcher auto-start)
* **One program to open:** when Maple Flip Hunter starts (and 1.5 s after the window is up) it starts FM Watcher
  from the configured folder **hidden** — `python launcher.py` with CREATE_NO_WINDOW, same as start.bat but without
  the black console; output goes to `%APPDATA%\MapleFlipHunter\logs\fmwatcher_launch.log`. FM Watcher keeps
  screen-reading the game exactly as before (it never needed its own window).
* Never a second copy: an FM Watcher already running (started by you or by us) is detected (psutil) and reused.
  On Quit / window close-to-exit Maple Flip Hunter stops FM Watcher **only if it started it** (whole process tree).
* Settings → Sources: "Start FM Watcher automatically …" (default on). Status strip shows
  "FM Watcher running (started by Maple Flip Hunter, pid …)" / "started separately" / "not running — <reason>";
  a start failure shows the reason in the status bar and a tray message (folder missing, launcher.py missing,
  Python not installed → run install.bat, exited immediately → see the launch log).
* Tests: unit (hidden start, duplicate prevention, stop only ours incl. child, every error message) + Windows
  runtime (CREATE_NO_WINDOW flag, child watcher.py started, tree stopped).
* Root cause: the FM Watcher adapter read the file correctly but never called `mark_success`, so its health could
  not leave STARTING and nothing about it was logged. Reproduced with the user's real `watcher_data_v2.json`
  (21 shop entries, Kerning City, channel 35) — now a regression test (unit + Windows).
* Now: ONLINE after the first successful read (also when the file is unchanged and fresh), STALE after 10 min
  without a write; status detail shows the file, its write time and the number of new/updated rows.
* Logs: `fmwatcher: adapter created, folder=…`, `fmwatcher: poll #n read <path> (mtime …): N new/updated listings,
  newest …s ago`, periodic "unchanged" lines, and every read error (`fmwatcher poll failed: …`).
* OCR'd town names lose their first letter ("erning City Construction Site") — mapped back to the known map name;
  FM rooms ("FM3") unchanged. Rows outside the Free Market keep channel + map name, no FM room.
* maplestory.watch, pricing and alerts untouched. 95 unit tests.


## 0.7.2 — 2026-10-26 (availability tracking — without slowing alerts)
* **Alert path unchanged and still first**: sound + floating card fire the moment a deal passes the filters; no
  network request is made before them (test: the sound timestamp precedes the first verification request).
* **Background verifier** (own asyncio task + priority queue, alerted deals first, ≥ 120 s between re-checks of
  the same row, stops after 2 h): asks the listing's source whether it is still there using a *separate* rate
  budget (maplestory.watch: ≤ 1 request / 2 s, item page cached 60 s locally) so discovery polls are never delayed.
* Statuses: **ACTIVE** (last seen by a source — not a promise), **UNVERIFIED** (no current confirmation, with the
  reason), **REMOVED** (source reports closed / gone / seller repriced), **SOLD** (explicit sold-out flag only).
  Absence from a partial scan is never treated as sold. Re-appearance in a later poll → ACTIVE; a reprice marks the
  old row REMOVED. Shown as *Availability* + *Last verified* in the details panel and in the row tooltip.
* Verified REMOVED/SOLD rows leave the active table (kept in the database, table `availability`); the floating card
  updates its text ("⛔ SOLD — … Don't go.") with **no extra sound**.
* Per-source capability: maplestory.watch (soldOut/closed flags, ≤ 5 min cache), Club FM (sold/old in the latest
  snapshot, 5–9 min behind), FM Watcher (your own re-scan of that shop), cozy.markets: cannot verify (blocked) —
  rows stay UNVERIFIED with that note rather than a misleading status.
* Tests: alert-before-verify ordering, all four statuses, reprice/re-appearance, explicit-flags-only parsing,
  limitation reporting, load (500 alerts + slow verifier: polls keep their cadence, a new deal still alerts < 3 s),
  popup/table updates. 93 unit tests.


## 0.7.1 — 2026-10-26 (table: 6 columns for real-time deal spotting)
* Opportunity table columns: **Item · Buy · Quick Sale · Net Profit · Age · Location** (thousands separators,
  right-aligned numbers, Item stretches, no horizontal scrollbar at 1366+). Net profit green when positive, red when
  negative; fresh & verified rows green, UNCERTAIN orange, stale (older than *Treat listings older than*) grey; faint
  tint behind JACKPOT rows. Age (2m / 5m / 1.6h) and the colours refresh every second without rescanning.
* Sorting on every header (Net Profit high→low, Age freshest first, …); default order newest first.
* Everything else (market value, conservative sale/net, ROI, confidence, liquidity, competitors, shop name, source,
  timestamps, stats) stays in the details panel; click a row as before. Pricing, filters, alerts and DB unchanged.
* UI tests updated: 6 columns, values, colours, sorting, live age, no overflow at 900–1920 px.


## 0.7.0 — 2026-10-26 (second LIVE source: your own FM Watcher scans)
* New source **FM Watcher (your own scans)** — `adapters/fmwatcher.py`. Club FM's free scanner (which you run; it
  only screen-reads shop windows and is Club FM's official tool) keeps every listing it saw in its folder:
  `watcher_data_v2.json` (rewritten within ~30 s of each read) and `my_scans.csv` (its documented export.bat —
  "Reviewing your own data"). Maple Flip Hunter polls those files every 5 s by modification time and ingests new /
  updated rows with seller, price (unit price for bundles), quantity, channel, FM room, scan time, sold-out flag and
  hovered equip stats. No game access, no Club FM server access, no permission needed; FM Watcher's own publishing
  is untouched. Settings → Sources → **FM Watcher folder** (Browse…). Status shows `reading watcher_data_v2.json`;
  STALE after 10 min without a file update.
* Rows flow through the unchanged pipeline: evidence from maplestory.watch/Club FM/local DB → pricing → filters →
  profit-band sound → floating card; cross-source dedupe prevents a second alert when maplestory.watch later sees
  the same shop. maplestory.watch runs independently.
* Tests: store-file parsing (location/price/time/stats/bundles/sold), CSV fallback, missing folder handling,
  end-to-end own-scan → JACKPOT → sound → dedupe vs maplestory.watch. 86 unit tests.


## 0.6.5 — 2026-10-26 (FIX: automatic alerts were silent)
* **Root cause:** alerts are raised on the scanner's asyncio thread; `QSoundEffect` objects created and played from
  that thread never produce sound (Qt audio needs the GUI thread's event loop). *Test alarm* runs on the GUI thread,
  which is why it always worked. The scanner, filters and the profit-band choice were correct (the log showed
  `1 alerts`, the row showed `alerted …`).
* **Fix:** every play request is marshalled to the GUI thread through a queued Qt signal; all sound effects are
  created once on the GUI thread, parented and kept for the life of the process (never destroyed mid-play).
  `MainWindow` attaches the proxy at start-up. No filter threshold was changed.
* Logging: `ALERT_CREATED`, `AUDIO_REQUESTED` (with thread=gui/worker), `AUDIO_STARTED`, `AUDIO_ERROR`,
  `AUDIO_SUPPRESSED reason=…` (muted / audio disabled / band disabled / already alerted / notifications off).
* Settings → **🧪 Test Automatic Alert**: runs the real alert path from a background thread (profit band → sound →
  floating card) for a synthetic TEST row; nothing is written to the database or the table.
* Tests: GUI-thread marshalling, suppression reasons, simulated alert end-to-end (unit) + Windows runtime test that an
  engine ingest on a worker thread reaches `AUDIO_STARTED thread=gui`. CI runners have no sound card: physical
  playback is NOT verified there — verify at home (see README §6).


## 0.6.4 — 2026-10-26 (main window: free resize, no clipping, layout memory)
* Main window resizes freely from every edge and corner down to 900×560; long status texts (source list, coverage)
  wrap/shrink instead of forcing the window wider — this was what pushed buttons and data off-screen until the window
  was maximized. Full values stay available as tooltips.
* Draggable splitter between the table and the details panel (8 px handle, neither side can collapse; table ≥ 420 px,
  details ≥ 320 px); both adapt to the window size, the Item column takes the remaining width.
* Window size, position, maximized state and the splitter split are remembered across sessions and restored fully
  on-screen (small/large screens, Windows scaling, second monitor); saved on close, on tray-hide and on Quit.
* Filter bar wraps onto a second line (flow layout) when the window is narrow, and the title may shrink — on Windows
  fonts the old single row demanded 1,292 px (caught by the new GUI test on the Windows runner).
* GUI tests: resize to 900×560 / 1366×728 / 1920×1040 / 1000×650 without clipping, splitter drag, maximize/restore,
  close → reopen restores geometry and split, off-screen geometry pulled back. 80 unit tests.


## 0.6.3 — 2026-10-26 (UI: 4-column table, settings window that fits the screen)
* Opportunity table shows exactly **Item · Buy · Market Value · Quick Sale** (thousands separators, right-aligned
  numbers, Item column stretches, no horizontal scrollbar). The level is a subtle colour on the item name (red JACKPOT,
  green GOOD FLIP, yellow UNCERTAIN, grey stale) plus a faint tint behind JACKPOT rows; hover shows net profit, location
  and the pricing explanation. Everything else (net profit quick/conservative, ROI/discount, confidence, liquidity and
  competitors, freshness, CH/FM/shop, source, timestamps, item stats) stays in the details panel on the right.
  Sorting by any header, filters, database and alerts are unchanged; default order is newest first.
* Settings window: scrollable body with Save/Cancel pinned at the bottom, initial height ≤ 80 % of the available
  screen, resizable/draggable, size and position remembered between sessions (also after Cancel), always moved fully
  on-screen. Geometry rule unit-tested for 1366×768, 1920×1080, 125 %/150 % scaling and a second monitor.
* Tests: 79 unit tests (table columns/details/sorting, dialog scroll/buttons/geometry).


## 0.6.2 — 2026-10-26 (Club FM: ready to switch on)
* Club FM adapter: configurable rate limits (`source_clubfm_interval_s` snapshot ≥ 30 s, `source_clubfm_live_interval_s`
  live, `source_clubfm_live_backoff_s` exponential back-off 30→60→120…≤600 s on a refused/failed live feed, one
  connection), bearer token, `source_clubfm_base_url` (for the simulator only), status detail "live mode" / "snapshot mode".
* `tools/clubfm_simulator.py` + `run_clubfm_sim.bat`: local server speaking Club FM's columnar format (snap public,
  live needs `demo-token`), injects one under-priced listing every 90 s. `--headless --settings <file>` runs the EXE
  against it. CI now runs the packaged EXE against the simulator and gates on: live mode → rows ingested → alert →
  ingest→sound latency recorded (no maplefm.gg traffic).
* End-to-end unit test (real HTTP, real adapter, real engine): injected row → JACKPOT → profit-band sound; the same shop
  arriving from maplestory.watch without a channel is a duplicate (no second alert); wrong token → snapshot mode with
  doubling back-off. 72 unit tests.
* Real Club FM requests remain OFF until `source_clubfm_permission_granted` is ticked. maplestory.watch unchanged.


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
