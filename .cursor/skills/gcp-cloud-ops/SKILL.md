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
| `docs/tyche_gcp_minimal_migration_spec_v2.md` | **Authoritative** GCP spec — §10 job resources, **§10.1 demand gate memory/reuse/OOM**, §11 schedules, §20 acceptance |
| `infra/gcp/README.md` | Deploy runbook (jobs, workflows, scheduler, secrets) |
| `docs/data-operations.md` | GCP cloud mode schedule vs local APScheduler |

## Architecture

```text
Cloud Scheduler (America/Los_Angeles)
  Mon–Fri 6:00 PM → tyche-evening-pipeline (4 parallel — ingest only)
            ingest-data | ingest-demand-data | ingest-news | ingest-edgar
            (NOT: flatfiles, alpha, demand-gate, publish)
  Tue–Sat 2:30 AM → tyche-morning-pipeline
            parallel: flatfiles + alpha-batch
            optional: run-demand-gate (~4–8h; failure OK)
            sequential: publish-signals → audit-snapshots
    → Cloud Run Jobs (tyche-jobs SA)
    → gs://tyche-data-prod/ (flat paths = backend/data/ layout)
    → runs/{job}/{run_id}/manifest.json
Local backend: TYCHE_DATA_BACKEND=gcs → reads published/ + signals/ only
Local CLI: same scripts (ingest_data, demand_data, flatfiles, run_demand_gate)
           against local data/ or GCS via ADC
```

### Publish prerequisites

| Required before publish | Optional |
|-------------------------|----------|
| `tyche-alpha-batch` | `tyche-run-demand-gate` (sustained ML models) |
| Evening ingest (for fresh demand/conviction inputs) | `tyche-ingest-options-flatfiles` (IV/options only) |

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

All 10 jobs: **8h** task timeout (`28800s`). `ingest-data` needs it for GCS cap
reprice; re-run `./infra/gcp/deploy_jobs.sh` (no `--build`) after timeout changes.

Workflows: do **not** use blocking `googleapis.run.v2.projects.locations.jobs.run`
(30m LRO default). YAMLs `http.post` `:run` then poll `executions.get` every 45s.
Re-run `./infra/gcp/deploy_workflow.sh` after workflow edits.

## Critical gotchas

1. **No SQLite in cloud intelligence** — `intelligence_export.py` writes
   `signals/intelligence/*.parquet`; checkpoints every 100 tickers.
2. **`--tasks=1`** — single container; asyncio parallelism only. Multi-task
   sharding is spec §21 TODO (main GCS perf fix).
3. **Workflow parallel steps** must have unique names (`run_ingest_data`, not `run`).
4. **Apple Silicon** — must build `--platform linux/amd64` via deploy script.
5. **APScheduler off** when `data_backend=gcs` (`scheduler_enabled=false`).
6. **Guidance manifest** — `guidance_tickers_fetched` vs `guidance_catalysts_written`.
7. **Flatfiles looked idle** — was subprocess `capture_output=True` + silent OHLCV preload.
   Fixed: `_run_subprocess` streams stdout; `ingest_options_flatfiles.py` logs
   `preload_ohlcv` / `download_dates` / `iv_extraction` via `job_progress`.
8. **Demand gate is morning-only** — evening runs `ingest-demand-data` (estimates),
   not `run-demand-gate`. Gate is optional before publish; UI needs alpha-batch + publish.
9. **`ingest_data.py` locally** — pass `storage_context_from_settings()` to stores;
   `IntradayStore` needs `ctx` for `_MetadataCache` (cloud jobs don't use intraday).
10. **Pacific ingest dates** — `TYCHE_INGEST_WINDOW=evening|morning` on Cloud Run jobs;
    `market_data/ingest_dates.py` (`pacific_today()`). Works on laptop regardless of host TZ.
11. **Local backend vs job deploy** — API read-path fixes (e.g. published-route NaN
    sanitization) need **backend restart only**, not `deploy_jobs.sh`. Redeploy Cloud Run
    jobs when batch Python changes; optional re-run `publish-signals` to rewrite GCS JSON.
12. **Published JSON NaN** — intelligence Parquet missing datetimes → `nan` in dict rows.
    `json_io.sanitize_for_json()` on write + `published_routes` on read. Legacy GCS files
    with `NaN` tokens work after backend restart.
13. **Demand gate memory** — see spec **§10.1**: build ~16 GiB, walk-forward **32 GiB**,
    `TYCHE_DEMAND_GATE_REUSE_DATASET`, exit -9 phase diagnosis.

## Observability

All batch jobs emit structlog events via `tyche/ops/job_progress.py`:

| Event | Purpose |
|-------|---------|
| `gcp_job_start` / `gcp_job_complete` | Container lifecycle (`run_gcp_job.py`) |
| `job_phase` | Step boundary (start / complete / skip) |
| `job_progress` | Loop progress: done, total, pct, eta_min |

**Cloud Logging queries** (see `infra/gcp/README.md`):

```text
resource.type="cloud_run_job"
jsonPayload.event="job_progress"
```

Filter by job: `jsonPayload.job="ingest-options-flatfiles"`.

**Phase map:** ingest-data (`bootstrap_ohlcv`, `recompute_market_caps`);
flatfiles (`preload_ohlcv`, `download_dates`, `iv_extraction`);
alpha-batch (`build_features`, `score_variant`);
demand gate (`build_dataset`, `walk_forward`, `promote_models`);
publish-signals (per-route phases).

Manifests (`runs/{job}/{run_id}/manifest.json`) are post-hoc summaries only —
use Cloud Logging for live state.

## Debug

```bash
gsutil cat gs://tyche-data-prod/runs/ingest_demand_data/*/manifest.json | tail -1
gcloud run jobs executions list --job=tyche-ingest-news --region=us-central1
cd backend && .venv/bin/python scripts/run_gcp_job.py audit-snapshots
```

## Open TODO (§21)

Multi-task ingest sharding via `CLOUD_RUN_TASK_INDEX` + batched GCS Parquet writes
(`write_bars`, cap recompute). Highest impact: `ingest-data`, `ingest-options-flatfiles`.
