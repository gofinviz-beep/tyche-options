# Tyche Cloud-Computed Signals Completion Spec v1 - Pre-App-Deploy Gate

Audience: Cursor Composer 2.5 Fast operating in `tyche-options-main`.

Purpose: finish the remaining cloud-compute gaps before deploying the Tyche web app to Cloud Run. The goal is that **all normal app pages load from compact precomputed artifacts** in GCS (`published/routes/*.json` and `signals/**/*.parquet`) whether the app runs locally or on Cloud Run.

This spec is separate from:
- `docs/tyche_gcp_minimal_migration_spec_v2.md` - batch/data migration reference.
- `docs/tyche_cloudrun_app_iap_spec_v1.md` - app/IAP deployment reference.
- Multi-Bagger Discovery Engine specs.

This spec must be completed before considering the Cloud Run app deployment production-ready.

---

## 0. Executive target

Move the remaining app-page compute to GCP.

Current good pattern:

```text
Directional Alpha:
  GCP alpha-batch
    -> alpha_signals*.parquet
    -> published/routes/stocks_alpha.json
  App reads one compact JSON/parquet artifact.
```

Target pattern for every page:

```text
GCP scheduled jobs:
  ingest/update raw + curated data
  compute route-specific signals
  publish route JSONs

Any app runtime:
  local laptop backend OR Cloud Run app
  reads only published/routes/*.json or signals/**/*.parquet
  never scans 13K OHLCV/options files on page load
```

---

## 1. Non-negotiable principle

Normal UI/API routes must be **read-only serving paths**, not compute paths.

In cloud mode:

```text
TYCHE_DATA_BACKEND=gcs
TYCHE_API_PREFER_PUBLISHED_SIGNALS=true
TYCHE_API_ALLOW_CURATED_FALLBACK=false
TYCHE_APP_MODE=cloudrun or gcs-local
```

A route is production-ready only when:

```text
route request
  -> reads published/routes/<route>.json
  -> optionally reads compact signals/<domain>/<artifact>.parquet
  -> returns response
```

A route is not production-ready if it:

```text
- calls read_all() across 13K OHLCV Parquet files;
- scans options_history/ or options_iv/ across the universe;
- recomputes conviction for all tickers on request;
- calls Tradier for every CSP-eligible name on request;
- reads local SQLite such as conviction.db in cloud mode;
- silently falls back to raw/curated GCS scans.
```

---

## 2. App deploy gate

Do not deploy the app to Cloud Run as the primary UI until one of these is true for every visible route:

1. The route is backed by `published/routes/*.json`.
2. The route is backed by compact `signals/**/*.parquet`.
3. The route is hidden/disabled in cloud mode with a clear message.

No visible route should do heavy compute on demand.

---

## 3. Route completion matrix

### Already aligned

| Page | Target artifact | Status |
|---|---|---|
| `/stocks/alpha/` | `published/routes/stocks_alpha.json` | Good |
| `/intelligence/news` | `published/routes/intelligence_news.json` | Good |
| `/intelligence/filings` | `published/routes/intelligence_filings.json` | Good |
| `/intelligence/insider` | `published/routes/intelligence_insider.json` or `signals/intelligence/insider.parquet` | Verify read path |

### Must be completed before app deploy

| Page | Required signal artifact | Required published artifact |
|---|---|---|
| `/options` | `signals/options/monitor.parquet` | `published/routes/options.json` |
| `/options/scanner` | `signals/options/scanner.parquet` | `published/routes/options_scanner.json` |
| `/options/conviction` | `signals/options/conviction.parquet` | `published/routes/options_conviction.json` |
| `/options/explore` | `signals/options/explore.parquet` | `published/routes/options_explore.json` |
| `/options/monitor` | `signals/options/monitor.parquet` | `published/routes/options_monitor.json` |
| `/options/covered-calls` | `signals/options/covered_calls.parquet` | `published/routes/options_covered_calls.json` |
| `/stocks/conviction` | `signals/stocks/conviction.parquet` | `published/routes/stocks_conviction.json` |
| `/stocks/deep-dips` | `signals/stocks/deep_dips.parquet` | `published/routes/stocks_deep_dips.json` |
| `/stocks/history` | `signals/stocks/history_summary.parquet` | `published/routes/stocks_history.json` |

---

## 4. Artifact envelope standard

Every route-level JSON artifact should use a consistent envelope:

