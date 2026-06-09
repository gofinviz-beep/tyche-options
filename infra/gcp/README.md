# Tyche GCP Batch Jobs (GCP-F / GCP-G)

Cloud Run Jobs compute and publish artifacts to `gs://tyche-data-prod/`. The local
backend reads `published/` and `signals/` only (`TYCHE_DATA_BACKEND=gcs`).

## Architecture

```text
Cloud Scheduler (Tue–Sat)
  6:00 PM PT → ``tyche-evening-pipeline`` (parallel ingest)
  2:30 AM PT → ``tyche-morning-pipeline`` (parallel options+alpha, publish)
    → parallel + sequential Cloud Run Jobs (tyche-jobs SA)
      → GCS read/write + Secret Manager API keys
      → runs/{job}/{run_id}/manifest.json
    → publish_signals → published/routes/*.json
```

### Two-window schedule (Tue–Sat PT)

**Evening 6:00 PM** — post–market-close sources, **all jobs in parallel**:

| Job | Outputs |
|-----|---------|
| `tyche-ingest-data` | `ohlcv_daily/`, repriced `ticker_meta.parquet` |
| `tyche-ingest-demand-data` | `fundamentals/`, `estimates/`, `estimate_snapshots/`, `short_interest/`, `catalyst_signals/` |
| `tyche-ingest-news` | `news_articles/`, `signals/intelligence/news.parquet` (batched checkpoints) |
| `tyche-ingest-edgar` | `filings_8k/`, `insider_transactions/`, intelligence signal Parquet |

**Morning 2:30 AM** — after options flatfile lands (~2 AM):

| Step | Job | Outputs |
|------|-----|---------|
| Parallel | `tyche-ingest-options-flatfiles` + `tyche-alpha-batch` | options/IV/derived + `alpha_signals*.parquet` |
| Optional | `tyche-run-demand-gate` | `ml/alpha_results/`, optional `ml/models/big_move_sustained_*` (~4–8h) |
| Sequential | `tyche-publish-signals` | `published/routes/*.json` |
| Sequential | `tyche-audit-snapshots` | estimate snapshot audits |

**Not in evening:** demand gate, flatfiles, alpha, publish. Evening `ingest-demand-data` ingests estimates; gate runs the next morning (optional).

Intelligence rollups are computed **in memory from article/filing Parquet** — no SQLite in
cloud jobs. Every 100 tickers, checkpoints flush to
`signals/intelligence/_checkpoints/*.partial.parquet` (crash-safe resume).

Estimate snapshots are written inside `ingest-demand-data` (same as local
`ingest_demand_data.py` — Finnhub consensus rows appended daily per ticker).

### Resources and timeouts (Cloud Run)

All jobs use `--tasks=1` (single container; in-process asyncio only). Full-universe
jobs are slower on GCS than local NVMe — see spec §21 for planned multi-task sharding.

| Job | CPU | Memory | Timeout |
|-----|-----|--------|---------|
| `tyche-ingest-data` | 2 | 4 Gi | 4h |
| `tyche-ingest-options-flatfiles` | 2 | 4 Gi | **8h** |
| `tyche-ingest-demand-data` | 2 | 4 Gi | **8h** |
| `tyche-ingest-news` | 2 | 4 Gi | 4h |
| `tyche-ingest-edgar` | 2 | 4 Gi | 4h |
| `tyche-alpha-batch` | 4 | 8 Gi | **8h** |
| `tyche-run-demand-gate` | 4 | 8 Gi | **8h** |
| `tyche-publish-signals` | 2 | 4 Gi | 2h |
| `tyche-audit-snapshots` | 1 | 2 Gi | 1h |
| `tyche-nightly-pipeline` | 4 | 8 Gi | 8h (fallback) |

Re-apply after editing: `./infra/gcp/deploy_jobs.sh` (rebuild with `--build` when Python changes).

Intelligence jobs export aggregate signals to `signals/intelligence/*.parquet`;
`publish_signals` reads those in GCS mode (no local `news.db` required).

With `TYCHE_DATA_BACKEND=gcs`, the local backend auto-disables APScheduler
(`scheduler_enabled=false`) so laptop jobs do not duplicate Cloud Run.

## Prerequisites

1. **IAM** (see `docs/tyche_gcp_minimal_migration_spec_v2.md` §13):
   - `tyche-jobs@…` → `storage.objectAdmin` on bucket, `secretAccessor`
   - `tyche-workflow@…` → `artifactregistry.writer`, `iam.serviceAccountUser` on `tyche-jobs`, `run.invoker` on each job, **`roles/workflows.invoker` on the project** (Scheduler 403 fix — there is no `gcloud workflows add-iam-policy-binding`):

   ```bash
   ./infra/gcp/fix_workflow_iam.sh
   ```

2. **Secret Manager** secrets (ids must match `gcp_secrets.py`):

   | Secret id | Env var | Needed by |
   |-----------|---------|-----------|
   | `POLYGON_API_KEY` | `TYCHE_POLYGON_API_KEY` | ingest-data, ingest-demand-data, ingest-news |
   | `FINNHUB_API_KEY` | `TYCHE_FINNHUB_API_KEY` | ingest-demand-data, ingest-news |
   | `MASSIVE_S3_ACCESS_KEY` | `TYCHE_MASSIVE_S3_ACCESS_KEY` | ingest-options-flatfiles |
   | `MASSIVE_S3_SECRET_KEY` | `TYCHE_MASSIVE_S3_SECRET_KEY` | ingest-options-flatfiles |
   | `GEMINI_API_KEY` | `TYCHE_GEMINI_API_KEY` | ingest-news, ingest-edgar (classification) |
   | `EDGAR_USER_AGENT_EMAIL` | `TYCHE_EDGAR_USER_AGENT_EMAIL` | ingest-edgar |

   Seed from local `backend/.env` (one-time):

   ```bash
   chmod +x infra/gcp/seed_secrets.sh
   source infra/gcp/config.env
   ./infra/gcp/seed_secrets.sh --from-dotenv
   ```

   Missing secrets log `gcp_secret_fetch_failed` (404) but jobs that do not need
   API keys still run — e.g. `audit-snapshots` only reads GCS.

