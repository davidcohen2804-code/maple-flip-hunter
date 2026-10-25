# Club FM (maplefm.gg) — activation checklist

Status (v0.6.0): **technical integration complete; real requests disabled** until a genuine written permission from the
Club FM team is on record and `Settings → Sources → Club FM` is ticked.

## What is implemented

| Item | Where | Notes |
|---|---|---|
| `/data/snap` columnar decoder (v2 schema, `D.active` / `D.sales` index lists, `st` fallback) | `adapters/clubfm.py::decode_snapshot` | verified against a real snapshot captured in the browser (56,405 rows) |
| `/data/live?after=<ms>` with `Authorization: Bearer <token>` | `adapters/clubfm.py::_fetch_live` | used whenever it answers; 401/403 → snapshot-only for 10 min, then retried; no token → snapshot only |
| Rate limiting | `adapters/base.py` TokenBucket (1 req/s, burst 2) + poll interval 60 s (`source_clubfm_interval_s`, min 30 s) + `429 Retry-After` back-off | snapshot CDN cache is 30 s, so polling faster than 30 s gains nothing |
| Permission gate | `Settings.source_clubfm_permission_granted` (default **False**) | adapter reports `UNSUPPORTED — permission not recorded` and sends **nothing** while unticked |
| Token field | `Settings.source_clubfm_token`, Settings dialog | stored in `%APPDATA%\MapleFlipHunter\settings.json` |
| Detection | Club FM asks/sales → `MarketEngine.gather_evidence` for *every* source's rows; Club FM listings ingested like any source | QUICK FLIPS ONLY uses the Club FM evidence age (`T`) |
| Dedupe vs maplestory.watch / cozy | `db/database.py` (`item|seller|channel|price`, channel-tolerant, 15 min) | first stored row kept, observation time advanced to the freshest sighting — never alerted twice |
| One source failing never stops the scanner | `adapters/base.py::run` per-adapter loop | tested (`test_engine.py`) |
| Tests with synthetic data | `tests/unit/test_clubfm.py` (decoder, permission gate, live/snapshot switch, evidence → QUICK FLIPS) | all green |

## Try the whole chain at home now (no Club FM traffic)
1. Double-click `run_clubfm_sim.bat` (needs Python; or `python tools\clubfm_simulator.py`).
2. In `%APPDATA%\MapleFlipHunter\settings.json` add `"source_clubfm_base_url": "http://127.0.0.1:8765"`,
   `"source_clubfm_permission_granted": true`, `"source_clubfm_token": "demo-token"`; start the app.
3. Status bar shows `Club FM (maplefm.gg): ONLINE — live mode`; within ~2 min a JACKPOT card + sound appears for shop
   **NoobTrader**. Remove the `base_url` line (and untick Club FM) afterwards.

## What is still missing for real activation

1. **A genuine approval message from the Club FM team.** The screenshot supplied on 2026-10-25 is headed
   *"Example — Club FM API Permission"* and carries no author, date, channel or server. It reads like a template of what an
   approval could say, not a message received from Club FM. The app will **not** be switched on from it. What counts:
   a message (Discord DM / channel post / e-mail) from a Club FM admin — keep a screenshot that shows the author, the date
   and the server/channel, and paste the text into `docs/ACCESS_REQUESTS.md` under "Received".
2. **Scope check of the real approval** (do this before ticking the box): it must cover automated `/data/snap` access;
   `/data/live` only if they say so (and a token if they require one); and any rate limit they name must be entered as
   `Club FM interval` in Settings (never below what they allow).
3. **Token** for `/data/live` — only if Club FM issues one. Without it the app uses the public snapshot (≈ 5–6 min behind
   real time), which is still useful for evidence but too slow for QUICK FLIPS alerts on Club FM's own rows.
4. **Tick** `Settings → Sources → Club FM`, paste the token, press OK. The scanner restarts itself; the status bar should
   show `Club FM (maplefm.gg): ONLINE` within ~60 s and `live` or `snapshot` in its detail.
5. **Observe the first hour**: no `429` / `403` lines in `%APPDATA%\MapleFlipHunter\logs\maple_flip_hunter.log`. If
   Club FM asks to slow down, raise the interval; if they revoke, untick the box — no code change needed.

Nothing in the app bypasses authentication, Cloudflare, or rate limits; a refused request is recorded as
AUTH REQUIRED / RATE LIMITED and retried only after the back-off.