```json
{
  "route": "/options/scanner",
  "route_key": "options_scanner",
  "as_of": "2026-06-09",
  "generated_at": "2026-06-09T10:45:00Z",
  "run_id": "...",
  "status": "ok",
  "stale": false,
  "row_count": 250,
  "source_paths": [
    "signals/options/scanner.parquet"
  ],
  "warnings": [],
  "errors": [],
  "data": []
}
```

Rules:
- Use `json_io.write_json(..., allow_nan=False)` and sanitize NaN/NA to `null`.
- Include enough freshness metadata for the UI to show whether data is stale.
- Keep route JSON compact enough for fast page load.
- If JSON becomes too large, publish a slim route JSON plus compact Parquet path reference.

---

## 5. Signal artifact schemas

These are first-pass schemas. Cursor should adapt exact column names to existing code, but must preserve the serving contracts.

### 5.1 `signals/stocks/conviction.parquet`

Purpose: cloud replacement for local `conviction.db`.

Minimum columns:

```text
ticker
as_of
rank
conviction_score
conviction_bucket
last_price
market_cap
avg_volume_30d
relative_strength
trend_score
volume_score
quality_score
catalyst_score
alpha_score
alpha_state
discovery_score optional
risk_flags
summary
generated_at
source_run_id
```

Published route:

```text
published/routes/stocks_conviction.json
```

### 5.2 `signals/stocks/deep_dips.parquet`

Purpose: precomputed deep-dip candidates.

Minimum columns:

```text
ticker
as_of
last_price
drawdown_from_52w_high_pct
drawdown_from_ath_pct optional
distance_to_200dma_pct
rsi_14
volume_z_20d
reversal_score
dip_quality_score
demand_score
risk_flags
summary
generated_at
source_run_id
```

Published route:

```text
published/routes/stocks_deep_dips.json
```

### 5.3 `signals/stocks/history_summary.parquet`

Purpose: compact history page summary, not raw OHLCV.

Minimum columns:

```text
ticker
as_of
last_price
return_1d
return_5d
return_1m
return_3m
return_6m
return_1y
return_3y optional
high_52w
low_52w
drawdown_52w_pct
avg_volume_30d
atr_14
trend_state
history_sparkline optional compact array/json
generated_at
```

Published route:

```text
published/routes/stocks_history.json
```

### 5.4 `signals/options/scanner.parquet`

Purpose: cloud replacement for local `POST /scanner/scan`.

Minimum columns:

```text
ticker
as_of
underlying_price
scanner_rank
scanner_score
strategy
expiration
strike
option_type
bid
ask
mid
delta
iv
iv_rank
iv_percentile
open_interest
volume
spread_pct
annualized_yield
probability_otm optional
max_profit optional
max_loss optional
breakeven optional
conviction_score
alpha_state
risk_flags
candidate_reason
generated_at
source_run_id
```

Published route:

```text
published/routes/options_scanner.json
```

### 5.5 `signals/options/conviction.parquet`

Purpose: ranked options conviction candidates.

Minimum columns:

```text
ticker
as_of
underlying_price
options_conviction_score
stock_conviction_score
strategy
recommended_action
expiration
strike
option_type
delta
iv
iv_rank
iv_percentile
liquidity_score
spread_pct
risk_reward_score
risk_flags
summary
generated_at
source_run_id
```

Published route:

```text
published/routes/options_conviction.json
```

### 5.6 `signals/options/explore.parquet`

Purpose: compact exploration dataset for optionable candidates.

Minimum columns:

```text
ticker
as_of
underlying_price
market_cap
avg_volume_30d
alpha_score
conviction_score
iv_rank
iv_percentile
atm_iv
put_call_volume_ratio optional
top_strategy
watch_state
risk_flags
generated_at
```

Published route:

```text
published/routes/options_explore.json
```

### 5.7 `signals/options/monitor.parquet`

Purpose: watchlist/monitor summary.

Minimum columns:

```text
ticker
as_of
underlying_price
watch_state
alpha_state
conviction_state
options_state
latest_signal
days_since_signal
iv_rank
iv_percentile
risk_flags
next_action
generated_at
```

Published routes:

```text
published/routes/options_monitor.json
published/routes/options.json
```

### 5.8 `signals/options/covered_calls.parquet`

Purpose: covered-call candidates.

Minimum columns:

```text
ticker
as_of
underlying_price
expiration
strike
call_bid
call_ask
call_mid
delta
iv
open_interest
volume
spread_pct
covered_call_score
annualized_yield
downside_buffer_pct
assignment_risk
risk_flags
summary
generated_at
```

Published route:

```text
published/routes/options_covered_calls.json
```

---

## 6. New Cloud Run Jobs