3. **Artifact Registry** repository:
   ```bash
   gcloud artifacts repositories create tyche \
     --repository-format=docker \
     --location=us-central1 \
     --project=tyche-platform
   ```

## Deploy

```bash
cd /path/to/tyche-options
cp infra/gcp/config.env.example infra/gcp/config.env
# edit config.env

# Build image + create/update all Cloud Run Jobs
chmod +x infra/gcp/deploy_jobs.sh infra/gcp/deploy_workflow.sh infra/gcp/deploy_scheduler.sh
./infra/gcp/deploy_jobs.sh --build   # builds linux/amd64 via buildx (required on Apple Silicon Macs)

# Deploy evening + morning workflows and scheduler triggers (6 PM + 2:30 AM PT)
gcloud services enable workflows.googleapis.com workflowexecutions.googleapis.com
./infra/gcp/deploy_workflow.sh
./infra/gcp/deploy_scheduler.sh
```

Manual one-off jobs (e.g. refresh PL news while pipeline is running):

```bash
gcloud run jobs execute tyche-ingest-news --region=us-central1 --project=tyche-platform --wait
gcloud run jobs execute tyche-ingest-edgar --region=us-central1 --project=tyche-platform --wait
gcloud run jobs execute tyche-publish-signals --region=us-central1 --project=tyche-platform --wait
```

## Apple Silicon Macs

Cloud Run is **linux/amd64**. If you `docker build` without `--platform linux/amd64`,
the image is arm64 and jobs fail instantly with:

```text
Application failed to start: The container may have exited abnormally.
```

`deploy_jobs.sh --build` uses `docker buildx build --platform linux/amd64 --push`.

Local smoke test (amd64 emulation):

```bash
docker buildx build --platform linux/amd64 -f backend/Dockerfile.jobs -t tyche-jobs:amd64 backend
docker run --rm \
  -e TYCHE_DATA_BACKEND=gcs \
  -e TYCHE_GCS_BUCKET=tyche-data-prod \
  -e TYCHE_GCP_PROJECT_ID=tyche-platform \
  -e TYCHE_RUN_ENV=prod \
  -e TYCHE_LOAD_GCP_SECRETS=false \
  tyche-jobs:amd64 audit-snapshots
```

## Manual test

```bash
gcloud run jobs execute tyche-ingest-demand-data \
  --region=us-central1 \
  --project=tyche-platform \
  --wait

# Check manifest
gsutil cat gs://tyche-data-prod/runs/ingest_demand_data/*/manifest.json | tail -1
```

## Local job runner (debug)

```bash
cd backend
export TYCHE_DATA_BACKEND=gcs
export TYCHE_GCS_BUCKET=tyche-data-prod
export TYCHE_GCP_PROJECT_ID=tyche-platform
# Keys from .env locally — do not set TYCHE_LOAD_GCP_SECRETS unless testing SM
.venv/bin/python scripts/run_gcp_job.py audit-snapshots
```

## Disable laptop scheduler

Once cloud jobs are verified, disable APScheduler jobs in Settings (or set
`flatfile_ingest_enabled=false`, `demand_data_enabled=false`, etc.) so your
laptop does not duplicate nightly work.

## Observability (Cloud Logging)

Long-running jobs emit structured **`job_phase`** (step boundaries) and
**`job_progress`** (done/total, pct, ETA) events via structlog. Subprocess
scripts (flatfiles, demand gate) stream stdout line-by-line instead of
buffering until exit.

**Lifecycle**

| Event | When |
|-------|------|
| `gcp_job_start` | Container entry (`run_gcp_job.py`) |
| `job_phase` | Phase start / complete / skip |
| `job_progress` | Loop progress (every N tickers/dates) |
| `gcp_job_complete` | Job finished |

**Useful Logs Explorer queries** (project `tyche-platform`):

```text
resource.type="cloud_run_job"
jsonPayload.event="job_phase"
```

```text
resource.type="cloud_run_job"
jsonPayload.event="job_progress"
```

```text
resource.type="cloud_run_job"
jsonPayload.job="ingest-options-flatfiles"
jsonPayload.phase=("preload_ohlcv" OR "download_dates" OR "iv_extraction")
```

```text
resource.type="cloud_run_job"
jsonPayload.job="alpha-batch"
jsonPayload.phase="build_features"
```

```text
resource.type="cloud_run_job"
jsonPayload.event="gcp_job_start"
```

**Phase map by job**

| Job | Phases |
|-----|--------|
| `ingest-data` | `bootstrap_ohlcv`, `recompute_market_caps` |
| `ingest-options-flatfiles` | `preload_ohlcv`, `download_dates`, `iv_extraction` |
| `ingest-demand-data` | `ingest` + per-source (`fundamentals`, `estimates`, …) |
| `alpha-batch` | `build_features`, `relational_features`, `score_variant` |
| `run-demand-gate` | `build_dataset`, `demand_ablation`, `walk_forward`, `promote_models` |
| `publish-signals` | `stocks_alpha`, `stocks_conviction`, `intelligence`, … |
| `ingest-news` / `ingest-edgar` | `pipeline` + intelligence checkpoint progress |

Run manifests remain at `gs://tyche-data-prod/runs/{job}/{run_id}/manifest.json`
for post-hoc summaries; live progress is in Cloud Logging.
