# Multi-Bagger Discovery Engine v7 - Architecture and Design

## 1. Executive decision

Do not pause the multi-bagger engine, but do not move blindly into estimate-revision implementation either.

The correct path is:

1. Treat the Finnhub probe as **P0.3 evidence**.
2. Update the architecture to include **local point-in-time estimate snapshot infrastructure**.
3. Proceed to P1 core work: config flags, class weighting, percentile discovery signals, guidance-tail preservation, and demand-adjusted extension.
4. Defer the estimate-ramp unit fix and ML training usage of estimate-revision velocity until local snapshots exist and non-null revision distributions can be inspected.

The key result: Finnhub's EPS/revenue estimate APIs return one current consensus row per fiscal period. They do not return historical as-of consensus snapshots. Therefore the system must create its own point-in-time estimate history going forward.

## 2. What P0.3 proved

**Status:** Completed (2026-06-03). Full probe record: [docs/alpha/estimate_snapshot_findings.md](alpha/estimate_snapshot_findings.md).

The Finnhub estimate API probe confirmed the following for MU, AVGO, STX, SNDK, and VRT:

- `/stock/eps-estimate` and `/stock/revenue-estimate` return top-level keys: `data`, `freq`, and `symbol`.
- Each row represents a fiscal period with fields such as `period`, `quarter`, `year`, `epsAvg` or `revenueAvg`, high/low estimate, and analyst count.
- Rows contain no `snapshot_date`, `asOfDate`, `estimateDate`, `updatedAt`, `lastUpdated`, or `revisionDate`.
- Each fiscal period appears once in the API response. There are no multiple rows for the same period with different as-of dates.
- Adding diagnostic params such as `from`, `to`, `asOf`, `asOfDate`, or `date` did not change the row count or response shape.
- The local wrapper sends only `symbol` and `freq`; its `as_of` argument is ingest-time metadata, not a vendor as-of query.

Conclusion: Finnhub is useful for current consensus by fiscal period, but not for historical point-in-time EPS/revenue consensus revision history.

## 3. Why this matters

A true estimate revision feature asks:

> For the same target fiscal period, how did analyst consensus change between two calendar dates?

Example:

```text
MU FY2026 Q4 EPS estimate as of 2026-03-01: 18.00
MU FY2026 Q4 EPS estimate as of 2026-06-01: 23.72
Revision: +31.8%
```

This requires storing the consensus curve at multiple calendar as-of dates. Finnhub does not provide historical as-of curves through the probed endpoints. The only available as-of history is the history created by your own ingestion runs.

Therefore, a 90-day revision will remain null until the same fiscal-period estimate has been captured at least once near today and once near 90 days earlier.

## 4. D-EST v7 design

D-EST should now be split into two tracks.

### 4.1 Static estimate features - available now

These can continue to be used in ML and live scoring:

- current EPS estimates by fiscal period
- current revenue estimates by fiscal period
- forward revenue/EPS growth features
- analyst count
- recommendation score and recommendation trend
- price target upside
- EPS surprise history

### 4.2 Point-in-time revision velocity - forward-captured

These are not immediately backtestable unless a true historical point-in-time estimate dataset is acquired.

Use them as live/rule-based signals after local snapshots accumulate:

- `e_eps_revision_7d`
- `e_eps_revision_14d`
- `e_eps_revision_30d`
- `e_eps_revision_90d`
- `e_rev_revision_7d`
- `e_rev_revision_14d`
- `e_rev_revision_30d`
- `e_rev_revision_90d`
- `e_eps_revision_num_changes_30d`
- `e_rev_revision_num_changes_30d`
- `e_eps_revision_days_since_change`
- `e_rev_revision_days_since_change`
- `e_eps_analyst_count_delta_30d`
- `e_rev_analyst_count_delta_30d`
- `e_eps_dispersion_change_30d`
- `e_rev_dispersion_change_30d`

## 5. Estimate snapshot store design

Create or extend a store that preserves every consensus curve snapshot.

Recommended logical schema:

| Column | Meaning |
|---|---|
| `ticker` | Universe ticker |
| `vendor_symbol` | Symbol used for Finnhub call |
| `vendor` | `finnhub` |
| `metric` | `eps` or `revenue` |
| `snapshot_date` | Local business date when API was fetched |
| `ingested_at` | Exact ingestion timestamp |
| `freq` | `quarterly` or `annual` |
| `period` | Fiscal period end date from vendor |
| `fiscal_year` | Vendor year |
| `fiscal_quarter` | Vendor quarter |
| `estimate_avg` | EPS/revenue consensus average |
| `estimate_high` | High estimate |
| `estimate_low` | Low estimate |
| `number_analysts` | Analyst count |
| `raw_payload_hash` | Hash for detecting changed payloads |
| `source_endpoint` | `/stock/eps-estimate` or `/stock/revenue-estimate` |

