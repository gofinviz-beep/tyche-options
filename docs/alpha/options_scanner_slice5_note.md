# Options Scanner Batch — Slice 5

**Status:** Verified in `tyche-data-prod` (2026-06-27). Morning pipeline E2E green.
See `docs/alpha/cloud_signals_slice67_completion_note.md`.

## Contract

```text
csp_scan_tickers.parquet + conviction.parquet + options_chain_contracts.parquet
  → tyche-options-scanner-batch
  → signals/options/scanner.parquet
  → reports/options_scanner/manifest.json
  → publish-signals → published/routes/options_scanner.json
```

No live Tradier in the morning pipeline. Chains come from Slice 4 flatfile prep via `ArtifactChainBroker`.

## Flow (mirrors local scanner gates)

1. Load ranked tickers from `csp_scan_tickers.parquet` (already `csp_eligible`).
2. Filter institutional ownership from `ticker_meta.parquet` (Parquet, not yfinance).
3. Intersect with tickers that have rows in `options_chain_contracts.parquet`.
4. Run `StrategyEngine.scan_csp_candidates()` with `min_oi=0` for flatfile chains.
5. Write top-N candidates to Parquet + manifest; publish reads both.

## Morning pipeline placement

After `tyche-options-chain-prep-batch`, before `tyche-publish-signals`.

## Key files

- `backend/src/tyche/workflow/options_scanner_batch.py`
- `backend/src/tyche/broker/artifact_chain.py`
- `backend/src/tyche/market_data/options_scanner_store.py`
- `backend/src/tyche/workflow/publish_signals.py` — `publish_options_scanner()`
- `backend/tests/unit/test_cloud_options_scanner.py`

## Deploy / verify

```bash
source infra/gcp/config.env
./infra/gcp/deploy_jobs.sh --build
./infra/gcp/deploy_workflow.sh
gcloud run jobs execute tyche-candidate-universe-batch --wait
gcloud run jobs execute tyche-options-chain-prep-batch --wait
gcloud run jobs execute tyche-options-scanner-batch --wait
gcloud run jobs execute tyche-publish-signals --wait
gsutil ls gs://tyche-data-prod/signals/universe/
gsutil ls gs://tyche-data-prod/signals/options/
```
