# Multi-Bagger Discovery Engine v7 - Cursor Composer Implementation Spec

This file supersedes the v6 Cursor Composer spec only where it conflicts. Keep the v6 guardrails: conservative Directional Alpha behavior must remain unchanged unless discovery flags are enabled.

## Immediate decision

Do not stop the project.

Do not proceed with the old P1.5 estimate-ramp unit fix.

P0.3 is recorded (see [estimate_snapshot_findings.md](alpha/estimate_snapshot_findings.md)). Proceed with P1 core tasks; revised P1.5 remains estimate snapshot infrastructure, not estimate-ramp unit fix.

## P0.3 - Finnhub EPS/Revenue Estimate As-Of Probe

### Status

**Completed** (2026-06-03). Full probe record: [docs/alpha/estimate_snapshot_findings.md](alpha/estimate_snapshot_findings.md).

### Result

Finnhub `/stock/eps-estimate` and `/stock/revenue-estimate` return **one current consensus row per fiscal period**. They do **not** provide historical point-in-time consensus snapshots.

| Finding | Detail |
|---------|--------|
| Client | `FinnhubClient.get_estimates()` → `_safe_get` for EPS + revenue |
| Params sent | `symbol`, `freq` only |
| `as_of` wrapper arg | Local ingest `snapshot_date` only — **not** sent to Finnhub |
| Params tested (no effect) | `from`, `to`, `asOf`, `asOfDate`, `date` |
| Row shape | `data` / `freq` / `symbol`; fields include `period`, `quarter`, `year`, `epsAvg` or `revenueAvg`, high/low, `numberAnalysts` |
| Missing row fields | No `snapshot_date`, `asOfDate`, `estimateDate`, `updatedAt`, `revisionDate`, etc. |
| Period structure | One row per fiscal period (e.g. MU/AVGO/STX: 42 unique periods; no duplicate periods for different as-of dates) |
| Tickers probed | MU, AVGO, STX, SNDK, VRT |

### Conclusion (rejects v6 estimate-ramp hypothesis)

`e_eps_revision_90d` and `e_rev_revision_90d` are empty primarily because the repo lacks **local point-in-time snapshots** of the same `(ticker, metric, fiscal period)` at prior calendar dates — not because of a ramp unit bug alone.

**Do not:**

- implement v6 P1.5-B estimate-ramp unit fix yet
- backfill historical revision velocity from Finnhub EPS/revenue endpoints
- add revision-velocity columns to ML training until local snapshot history is meaningful

**Do:** proceed to P1 core; scope revised P1.5 as **estimate snapshot infrastructure** (see below).

### Acceptance (met)

- Read-only documentation in `docs/alpha/estimate_snapshot_findings.md`
- No scoring, model, or `AlphaScoreEngine` changes

Suggested commit (if not yet committed):

```text
docs: record P0.3 Finnhub estimate snapshot probe (completed)
```

## P1 core — status

**Completed** (2026-06-03; validation accepted with caveats 2026-06-06). **Superseded by** [v8 spec](multibagger_discovery_engine_v8_cursor_composer_spec.md). Full note: [docs/alpha/p1_completion_note.md](alpha/p1_completion_note.md).

| Task | Delivered |
|------|-----------|
| P1.1 | Discovery config flags in `config.py` |
| P1.2 | Per-target `scale_pos_weight` in `ml/xgb_baseline.py` |
| P1.4 | Percentile signals + `build_alpha_score_engine()` in `alpha_engine.py` |
| P1.7 | Guidance `tanh(log1p)` impact; train scripts log universe floor ($2B default, `--discovery-train` for $250M) |
| P1.8 | Gated demand-adjusted extension |
| P1.3 | `__isna` missingness indicators + `BreakoutPredictor` parity |
| P1.9 | `ml/validation.py` purged walk-forward splits |
| P1.5-A/B | `EstimateSnapshotStore`, `get_consensus_snapshot_rows()`, daily ingest wiring, `audit_estimate_snapshots.py` |

**Not done (by design):** estimate-ramp unit fix; P1.5-C same-period revision features in ML (`demand_feature_columns()`); P1.5-F ramp calibration.

**Next:** Phase P2 (multi-bagger labels, discovery namespace) per this spec. Operational: let daily `ingest_demand_data` accumulate `estimate_snapshots/` snapshot dates before P1.5-C.

## Revised P1 order (reference)

1. P1.1 config flags
2. P1.2 rare-class weighting
3. P1.4 percentile discovery signals
4. P1.7 guidance-tail preservation
5. P1.8 demand-adjusted extension
6. P1.3 missingness indicators and train/serve parity
7. P1.9 purged walk-forward validation
8. Revised P1.5 estimate snapshot infrastructure

## Revised P1.5 - Estimate snapshot infrastructure

### Why this changed

The old P1.5 assumed `e_eps_revision_90d` and `e_rev_revision_90d` existed but were scaled wrong. The audits and Finnhub probe proved they are empty because the vendor endpoints do not provide historical as-of consensus snapshots.

### P1.5-A - Preserve local point-in-time estimate snapshots

Implement or revise the estimate store so each Finnhub fetch preserves every row with local as-of metadata.

Required identity:

```text
ticker + vendor + metric + freq + period + snapshot_date
```

Required columns:

