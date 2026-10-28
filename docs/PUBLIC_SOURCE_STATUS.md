# Public-page scanning in 0.15.2

The default configuration now reads the public Free Market Classic `/fm?hl=en` HTML table once per minute.
It does not use login cookies, an API key, a hidden browser, spoofed browser headers, or a challenge bypass.
HTTP 401/403 stops data ingestion; HTTP 429 backs off. A changed table layout fails closed.

Live check on 2026-10-28: 196 usable shop rows were parsed, including item, unit price, seller, channel and
FM location. The newest displayed observation was `1 h ago`, treated conservatively as up to two hours old.
The source was therefore STALE and produced zero alerts. Downloading the page does not refresh a shop's
observation time. An unknown timestamp or missing seller is skipped.

Coverage is **only the shop rows rendered on that public page**, not every shop or every new listing.
The table's 14-day sold median and sample count may support valuation, but aggregate age and 24-hour sales
are not supplied and are not invented. Equipment without its exact stats remains unverified.
Fresh observations can be evaluated again after stale observations; alert history prevents duplicates.

Settings → Sources contains an independent enable switch and a polling interval with a 60-second minimum.
The existing licensed Friends Edition and its licence server are unchanged.

The Windows GitHub Actions runner returned HTTP 403 for the public page on the same day, although the local
check received 196 rows. Source availability therefore depends on the network. 0.15.2 is a preview, not a
claim of working live collection on every PC. The release includes the build network's actual source report;
unit and EXE startup checks remain mandatory. Source failures are reported rather than bypassed.

## Remaining requested sources

- MapleStory.watch, Club FM and Cozy: the public landing pages load, but contain no populated shop rows in
  the initial HTML. Their existing data adapters still have their existing access checks. Loading a home
  page is not proof that a market feed works.
- MeowDB: its public market page renders some aggregate prices and volumes. That is not a verified feed
  of individual shop listings with seller, location and observation time. No new adapter is claimed.
- HeneFM: the public table currently marks its rows expired and has no usable prices in the checked rows.
  No new adapter is claimed.

Run `python tools/check_public_market.py` for a real, read-only ingestion check. Its output explicitly
reports freshness, parsed row count, alert count and the coverage limit. The Windows release workflow runs
this diagnostic as well as the complete unit suite and the packaged executable licence/startup check.
