# Data freshness and latency

## Two clocks, never confused

| Clock | Meaning | Available? |
|---|---|---|
| Seller publication time | when the seller opened / repriced the shop in game | **No source reports it** → shown as "unknown" |
| Source observation (`source_seen_at`) | when a community scanner uploaded the row | yes (cozy `at`, msw `seen`) |
| Source first-seen (`source_first_seen_at`) | first time the source saw the row | msw `firstSeen` only |
| Our first sight (`local_first_seen_at`) | when the collector received the row | yes |
| Analysis done | after evidence fetch + valuation | yes (`analysis_ms`) |
| Notification fired (`notified_at`) | alarm played / toast shown | yes |

**Source freshness** = observation → available over HTTP. **Application latency** = available → our alert.
The dashboard's "Avg source→alert latency" is `notified_at − source_seen_at`, i.e. *both* combined,
measured per alert. "Newest data age" is the age of the newest row the sources returned on the last poll.

## Measured (2026-10-25)

| Quantity | cozy.markets | maplestory.watch |
|---|---|---|
| CDN cache | 5 s edge / 15 s browser | 203 s (index) / 300 s (item) |
| Newest observation age at fetch | 5–60 s | 100–130 s |
| Our poll interval | 10 s (shops + deals) | 180 s (index), item pages on demand |
| HTTP round-trip (from Israel) | 265–450 ms | ~300 ms (287 KB) |
| Local analysis per candidate (synthetic tests) | 0–3 ms without network, one cached `/api/items/{id}` fetch (~300 ms) when evidence is cold | same |

So for cozy the expected **source→alert** time is roughly *scanner upload delay (unknown, seconds)* + *≤5 s cache* +
*≤10 s poll interval* + *~0.3–0.8 s fetch/analysis* ≈ **5–20 s** after the row reaches the site. The CI live test
and the headless runner record the actual distribution in `tests/live/last_live_measurements.json`
(`metrics.source_to_alert_avg_s`, `source_age_at_ingest_avg_s`).

## What "LIVE" means on the dashboard
* **LIVE** — at least one source is ONLINE *and* rows were received in the last 3 minutes.
* **DEGRADED** — sources respond but are STALE / RATE LIMITED, or no rows for >3 minutes.
* **OFFLINE** — scanner stopped or every source UNAVAILABLE / AUTH REQUIRED.

## Honest limitation
Both sources depend on *other players running scanners*. A shop nobody walks past is invisible to every site
and therefore to this app. Coverage is displayed as "N items seen / M catalog · K rows last 1 h" and must be read
that way. The app is near-real-time relative to the sources (seconds), not relative to the game.
