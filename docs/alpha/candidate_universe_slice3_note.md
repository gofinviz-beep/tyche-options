# Candidate Universe — Slice 3

**Status:** Implemented (pending GCP verification after deploy)

## Contract

```text
ticker_meta.parquet + alpha_signals_sustained.parquet + signals/stocks/conviction.parquet
  → tyche-candidate-universe-batch
  → signals/universe/options_candidates.parquet  (top 500 by priority score)
  → signals/universe/csp_scan_tickers.parquet    (csp_eligible only, ranked)
  → signals/universe/stocks_candidates.parquet   (top 3000 by market cap)
```

No published JSON route yet — Parquet is the inspectable artifact for Slice 4+ consumers.

## Selection order

1. Start from `ticker_meta.parquet` tickers (not OHLCV directory scan).
2. Filter common stock (`filter_equity_only`).
3. Filter by market cap (`options_snapshot_min_market_cap` for options; `min_market_cap_millions` for stocks).
4. Filter by optionable flag when metadata column exists and `require_optionable=true`.
5. Read OHLCV only for survivors — min price + 20d avg volume.
6. Join sustained alpha + stocks conviction Parquet.
7. Rank options by `compute_priority_score()` (CSP eligibility, conviction, IV/VRP, alpha).
8. Cap at `options_candidate_max_tickers` / `stocks_derived_max_tickers`.
9. Export **`csp_scan_tickers.parquet`**: all survivors with `csp_eligible=true` from conviction, ranked by priority score (no separate cap). This is the **scanner + post-open Tradier fetch list**.

## Artifact roles

| Artifact | Scope | Consumers |
|----------|-------|-----------|
| `options_candidates.parquet` | Top 500 by priority | Slice 4 flatfile chain prep (broad I/O cap) |
| `csp_scan_tickers.parquet` | `csp_eligible=true` only | Slice 5 scanner, optional Tradier refresh |

## Config knobs

| Setting | Default |
|---------|---------|
| `options_candidate_max_tickers` | 500 |
| `stocks_derived_max_tickers` | 3000 |
| `require_optionable` | true (no-op until meta has `optionable` column) |

## Morning pipeline placement

After `tyche-stocks-derived-batch`, before `tyche-options-chain-prep-batch`.

## Key files

- `backend/src/tyche/workflow/candidate_universe.py`
- `backend/src/tyche/market_data/universe_candidates_store.py`
- `backend/src/tyche/ops/gcp_jobs.py` — `run_candidate_universe_batch_job`
- `backend/tests/unit/test_cloud_candidate_universe.py`

## Deploy

```bash
source infra/gcp/config.env
./infra/gcp/deploy_jobs.sh --build
./infra/gcp/deploy_workflow.sh
gcloud run jobs execute tyche-candidate-universe-batch --wait
gsutil ls gs://tyche-data-prod/signals/universe/
```