Primary identity:

```text
ticker + vendor + metric + freq + period + snapshot_date
```

Do not overwrite prior snapshots. Upsert only for the same ticker/vendor/metric/freq/period/snapshot_date.

## 6. Revision computation design

Revision features must compare the same ticker, same metric, same fiscal period, and two different snapshot dates.

Correct:

```text
MU EPS estimate for period 2026-09-30 as of today
vs.
MU EPS estimate for period 2026-09-30 as of 30 days ago
```

Incorrect:

```text
Today's front quarter estimate
vs.
30-days-ago front quarter estimate
```

The second form can compare different fiscal periods and is contaminated by period roll.

For each ticker/metric/period/snapshot_date:

```text
prior_snapshot = nearest snapshot <= snapshot_date - horizon_days
if prior_snapshot exists for the same ticker + metric + period:
    revision = (estimate_avg_now - estimate_avg_prior) / abs(estimate_avg_prior)
else:
    revision = null
```

For EPS, handle negative and near-zero prior values carefully. Recommended:

```text
if abs(prior_eps) < eps_floor:
    mark revision invalid or use absolute_delta feature instead
else:
    pct_revision = (now - prior) / abs(prior)
```

Also store absolute delta:

```text
e_eps_revision_abs_30d = eps_now - eps_prior
```

For revenue, percent revision is usually more stable.

## 7. Same-period vs active-forward-period features

Keep these separate.

### Same-period revision

This is the cleanest feature and should be preferred for ML training once enough history exists.

Example:

```text
e_eps_revision_30d_same_period
```

### Active-forward revision

This can be useful for live scoring but must be explicitly named because it can roll between fiscal periods.

Example:

```text
e_eps_revision_active_forward_30d
```

Do not silently replace same-period revision with active-forward revision.

## 8. Live scoring use

Once snapshots accumulate, use estimate revision velocity as a live D-EST confirmation signal.

Example rule:

```text
if 30d same-period revenue revision > +3%
and analyst count is stable or rising
and catalyst/guidance is positive
then D-EST demand acceleration is positive
```

For extended stocks, this feeds demand-adjusted extension:

```text
Do not penalize extension.
Penalize price that has outrun validated demand.
```

Estimate revision velocity is one of the strongest forms of validated demand acceleration.

## 9. Training use

Do not add new revision velocity columns to XGBoost training until one of the following is true:

1. You have accumulated enough local point-in-time snapshot history; or
2. You acquire a historical point-in-time estimates dataset from a vendor such as LSEG I/B/E/S, S&P Capital IQ/Visible Alpha, Bloomberg, Estimize/ExtractAlpha, or Intrinio/Zacks if verified.

Until then, including these columns in training will either create all-null features or train/serve mismatch.

## 10. P0/P1 roadmap after this finding

### P0.3 - Finnhub estimate as-of probe and snapshot-cadence diagnosis

Status: effectively complete from the probe.

Acceptance:

- Raw API response shape documented.
- No vendor as-of fields found.
- Optional date/as-of params confirmed ineffective.
- Root cause documented: no vendor point-in-time estimate history through these endpoints.

### P1 core - proceed

Proceed with:

1. config flags
2. class weighting
3. percentile discovery signals
4. guidance-tail preservation
5. demand-adjusted extension
6. missingness/train-serve parity
7. purged walk-forward validation

### P1.5 - revise scope

Do not implement estimate ramp-unit fix yet.

Replace old P1.5 with:

1. P1.5-A: estimate snapshot infrastructure
2. P1.5-B: revision feature computation after enough snapshots exist
3. P1.5-C: live D-EST scoring use
4. P1.5-D: ML training inclusion only after historical non-null coverage exists
5. P1.5-E: ramp-unit calibration after real values and quantiles are observed

## 11. Cursor implementation guidance

Cursor should not start by editing AlphaScoreEngine.

First implementation commit:

```text
feat(estimates): preserve point-in-time estimate snapshots
```

Second implementation commit:

```text
audit(estimates): report snapshot cadence and revision availability
```

Third implementation commit, only after enough snapshots exist:

```text
feat(estimates): compute same-period estimate revision features
```

## 12. Final principle

Finnhub gives the current consensus curve. Tyche must build the historical as-of consensus curve by saving each fetch over time.

This is not a blocker for the multi-bagger engine. It is a data infrastructure phase that will become a powerful live signal and a future ML feature.
