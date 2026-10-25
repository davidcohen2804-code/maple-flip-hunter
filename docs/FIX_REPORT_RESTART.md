# Fix report — scanner stays OFFLINE after Stop → Settings → Start ("client has been closed")

Reported from the home PC, 2026-10-25 20:54: after changing settings and stopping/starting the scanner the app
stayed OFFLINE and the log repeated `Cannot send a request, as the client has been closed` for both sources.

## Root cause
`MarketEngine.stop()` ended the engine thread, whose shutdown closed every adapter's `httpx.AsyncClient`
(`adapter.aclose()`). `MarketEngine.start()` reused the **same adapter objects**, so every poll ran on a closed
client and failed immediately. A second, latent race: `stop()` looked up the event loop that the thread creates,
so a stop issued right after a start could miss the loop and leave adapters sleeping for a full interval.

## Changes (v0.4.1)
1. **Adapter lifecycle** (`adapters/base.py`): `reset()` rebuilds the HTTP client when it was closed, resets the stop
   flag, rate bucket and health; `aclose()` marks the adapter `closed`; `fetch_json()` refuses to use a closed client
   with a clear error instead of httpx's. Operator tokens survive a rebuild (`set_header`).
2. **Engine start** (`collector/engine.py`): adapters built from Settings are **rebuilt from the current Settings**
   on every start (new tokens / enabled sources / Club FM permission take effect); injected adapters are `reset()`.
   The event loop is created in `start()` so `stop()` can always signal it.
3. **Engine stop**: signals every adapter, cancels in-flight tasks, closes clients, joins the thread (10 s guard,
   logged if exceeded); idempotent. Housekeeping exits within 0.1 s.
4. **UI**: changing any `source_*` setting while the scanner runs now restarts it automatically.

## Tests
* Unit (new): start→stop→start rebuilds the closed client and keeps polling (no "closed" error, ONLINE);
  settings change between runs rebuilds adapters from settings; stop is idempotent and a 403-blocked source does not
  stop the others (state LIVE with one AUTH REQUIRED source). Unit suite: **53 passed**.
* Windows (new, real sources, runs on CI): `test_start_stop_settings_start_recovers_live_with_real_sources` —
  start 8 s → stop (all clients closed) → change settings → start → maplestory.watch must return ONLINE/STALE within
  150 s and the scanner to LIVE/DEGRADED, with no "closed" error on any adapter; cozy may stay AUTH REQUIRED (403,
  not bypassed).

## CI run #13 finding (v0.4.1) and fix (v0.4.2)
The new Windows test failed: `engine thread did not finish within 10s` → adapters not closed. Cause (not a timeout
problem): `stop()` only *flagged* the adapters; a poll already inside a long loop — maplestory.watch fetching up to
40 candidate items at 1.5 req/s (~30 s), cozy draining its shop queue, or an HTTP back-off sleep — kept running
until it finished, so the engine thread outlived the 10 s join and `aclose()` had not run yet.
Fix: `stop()` now runs `_request_shutdown` on the engine loop — flags every adapter **and** cancels all adapter
tasks 1.5 s later (grace for a request about to return); the long loops in cozy / maplestory.watch check the stop
flag between requests; `_main`'s `finally` awaits the cancelled tasks and closes every client before the thread
ends, so `stop()` returns only after all clients are closed. No timeout was raised.
New unit tests: stop interrupts a 60 s poll in < 5 s; stop during an in-flight HTTP request (server that never
answers) returns in < 5 s with the client closed, thread finished, and the next start uses a fresh client.

## Verification status
**VERIFIED on Windows CI run #14** (https://github.com/davidcohen2804-code/maple-flip-hunter/actions/runs/40226469846):
unit 55/55, Windows runtime incl. `test_start_stop_settings_start_recovers_live_with_real_sources` passed (start → stop with all
clients closed → settings change → start → maplestory.watch ONLINE, scanner LIVE/DEGRADED, no 'closed' errors; cozy AUTH REQUIRED, not bypassed),
live tests passed, packaged EXE 90 s headless run passed. Audio playback still NOT verified on the runner (no device) — verified by the user at home (Test Alarm).