```text
ticker
vendor_symbol
vendor
metric
snapshot_date
ingested_at
freq
period
fiscal_year
fiscal_quarter
estimate_avg
estimate_high
estimate_low
number_analysts
raw_payload_hash
source_endpoint
```

Map Finnhub fields:

```text
epsAvg -> estimate_avg where metric = eps
epsHigh -> estimate_high
epsLow -> estimate_low
revenueAvg -> estimate_avg where metric = revenue
revenueHigh -> estimate_high
revenueLow -> estimate_low
numberAnalysts -> number_analysts
period -> period
quarter -> fiscal_quarter
year -> fiscal_year
```

Guardrails:

- Do not overwrite prior snapshot dates.
- Upsert only for the same ticker/vendor/metric/freq/period/snapshot_date.
- Preserve raw response or raw hash for debugging.
- Keep existing features backward-compatible.

Suggested commit:

```text
feat(estimates): preserve point-in-time consensus snapshots
```

### P1.5-B - Snapshot cadence audit

Extend `audit_estimates_coverage.py` or create `audit_estimate_snapshots.py`.

Report:

- distinct snapshot dates per ticker/metric
- gaps between snapshot dates
- count of same-period prior values available at 7d/14d/30d/90d
- count of periods with more than one snapshot
- current active forward period
- whether prior values are same-period or contaminated by front-period roll

Root-cause labels:

```text
too_few_snapshots_for_revision
same_period_prior_missing
front_period_roll_contaminates_revision
metric_missing
feature_assignment_bug
revision_populated
```

Suggested commit:

```text
audit(estimates): report snapshot cadence and revision availability
```

### P1.5-C - Compute same-period revisions only when data exists

Add revision feature computation only after the snapshot store has at least two snapshots for the same ticker/metric/period.

Features:

```text
e_eps_revision_7d_same_period
e_eps_revision_14d_same_period
e_eps_revision_30d_same_period
e_eps_revision_90d_same_period
e_rev_revision_7d_same_period
e_rev_revision_14d_same_period
e_rev_revision_30d_same_period
e_rev_revision_90d_same_period
```

For EPS, also compute absolute deltas because percent change can explode around zero:

```text
e_eps_revision_abs_7d_same_period
e_eps_revision_abs_14d_same_period
e_eps_revision_abs_30d_same_period
e_eps_revision_abs_90d_same_period
```

Computation rule:

```text
prior = nearest snapshot <= snapshot_date - horizon_days
same ticker + same metric + same period only
revision = (estimate_now - estimate_prior) / abs(estimate_prior)
```

If `abs(estimate_prior)` is too small, do not compute percent revision. Use absolute delta only.

Suggested commit:

```text
feat(estimates): compute same-period estimate revision features
```

### P1.5-D - Active-forward revisions must be separately named

Do not compare today's front quarter against the prior front quarter and call it a same-period revision.

If implemented, use explicit names:

```text
e_eps_revision_active_forward_30d
e_rev_revision_active_forward_30d
```

These may be useful for live scoring but should be treated differently from same-period revisions.

### P1.5-E - Training guardrail

Do not add new estimate-revision velocity columns to `demand_feature_columns()` or XGBoost training until historical non-null coverage is meaningful.

Near-term allowed:

- use revision velocity in live rule-based D-EST scoring after local snapshots exist
- expose revision values in diagnostics/UI
- keep ML training unchanged

Blocked until later:

- adding new revision velocity columns to production model training
- applying ramp unit fix without populated values and quantiles

### P1.5-F - Ramp calibration only after real values exist

After revision columns populate, print quantiles:

```text
p01, p05, p25, p50, p75, p95, p99
non_null_count
zero_count
positive_count
negative_count
```

Only then calibrate `_est_quality` ramp bounds.

## Acceptance commands after P1.5-A/B

```bash
.venv/bin/python scripts/audit_estimates_coverage.py \
  --data-dir data \
  --tickers MU AVGO SNDK STX ARM WDC CIEN LITE VRT FIX \
  --output-csv data/ml/alpha_results/estimates_coverage_known_winners_v3.csv \
  --output-json data/ml/alpha_results/estimates_coverage_known_winners_v3.json
```

```bash
.venv/bin/python scripts/audit_estimates_coverage.py \
  --data-dir data \
  --sample-size 250 \
  --output-csv data/ml/alpha_results/estimates_coverage_sample_v3.csv \
  --output-json data/ml/alpha_results/estimates_coverage_sample_v3.json
```

If new snapshot audit script is created:

```bash
.venv/bin/python scripts/audit_estimate_snapshots.py \
  --data-dir data \
  --tickers MU AVGO SNDK STX ARM WDC CIEN LITE VRT FIX \
  --output data/ml/alpha_results/estimate_snapshot_cadence_v1.json
```

## Stop conditions

Stop and report if:

- existing EstimatesStore overwrites prior snapshot dates
- snapshot_date is missing or ambiguous
- the store cannot distinguish EPS from revenue rows
- same-period revision computation would compare different fiscal periods
- a proposed change touches AlphaScoreEngine before snapshot storage is fixed
- a proposed change adds all-null revision columns to training

## Summary for Cursor

P0.3 converted the estimate-revision problem from a feature-scaling task into a data-infrastructure task.

Proceed to P1 core work, but treat estimate revision velocity as a forward-captured live signal first and an ML training feature later.
