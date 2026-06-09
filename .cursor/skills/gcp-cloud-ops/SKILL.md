---
name: gcp-cloud-ops
description: >-
  GCP cloud batch operations for tyche-options: Cloud Run Jobs, Workflows,
  Scheduler, GCS storage mode, publish_signals, intelligence Parquet rollups,
  deploy scripts, and manifest debugging. Use when deploying jobs, fixing
  cloud ingest failures, wiring schedules, or working with TYCHE_DATA_BACKEND=gcs.
---

# GCP Cloud Ops (Tyche)

## Authority

| Doc / path | Role |
|------------|------|
| `docs/tyche_gcp_minimal_migration_spec_v2.md` | Architecture spec + acceptance checklist + §21 TODO |
| `infra/gcp/README.md` | Deploy runbook (jobs, workflows, scheduler, secrets) |
| `docs/data-operations.md` | GCP cloud mode schedule vs local APScheduler |

## Architecture

```text
Cloud Scheduler (Tue–Sat PT)
  6:00 PM → tyche-evening-pipeline (parallel)
  2:30 AM → tyche-morning-pipeline (parallel then sequential)
    → Cloud Run Jobs (tyche-jobs SA)
    → gs://tyche-data-prod/ (flat paths = backend/data/ layout)
    → runs/{job}/{run_id}/manifest.json
Local backend: TYCHE_DATA_BACKEND=gcs → reads published/ + signals/ only
```

## Jobs (10)

`ingest-data`, `ingest-options-flatfiles`, `ingest-demand-data`, `ingest-news`,
`ingest-edgar`, `alpha-batch`, `run-demand-gate`, `publish-signals`,
`audit-snapshots`, `nightly-pipeline` (fallback).

Entry: `backend/scripts/run_gcp_job.py` → `tyche/ops/gcp_jobs.py`.

## Deploy sequence

```bash
source infra/gcp/config.env
./infra/gcp/deploy_jobs.sh --build   # after Python changes; linux/amd64 required
./infra/gcp/deploy_workflow.sh
./infra/gcp/deploy_scheduler.sh
```

## Critical gotchas

1. **No SQLite in cloud intelligence** — `intelligence_export.py` writes
   `signals/intelligence/*.parquet`; checkpoints every 100 tickers.
2. **`--tasks=1`** — single container; asyncio parallelism only. Multi-task
   sharding is spec §21 TODO (main GCS perf fix).
3. **Workflow parallel steps** must have unique names (`run_ingest_data`, not `run`).
4. **Apple Silicon** — must build `--platform linux/amd64` via deploy script.
5. **APScheduler off** when `data_backend=gcs` (`scheduler_enabled=false`).
6. **Guidance manifest** — `guidance_tickers_fetched` vs `guidance_catalysts_written`.

## Debug

```bash
gsutil cat gs://tyche-data-prod/runs/ingest_demand_data/*/manifest.json | tail -1
gcloud run jobs executions list --job=tyche-ingest-news --region=us-central1
cd backend && .venv/bin/python scripts/run_gcp_job.py audit-snapshots
```

## Open TODO (§21)

Multi-task ingest sharding via `CLOUD_RUN_TASK_INDEX` + batched GCS Parquet writes
(`write_bars`, cap recompute). Highest impact: `ingest-data`, `ingest-options-flatfiles`.
