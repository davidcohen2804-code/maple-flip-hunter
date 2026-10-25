# Pricing engine

Implementation: `maple_flip_hunter/pricing/engine.py`, evidence assembly in `collector/engine.py::gather_evidence`.

## Inputs (`MarketEvidence`)
* `sold_median`, `sold_count`, `sold_recent_24h` — completed sales. cozy: `stats.sold_median`/`stats.sold`
  (rows seen greyed-out / stock dropped); maplestory.watch: `typical`/`samples` (14-day sold median). For **equipment with
  known stats** the item-wide median is discarded and recomputed from same-variant sightings only.
* `ask_median`, `ask_p25`, `ask_low`, `ask_count` — asking prices (never labelled as sales).
* `comps[]` — recent sightings with a `sold` flag; `active_asks[]` — unsold competing rows in the comp window
  (24 h), merged from the primary source, the secondary source and our own database, excluding the candidate row itself.
* `secondary_reference` — another site's reference (meowdb via cozy, or maplestory.watch typical).
* `stats_matched` — `None` non-equipment, `True` same-variant comps, `False` equipment with unknown stats.

## Reference Market Value
1. `sold_median` when `sold_count ≥ 5`;
2. else median of ≥3 observed sales (flagged *thin*);
3. else 90 % of the asking median when ≥5 asks (flagged *no sale data*);
4. else 90 % of the secondary reference (flagged);
5. else **no valuation** → UNCERTAIN at best.

**Competitive cap:** if ≥5 live competitors exist and the median of the 5 cheapest is below 75 % of the reference,
the reference becomes that median (the market has moved; the site average is not trusted).

## Quick Sale Price
`min(reference × 0.95, cheapest live competitor × 0.97)`. With ten competitors at 35,000 and a 100,000 "reference",
quick sale is 33,950 — never 100,000 (spec §13 example; unit test `test_5_…`).

## Conservative Sale Price
`quick × 0.90`, further capped by 95 % of the third-cheapest competitor and by the asking-price 25th percentile.

## Net profit
`net = quick × (1 − fee) − buy`, `fee` default 3 % (configurable, Settings → Selling fee). A conservative net is
also shown. ROI = net / buy. Discount = 1 − buy / reference.

## Liquidity (from sale counts only, never invented)
HIGH: ≥5 sales in 24 h or ≥50 recorded; MEDIUM: ≥1 in 24 h or ≥10 recorded; LOW: some sales; UNKNOWN: none.

## Confidence (0–1, explainable)
| Component | Max | Rule |
|---|---|---|
| sale sample size | 0.35 | linear to 20 sales |
| comparables depth | 0.20 | linear to 10 comps |
| evidence freshness | 0.15 | <10 min 0.15, <1 h 0.10, else 0.03 |
| variant match | 0.15 | non-equip or same-stat comps |
| reference quality | 0.15 | full when reference is a real sale median; 0.07 otherwise; 0 when price <3 % of reference (likely misread) |
Penalties: stale observation ×0.5; **dropped-digit guard**: if the same shop lists the item at ~10× the price (a real pattern seen in cozy data: 74,999 / 64,999 / 7,499) confidence is capped at 0.40, and if price×10 equals the reference ±10 % confidence ×0.7; shop name not read by the scanner → capped at 0.55 (GOOD at most); **equipment with unknown stats capped at 0.40** (→ shown as UNCERTAIN, never alarmed).
Labels: HIGH ≥0.70, MEDIUM ≥0.45, LOW.

## Classification (`detection/detector.py`)
* Budget gate: buy ≤ max purchase price (75,000 default); profit gate: net ≥ minimum (15,000 default).
* **JACKPOT**: net ≥ 50,000 *or* (ROI ≥ 100 % and net ≥ 25,000), confidence ≥ 0.60, liquidity HIGH/MEDIUM.
* **GOOD FLIP**: gates pass, confidence ≥ 0.45, liquidity HIGH/MEDIUM.
* **UNCERTAIN**: gates pass but confidence/liquidity insufficient, or ≥30 % under reference with no profitable valuation,
  or far under the source reference with no valuation at all. Displayed, never alarmed.
* Stale rows (observation older than 15 min default) are classified but never alerted.

## Duplicate-alert prevention
A listing key is `source|item variant|seller|channel|price`. The same row re-scanned at the same price is a
"seen" event (no re-evaluation). A **price change** by the same seller creates a new key → evaluated again → may
alert. Alerts are recorded in SQLite, so a restart does not re-alert old rows. The same physical row reported by a
second source (`item|seller|channel|price` match) is stored but counted as a duplicate and never alerted twice.
