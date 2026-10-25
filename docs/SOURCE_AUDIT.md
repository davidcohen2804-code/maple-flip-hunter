# Source audit — MapleStory Classic World market data

Investigated 2026-10-25 (Asia/Jerusalem afternoon) with real HTTP requests from a browser session
and from the collector code. Every "measured" number below was observed, not assumed.
Numbers marked *(CI)* are refreshed by the Windows CI job (`tests/live`) and stored in
`tests/live/last_live_measurements.json`.

## Update 2026-10-25 evening — access status after the home test
* **cozy.markets returns HTTP 403 to this application also from the user's home PC** (the same URLs work in a browser):
  Cloudflare bot protection on non-browser clients. Status in the app: `AUTH REQUIRED`. **Not bypassed.** A permission /
  token request is drafted in docs/ACCESS_REQUESTS.md; the adapter accepts a bearer token the moment one is issued.
* **maplestory.watch works from home and from Windows CI** (secondary, 2–5 min behind).
* New sites investigated (details below): **Club FM** (adapter built, disabled until written permission), **Kerning
  Exchange** (smega feed, no room/channel), **EvilEyeFM** (no coverage), **meso.watch** (automated access disallowed).

## Summary table

| Site | Verified access | API | Listing fields | Refresh / freshness | Rate limits | Coverage | Restrictions | Status |
|---|---|---|---|---|---|---|---|---|
| **cozy.markets** | HTTP 200, public JSON (no auth) | Undocumented but stable JSON used by the site's own front-end (`/api/*`) | item id+name, category, seller, shop title, channel, FM room / map, unit price, qty, stock, sold flag, equipment stats (when hovered), source `fm` reference, sighting timestamp | `Cache-Control: max-age=15, s-maxage=5`. Newest shop scan age measured **5–60 s**; deals feed newest row age **20–60 s** | None advertised; we self-limit to 3 req/s, ~1 cycle / 10 s | 1,377 catalogued items, 24,930 shops seen (7 d), ~94 k sightings / 24 h, 289 contributors / 24 h | robots.txt `Allow: /`; no terms page; privacy page only. Community-scanner uploads (MaplePrice app) | **INTEGRATED — primary** |
| **maplestory.watch** | HTTP 200, public JSON (no auth) | Undocumented JSON (`/api/items`, `/api/item?name=`) | seller, shop title, map ("Free Market <10>"), channel, bundle, price, unit, soldOut, closed, firstSeen, seen | `/api/items` `max-age=203`, index `at` age measured **~100 s**; `/api/item` `max-age=300`; newest listing age measured **~126 s** | None advertised; we self-limit to 1.5 req/s, index every 180 s, ≤40 item pages / cycle | 1,348 items, 281 k sightings / 24 h, 64 k sales / 24 h | robots.txt uses content signals (no search/ai-train directives set); privacy page says the index is public. Browser-based scanner uploads | **INTEGRATED — secondary** (sale medians + candidate listings) |
| **freemarketclassic.com** | HTTP 200 (HTML) | None found | shop rows: item, posted price, typical price, seller, channel, FM room, "last seen" | Rows showed "48 min – 8 h" ago at audit time (slower than cozy) | n/a | 61,802 recorded prices; also live smega feed | **Terms §2 forbid "bots, scripts or programs that automate … this website"** | **UNSUPPORTED (ToS)** — their own *FM Sync* Windows app offers alerts |
| **maplemarket.fyi** | HTTP 200 (HTML), data behind paid login | None public | lowest / median / sold per item, shop + channel + room (paid) | unknown (paid) | n/a | 1,122 items on sale, 63 k listings, 6.5 k shops read / 24 h (home page KPIs) | **Paid subscription; ToS §2: don't "republish or scrape the site's data"** | **UNSUPPORTED (paid + ToS)** |
| **maplefm.gg (Club FM)** | HTTP 200 (static HTML, data files on GitHub Pages) | Data files exist but **ToS §2 requires written permission** for automated collection | sales + asks, seller, channel, room | "updates itself every minute" (per site) | n/a | community FM Watcher + Twitch stream reader | ToS: "No bots, scrapers … without asking us first. … we usually say yes" | **UNSUPPORTED pending permission** — a good future source; ask via their Feedback button / Discord |
| **maplefm.gg (Club FM)** — detail | HTTP 200 JSON `https://api.maplefm.gg/data/snap` (public, `max-age=30`); `/data/live?after=` gated (401/403) | columnar snapshot v2: 56,405 rows, cols `i item, s seller, p price, q qty, u unit, r room ("FM2"), c channel, t seconds-before-T, who scanner, st up/old, sold, chat, xj job`; `D.active` / `D.sales` index lists | measured: newest row (`T`) **326 s** older than the snapshot `at`; live feed "updates every minute" (gated) | self-limit 1 req/min | 1,366 items, 1,282 "spot" rows, scanner ranks | **Terms §2: permission required** → adapter `adapters/clubfm.py` built and tested, **disabled** until permission is recorded in Settings | PENDING PERMISSION |
| **kerning.gg (Kerning Exchange)** | HTTP 200 HTML; no JSON found | none documented | smega shouts: item, price/offer, seller, some stats text, "just now / 1m ago" timestamps; item pages with 7-day medians | seconds (chat feed) | n/a | all smegas | no terms page; Discord only | NOT INTEGRATED (no channel / FM room → cannot direct to a shop; request drafted) |
| **evileyefm.com** | HTTP 200; page reads a third-party database (Supabase) with a key embedded in its JS | not an authorized API | listings with seller, channel, free_market room, equip stats, captured_at | unknown | n/a | **12 rows in the last 7 days** (Rooms page) | none published | NOT INTEGRATED (no authorized access, no coverage) |
| **meso.watch** | fetch refused (robots disallow) | unknown | unknown | unknown | n/a | unknown | disallows automated access | UNSUPPORTED |
| **beta.meso.watch** | Fetch refused: site disallows automated access | unknown | unknown | unknown | n/a | unknown | robots disallow | **UNSUPPORTED (blocked)** |
| meowdb.com (extra) | HTTP 200 (HTML) | none seen | player-reported 14-day median prices | manual reports | n/a | n/a | not investigated for ToS; cozy already exposes its figure as `meow` | Reference only (used indirectly via cozy `meow` field) |