Add four first-pass jobs. They can be implemented as separate Cloud Run Jobs or one orchestrated job with subcommands, but outputs must be separate and traceable.

### 6.1 `tyche-stocks-conviction-batch`

Purpose:
- replace local `conviction.db` dependency with GCS Parquet.
- compute `/stocks/conviction`.

Inputs:

```text
ticker_meta.parquet
alpha_signals*.parquet
signals/intelligence/*.parquet optional
fundamentals/ estimates/ catalyst_signals/ optional
```

Outputs:

```text
signals/stocks/conviction.parquet
published/routes/stocks_conviction.json via publish_signals
```

Implementation:
- reuse existing conviction logic if possible;
- export DataFrame to Parquet;
- do not write SQLite as canonical cloud output;
- SQLite may remain local-dev only.

### 6.2 `tyche-stocks-derived-batch`

Purpose:
- compute `/stocks/deep-dips` and `/stocks/history`.

Inputs:

```text
ticker_meta.parquet
ohlcv_daily/ selected subset
alpha_signals*.parquet
```

Outputs:

```text
signals/stocks/deep_dips.parquet
signals/stocks/history_summary.parquet
```

Important:
- avoid reading all 13K OHLCV files when possible.
- use metadata/index filtering first.
- if universe-wide history summary truly requires all tickers, do it once in GCP job, never in route handler.

### 6.3 `tyche-options-snapshot-batch`

Purpose:
- fetch option chain snapshots once in GCP for selected candidate universe.
- avoid calling Tradier from every page request.

Inputs:

```text
ticker_meta.parquet
alpha_signals*.parquet
signals/stocks/conviction.parquet optional
```

Outputs:

```text
options_chains/{as_of}/{ticker}.json or parquet
signals/options/options_chain_snapshot.parquet optional
reports/options_snapshot/manifest.json
```

Universe selection:
- use optionable/liquid tickers only;
- use metadata and alpha/conviction filters before vendor API calls;
- max tickers configurable.

Config:

```text
TYCHE_OPTIONS_SNAPSHOT_MAX_TICKERS=500
TYCHE_OPTIONS_SNAPSHOT_MIN_MARKET_CAP=...
TYCHE_OPTIONS_SNAPSHOT_MIN_AVG_VOLUME=...
```

### 6.4 `tyche-options-scanner-batch`

Purpose:
- cloud replacement for local scanner.
- compute scanner, options conviction, covered calls, monitor, explore.

Inputs:

```text
signals/options/options_chain_snapshot.parquet or options_chains/{as_of}/
signals/stocks/conviction.parquet
alpha_signals*.parquet
ticker_meta.parquet
limited OHLCV feature summaries, not raw scan in route
```

Outputs:

```text
signals/options/scanner.parquet
signals/options/conviction.parquet
signals/options/explore.parquet
signals/options/monitor.parquet
signals/options/covered_calls.parquet
```

Implementation:
- reuse existing `run_morning_scan()` logic where practical;
- refactor it so compute can run as batch and return/write DataFrames;
- remove route dependency on local scanner compute;
- do not call `read_all()` across 13K tickers from API requests.

---

## 7. Candidate universe optimization

The options scanner should not begin by reading OHLCV for all 13K tickers.

Add a candidate universe builder:

```text
backend/src/tyche/workflow/candidate_universe.py
```

Inputs:

```text
ticker_meta.parquet
alpha_signals_sustained.parquet
stocks_conviction.parquet optional
liquidity metadata
optionable flag if available
```

Outputs:

```text
signals/universe/options_candidates.parquet
signals/universe/stocks_candidates.parquet
```

Candidate selection should happen in this order:

1. Start from metadata table, not OHLCV file scan.
2. Filter by active/common stock if metadata supports it.
3. Filter by minimum market cap.
4. Filter by minimum price.
5. Filter by minimum average volume/liquidity.
6. Filter by optionable if available.
7. Join alpha/conviction signals.
8. Keep top N by priority score.
9. Only then read ticker-specific OHLCV/options where required.

Config:

```text
TYCHE_OPTIONS_CANDIDATE_MAX_TICKERS=500
TYCHE_STOCKS_DERIVED_MAX_TICKERS=3000 optional
TYCHE_REQUIRE_OPTIONABLE=true
```

This candidate universe is itself a signal artifact and should be inspectable.

---

## 8. API/read-path changes

### 8.1 Scanner

Current anti-pattern:

```text
POST /scanner/scan
  -> run_morning_scan()
  -> read_all() 13K GCS Parquet
  -> Tradier calls
```

Replace with:

```text
GET /scanner/latest
  -> read published/routes/options_scanner.json
```

`POST /scanner/scan` in cloud/GCS mode:

Option A first pass:

```text
return 409 or 405:
  "Cloud mode does not run scanner inline. Use scheduled tyche-options-scanner-batch or trigger job."
```

Option B later:

```text
trigger Cloud Run Job and return job execution id
```

Local-dev override:

```text
POST /scanner/scan?mode=local-dev
```

Only works when `TYCHE_DATA_BACKEND=local` or explicit `TYCHE_ALLOW_INLINE_SCAN=true`.

### 8.2 Options conviction

Route should read:

```text
published/routes/options_conviction.json
```

Fallback:

```text
signals/options/conviction.parquet
```

No live engine in normal cloud mode.

### 8.3 Stocks conviction

Route should read:

```text
published/routes/stocks_conviction.json
```

Fallback:

```text
signals/stocks/conviction.parquet
```

No local `conviction.db` in cloud mode.

### 8.4 Deep dips and history

Routes should read:

```text
published/routes/stocks_deep_dips.json
published/routes/stocks_history.json
```

Fallback:

```text
signals/stocks/deep_dips.parquet
signals/stocks/history_summary.parquet
```

No OHLCV scan in route handler.

---

## 9. Publisher changes

Extend:

```text
backend/src/tyche/workflow/publish_signals.py
backend/scripts/publish_signals.py
```

Remove placeholder-only behavior for:

```text
options_scanner
options_conviction
options_explore
options_monitor
options_covered_calls
stocks_conviction
stocks_deep_dips
stocks_history
```

For each route:
1. read corresponding signal Parquet;
2. select columns needed by UI;
3. sort/rank rows;
4. limit rows for first page load;
5. write route JSON envelope;
6. write route manifest;
7. fail or publish explicit stale/not-ready state if upstream missing.

Do not silently publish empty placeholders once the job is expected to exist.

---

## 10. Workflow changes

Update:

```text
infra/gcp/deploy_jobs.sh
backend/scripts/run_gcp_job.py
backend/src/tyche/ops/gcp_jobs.py
infra/gcp/workflows/morning-pipeline.yaml
infra/gcp/workflows/evening-pipeline.yaml if needed
```

Recommended morning flow:

```text
Parallel:
  tyche-ingest-options-flatfiles
  tyche-alpha-batch

Then:
  tyche-stocks-conviction-batch
  tyche-stocks-derived-batch
  tyche-candidate-universe-batch
  tyche-options-snapshot-batch
  tyche-options-scanner-batch

Optional:
  tyche-run-demand-gate

Then:
  tyche-publish-signals
  tyche-audit-snapshots
```

Alternative:
- run stocks conviction/derived in evening after OHLCV/demand/news ingest;
- run options snapshot/scanner in morning after options flatfiles and alpha.

Choose the simpler implementation first, but guarantee `publish_signals` runs after all route artifacts needed for UI.

---

## 11. IAM and secrets

Runtime for new jobs:

```text
tyche-jobs@tyche-platform.iam.gserviceaccount.com
```

Required:

```text
Storage Object Admin on tyche-data-prod bucket
Secret Manager Secret Accessor for Tradier/Massive/etc.
Logging Log Writer
```

No service-account JSON keys.

The app runtime later remains:

```text
tyche-ui@tyche-platform.iam.gserviceaccount.com
```

Read-only:

```text
Storage Object Viewer
```

The app must not need vendor API secrets for normal page loads.

---

## 12. Guardrail tests

Add tests that fail if cloud-mode routes use heavy paths.

### 12.1 API cloud-mode tests

For each route:

```text
/stocks/conviction
/stocks/deep-dips
/stocks/history
/options/scanner
/options/conviction
/options/explore
/options/monitor
/options/covered-calls
```

Test:
- with `TYCHE_DATA_BACKEND=gcs`;
- with `TYCHE_API_ALLOW_CURATED_FALLBACK=false`;
- route reads published JSON or signal Parquet;
- route does not call scanner compute;
- route does not call `read_all()`;
- route does not open `conviction.db`;
- route does not call Tradier unless explicitly live mode.

### 12.2 Publisher tests

For each expected signal file:
- sample Parquet input -> route JSON output;
- NaN sanitized to `null`;
- JSON contains route, as_of, generated_at, row_count, data.

### 12.3 Job smoke tests

Each new job should support:

```bash
python scripts/run_gcp_job.py --job stocks-conviction --dry-run
python scripts/run_gcp_job.py --job stocks-derived --dry-run
python scripts/run_gcp_job.py --job options-snapshot --dry-run
python scripts/run_gcp_job.py --job options-scanner --dry-run
```

