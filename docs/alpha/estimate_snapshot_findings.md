# P0.3 — Finnhub EPS/Revenue Estimate As-Of Probe

**Status:** Completed (2026-06-03)

**Supersedes:** v6 assumption that Finnhub Estimates-1 endpoints expose point-in-time EPS/revenue consensus history suitable for `e_eps_revision_90d` / `e_rev_revision_90d` without local snapshot capture.

**Related specs:** [multibagger_discovery_engine_v7_cursor_composer_spec.md](../multibagger_discovery_engine_v7_cursor_composer_spec.md), [multibagger_discovery_engine_v7_architecture_design.md](../multibagger_discovery_engine_v7_architecture_design.md)

---

## Question

Can we compute same-period estimate revisions such as:

```text
MU 2026-Q3 EPS consensus as of 90 days ago
vs.
MU 2026-Q3 EPS consensus today
```

directly from Finnhub `/stock/eps-estimate` and `/stock/revenue-estimate`?

## Answer

**No.** Those endpoints return **latest/current consensus per fiscal period**, not historical as-of consensus snapshots. Point-in-time revision velocity requires **local snapshot infrastructure** (revised P1.5), not a vendor backfill from these endpoints.

---

## Probe setup

### Tickers

`MU`, `AVGO`, `STX`, `SNDK`, `VRT`

### Endpoints

| Endpoint | Purpose |
|----------|---------|
| `/stock/eps-estimate` | Quarterly EPS consensus |
| `/stock/revenue-estimate` | Quarterly revenue consensus |

### Client path

`FinnhubClient.get_estimates(ticker, as_of=None, freq="quarterly")` in `backend/src/tyche/market_data/finnhub.py`.

Implementation calls `FinnhubClient._safe_get(path, params)` twice (EPS, then revenue).

### Params actually sent to Finnhub

```text
symbol
freq
```

The wrapper `as_of` argument is **not** sent to Finnhub. It is used only locally as ingest-time `snapshot_date` stamped into `EstimatesStore` rows.

### Optional params tested (no effect)

Diagnostic GETs for `MU` also tried:

```text
from
to
asOf
asOfDate
date
```

These did **not** change response shape or row count for EPS or revenue estimates.

---

## Response shape

### Top-level keys (both endpoints)

```text
data
freq
symbol
```

### EPS row fields (representative)

```text
epsAvg
epsHigh
epsLow
numberAnalysts
period
quarter
year
```

### Revenue row fields (representative)

```text
revenueAvg
revenueHigh
revenueLow
numberAnalysts
period
quarter
year
```

### Row-level as-of fields checked (none present)

```text
snapshot_date
asOfDate
as_of
estimateDate
estimate_date
updatedAt
updated
lastUpdated
last_updated
revisionDate
revision_date
```

**Inferred API shape:** `one_row_per_fiscal_period_no_asof_in_api_rows`

There were **no duplicate fiscal periods** representing multiple historical as-of consensus snapshots.

---

## Period counts observed

| Ticker | EPS rows / unique periods | Revenue rows / unique periods |
|--------|---------------------------|-------------------------------|
| MU     | 42 / 42                   | 42 / 42                       |
| AVGO   | 42 / 42                   | 42 / 42                       |
| STX    | 42 / 42                   | 42 / 42                       |
| SNDK   | 7 / 7                     | 7 / 7                         |
| VRT    | 28 / 28                   | 28 / 28                       |

---

## Why `e_eps_revision_90d` and `e_rev_revision_90d` are empty

These columns are **not** primarily empty because of:

- a simple feature-assignment bug, or
- an estimate-ramp unit mismatch (v6 P1.5-B).

They are empty because the repo lacks **dense local point-in-time snapshots** of the same `(ticker, metric, fiscal period)` at two calendar dates separated by the revision horizon.

The repo has **deep estimate rows by fiscal period** (many forward quarters in one API response), but **not** multiple as-of snapshots per period unless the endpoint was fetched and stored on prior calendar dates.

Finnhub does not supply that history through the probed endpoints.

---

## Implementation implications

| Action | Decision |
|--------|----------|
| v6 P1.5-B estimate-ramp unit fix (`(-5.0, 10.0)` on `e_eps_revision_90d`) | **Do not implement** until revisions populate from local snapshots |
| Backfill historical EPS/revenue revision velocity from Finnhub EPS/revenue endpoints | **Do not attempt** |
| Add revision-velocity columns to `demand_feature_columns()` / XGBoost training | **Blocked** until meaningful historical non-null coverage |
| Revised P1.5 | **Estimate snapshot infrastructure first** (see v7 spec) |

### Same-period revision (target semantics)

Compare the **same fiscal period** across local snapshot dates, for example:

```text
MU period 2026-09-30 EPS estimate today
vs.
MU period 2026-09-30 EPS estimate as of 7 / 14 / 30 / 90 days ago
```

**Do not** compare today’s front quarter to the front quarter from 90 days ago if the fiscal period label rolled.

### Active-forward revision (separate naming)

If implemented, use explicit names such as:

```text
e_eps_revision_active_forward_30d
e_rev_revision_active_forward_30d
```

These must **not** silently replace same-period revision columns.

### Near-term usage

- Revision velocity: live/rule-based D-EST when populated
- ML training: unchanged until local history accumulates and quantiles are inspected
- Ramp calibration (`_est_quality`): only after real revision distributions exist

---

## Revised P1.5 summary (forward work)

1. On every scheduled Finnhub estimate ingest, persist the full EPS/revenue response as a point-in-time snapshot.
2. Key rows by at least: `ticker`, `vendor`, `metric`, `fiscal period`, `year`, `quarter`, `snapshot_date`, `ingested_at`.
3. Preserve: estimate average, high, low, `numberAnalysts`.
4. Do not overwrite prior snapshots for the same ticker + metric + fiscal period; append or upsert on the full key including `snapshot_date`.
5. Compute same-period revisions only when a prior local snapshot exists for the same ticker + metric + fiscal period.
6. Horizons: 7d / 14d / 30d / 90d for EPS and revenue (same-period naming per v7 spec).
7. After enough history: inspect distributions, then calibrate ramp bounds — do not assume percent-point vs fractional units.

---

## Next step

Proceed with **v7 P1 core tasks** (config flags, rare-class weighting, percentile discovery signals, guidance-tail preservation, demand-adjusted extension, missingness indicators, purged walk-forward). Treat **revised P1.5** as estimate snapshot infrastructure, not an estimate-ramp unit fix.
