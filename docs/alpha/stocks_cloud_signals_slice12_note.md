# Stocks cloud signals — Slices 1–2 completion note

**Date:** 2026-06-25  
**Spec:** `docs/tyche_cloud_computed_signals_completion_spec_v1.md`  
**Status:** Slices 1–2 **verified in GCP** (`tyche-data-prod`). Slice 3+ pending.

## Cloud contract (live)

```text
tyche-stocks-conviction-batch  → signals/stocks/conviction.parquet
tyche-stocks-derived-batch     → signals/stocks/deep_dips.parquet
                                 signals/stocks/history_summary.parquet
tyche-publish-signals          → published/routes/stocks_conviction.json
                                 published/routes/stocks_deep_dips.json
                                 published/routes/stocks_history.json
```

Morning workflow order (`infra/gcp/workflows/morning-pipeline.yaml`):

```text
parallel: flatfiles + alpha-batch
→ optional: run-demand-gate
→ stocks-conviction-batch
→ stocks-derived-batch
→ publish-signals
→ audit-snapshots
```

Evening pipeline is **unchanged** (ingest-only). Stocks compute runs in the morning after OHLCV from the prior evening ingest.

## GCP verification (2026-06-25)

| Artifact | Status | Notes |
|----------|--------|-------|
| `signals/stocks/conviction.parquet` | ✓ | ~1995 rows; `as_of` 2026-06-24 (morning session) |
| `published/routes/stocks_conviction.json` | ✓ | `status: ok`, source `signals/stocks/conviction.parquet` |
| `signals/stocks/history_summary.parquet` | ✓ | ~1504 rows |
| `published/routes/stocks_history.json` | ✓ | `status: ok` |
| `signals/stocks/deep_dips.parquet` | ✓ | 141 alerts (2026-06-25 session) |
| `published/routes/stocks_deep_dips.json` | ✓ | `status: ok` |

Manifests: `runs/stocks_conviction_batch/`, `runs/stocks_derived_batch/`, `runs/publish_signals/`.

## Production fixes applied during rollout

1. **`publish_signals` legacy fallback** — removed `conviction_signals.parquet` from publish candidates. That file is the local EMA disk cache (no `trend_state` / `conviction_level`); using it caused Pydantic validation failures in Cloud Run.
2. **Deep dip batch `prior_streak`** — `ConvictionSnapshotResponse` now includes `prior_streak`; derived batch reads conviction Parquet via `_snapshot_to_signal()` without `AttributeError`.
3. **Parquet load normalization** — `load_stocks_conviction_parquet()` coerces `date` → ISO string and skips incomplete rows with warnings.

## API behavior (GCS mode)

With `TYCHE_DATA_BACKEND=gcs`, `api_prefer_published_signals=true`, `api_allow_curated_fallback=false`, `api_allow_local_db_fallback=false`:

- `GET /stocks/conviction/snapshots` — published JSON → signal Parquet; no `conviction.db`, no `read_all()`
- `GET /stocks/deep-dips` — published JSON → signal Parquet; no live OHLCV scan
- `GET /stocks/transitions`, `GET /stocks/conviction/history` — published `stocks_history.json` payload

Local `POST /stocks/conviction/refresh` still runs live batch (dev path).

## Tests

```bash
cd backend && .venv/bin/pytest tests/unit/test_cloud_stocks_conviction.py \
  tests/unit/test_cloud_stocks_derived.py tests/unit/test_deep_dip_recovery_signal.py -q
```

Pre-deploy gate in `deploy_jobs.sh` includes the cloud stocks tests.

## Manual recovery (bootstrap)

If morning workflow skipped stocks jobs or publish failed:

```bash
gcloud run jobs execute tyche-stocks-conviction-batch --region=us-central1 --project=tyche-platform --wait
gcloud run jobs execute tyche-stocks-derived-batch      --region=us-central1 --project=tyche-platform --wait
gcloud run jobs execute tyche-publish-signals           --region=us-central1 --project=tyche-platform --wait
```

## Next: Slice 3

`workflow/candidate_universe.py` → `signals/universe/options_candidates.parquet`. See spec §7 and §13.