No WebSocket / push feeds were found on any site; every integrated feed is polled.

## cozy.markets — verified endpoints (all `GET`, JSON)

| Endpoint | Observed response | Used for |
|---|---|---|
| `/api/shops?page=1&size=100&sort=last_at&dir=desc` | 200, `rows[]` with `shop, seller, title, channel, fm_room, map_name, scans, listings, sold, first_at, last_at, top[]`; `total` 24,930. Newest `last_at` age **5 s / 30 s** for the two top rows | incremental "which shops changed" signal (fastest) |
| `/api/shops/{shop}/listings?page=1&size=100` | 200 in **~450 ms**, `rows[]`: `id, name, at, qty, stock, channel, fm_room, map_name, price, unit_price, confirmed, sold, fm, vs_fm, stats` | every row of a changed shop (whole market, not only deals) |
| `/api/deals?page=1&size=100&kind=under-fm&seen=1h&sort=at&dir=desc` | 200 in **265–275 ms**, 100 rows max per page, `total` 363–402 in the last hour, newest row age **20–60 s**; also `kind=flips`, `kind=npc`, `band`, `ch`, `cat`, `q` | safety net; includes `fm` reference + `fm_basis` (sold/day/week) + `stats` |
| `/api/items?page=1&size=100` | 200, 1,377 items / 14 pages: `id, name, npc, fm, fm_basis, meow, cat, sub, job, count, sold, last_at, median, low` | item catalog (categories, subtypes) |
| `/api/items/{id}` | 200, `info{type, subtype, levelReq, reqJob, stats, iconKey}`, `stats{count, low, p25, median, high, last, sold, last_sold, sold_median}`, `chart[]` recent sightings with `sold` flag, shop, channel, room | reference stats + comparables |
| `/api/items/{id}/sightings?page=1&size=100&since=<unix>&stats=watk:56,slots:7` | 200, rows filtered to the same stat variant; `since` honoured (3 rows in last 10 min) | equipment comparables per variant |
| `/api/overview` | 200, KPIs: `sightings_24h 93,782`, `sales_24h 24,897`, `items_tracked 1,377`, `shops 27,485`, `scans_24h 13,630`, `contributors_24h 289` | coverage display |
| `/api/icons/{iconKey}.png?v=3` | PNG | item image in the details panel |

Response headers: `cache-control: max-age=15, public, s-maxage=5`, Cloudflare in front, `via: 1.1 Caddy`.
Polling faster than 5 s cannot return newer data; the app polls shops+deals every 10 s (configurable ≥5 s).

### Measured data age (cozy)
* `GET /api/shops` newest `last_at`: **5 s** and **30 s** (two samples, 13:5x UTC).
* `GET /api/deals … seen=1h&sort=at`: newest rows **20, 23, 23, 23, 57 s** old; second sample **38, 57, 60, 60, 60, 94 s**.
* Seller-publication time is **not** available from any source; only the scanner's observation time.

## maplestory.watch — verified endpoints

| Endpoint | Observed | Used for |
|---|---|---|
| `/api/items` | 200, 287 KB, `at` (index build time, age **104 s** at fetch), `items[]` 1,348 with `typical, range, dayAgo, samples, prices, lowest, listed, sold, units, volume, seen24h, sales24h, seen`; `cache-control: public, max-age=203` | whole-market index, sale medians (14-day), candidate detection (`lowest` ≪ `typical`) |
| `/api/item?name=AP%20Reset%20Scroll` | 200, `listings[]` with `seller, title, map, channel, bundle, price, unit, soldOut, closed, firstSeen, seen, pos`; `max-age=300` | listing rows + competitors for candidates |
| `/api/version` | 200 | — |

Newest listing `seen` in the index was **126 s** old at fetch. This source is therefore 2–5 minutes behind
cozy on average and is used for (a) completed-sale medians and (b) extra candidate rows, never as the
speed path.

## Access rules observed by the collector
* Honest `User-Agent: MapleFlipHunter/0.1.0 (+personal desktop tool; …)`.
* Token-bucket rate limits per source; HTTP 429 → `RATE LIMITED` state and back-off for `Retry-After`.
* 401/403 → `AUTH REQUIRED` (never retried with credentials); 5xx / network errors → exponential back-off (1 s → 20 s).
* No authentication bypass, no anti-bot evasion, no game-client interaction of any kind.

## Not integrated — why, exactly
* **freemarketclassic.com**: Terms of use (draft v0.1, 2026) §2 prohibit "bots, scripts or programs that automate the game *or this website*". The site exposes no documented API, so any integration would be a scraper → excluded.
* **maplemarket.fyi**: market data requires a paid Stripe subscription and the ToS forbid republishing/scraping → excluded.
* **maplefm.gg**: ToS §2 requires written permission for automated collection of the price tables/data files; the authors state they usually grant it. **Action item:** ask via the site's Feedback button or Discord; an adapter can then be added (their data files are static JSON on GitHub Pages, "updated every minute").
* **beta.meso.watch**: automated fetch refused; nothing could be verified.