or equivalent subcommands.

---

## 13. Implementation order

### Slice 1 - Stocks conviction cloud export

```text
Build tyche-stocks-conviction-batch.
Write signals/stocks/conviction.parquet.
Publish stocks_conviction.json.
Patch /stocks/conviction read path.
```

Why first:
- removes SQLite cloud gap;
- easier than options scanner;
- establishes pattern.

### Slice 2 - Stocks deep dips/history

```text
Build tyche-stocks-derived-batch.
Write deep_dips + history_summary.
Publish route JSONs.
Patch routes.
```

### Slice 3 - Options candidate universe

```text
Build candidate_universe.py.
Write signals/universe/options_candidates.parquet.
Use metadata/alpha/conviction filters before OHLCV/vendor reads.
```

### Slice 4 - Options chain snapshot

```text
Build tyche-options-snapshot-batch.
Fetch Tradier chains once per candidate ticker in GCP.
Persist snapshot artifact + manifest.
```

### Slice 5 - Options scanner batch

```text
Refactor run_morning_scan into batch workflow.
Write scanner, conviction, explore, monitor, covered_calls Parquet.
Publish all options route JSONs.
```

### Slice 6 - API cloud-mode enforcement

```text
Patch all listed routes.
GET reads published/signal only.
POST scan no longer runs inline in cloud mode.
Add tests.
```

### Slice 7 - Workflow integration

```text
Deploy new Cloud Run jobs.
Add jobs to morning/evening workflow before publish.
Run end-to-end.
```

---

## 14. Acceptance criteria

This spec is done when:

```text
[x] signals/stocks/conviction.parquet produced in GCP.
[x] published/routes/stocks_conviction.json produced in GCP.
[x] signals/stocks/deep_dips.parquet produced in GCP.
[x] published/routes/stocks_deep_dips.json produced in GCP.
[x] signals/stocks/history_summary.parquet produced in GCP.
[x] published/routes/stocks_history.json produced in GCP.
[ ] signals/universe/options_candidates.parquet produced in GCP.
[ ] options chain snapshots are fetched in GCP, not per page request.
[ ] signals/options/scanner.parquet produced in GCP.
[ ] published/routes/options_scanner.json produced in GCP.
[ ] signals/options/conviction.parquet produced in GCP.
[ ] published/routes/options_conviction.json produced in GCP.
[ ] signals/options/explore.parquet produced in GCP.
[ ] published/routes/options_explore.json produced in GCP.
[ ] signals/options/monitor.parquet produced in GCP.
[ ] published/routes/options_monitor.json produced in GCP.
[ ] signals/options/covered_calls.parquet produced in GCP.
[ ] published/routes/options_covered_calls.json produced in GCP.
[ ] Local backend in GCS mode loads all listed pages from published/signals only.
[ ] No normal page request scans 13K OHLCV Parquet files.
[ ] No normal page request scans full options history/IV directories.
[ ] No normal page request calls Tradier for the whole universe.
[ ] No normal cloud-mode route depends on local SQLite.
[ ] publish_signals no longer emits placeholders for completed routes.
[ ] Morning/evening workflow produces all route artifacts before publish.
```

Definition of done:

```text
All visible Tyche app pages are cloud-computed and artifact-served.
The app can run locally or on Cloud Run and page-load behavior is the same:
read compact precomputed JSON/Parquet only.
```

**Slice 1–2 completion (2026-06-25):** verified in `tyche-data-prod`. See `docs/alpha/stocks_cloud_signals_slice12_note.md`.

---

## 15. Cursor start prompt

Use this prompt to begin implementation:

```text
Use docs/tyche_cloud_computed_signals_completion_spec_v1.md as the authoritative pre-app-deploy spec.

Do not deploy the Tyche app to Cloud Run yet.

Goal:
Move the remaining UI compute paths to GCP and make every visible app page read compact precomputed artifacts.

Start with Slice 1 only:
- implement tyche-stocks-conviction-batch;
- export signals/stocks/conviction.parquet;
- update publish_signals to produce published/routes/stocks_conviction.json from that Parquet;
- patch /stocks/conviction to read published first, signals second;
- disable local conviction.db fallback in cloud/GCS mode unless explicitly dev-enabled;
- add tests proving cloud-mode route does not read conviction.db or raw/curated data.

Do not modify alpha-batch, demand gate, Multi-Bagger P2, or Cloud Run app/IAP deployment in this slice.
```
