# Tyche GCP Batch Jobs (GCP-F / GCP-G)

Cloud Run Jobs compute and publish artifacts to `gs://tyche-data-prod/`. The local
backend reads `published/` and `signals/` only (`TYCHE_DATA_BACKEND=gcs`).

**Architecture spec (authoritative):** `docs/tyche_gcp_minimal_migration_spec_v2.md` — job resources (§10), demand gate memory/reuse/OOM (§10.1), schedules (§11), acceptance checklist (§20).

## Architecture

```text
Cloud Scheduler (America/Los_Angeles)
  Mon–Fri 6:00 PM  → tyche-evening-pipeline
  Tue–Sat 2:30 AM  → tyche-morning-pipeline
    → parallel + sequential Cloud Run Jobs (tyche-jobs SA)
      → GCS read/write + Secret Manager API keys
      → runs/{job}/{run_id}/manifest.json
    → publish_signals → published/routes/*.json
```

### Two-window schedule (Pacific)

**Evening Mon–Fri 6:00 PM** — post–market-close same-day OHLCV + demand + news + EDGAR:

| Job | Outputs |
|-----|---------|
| `tyche-ingest-data` | `ohlcv_daily/`, repriced `ticker_meta.parquet` |
| `tyche-ingest-demand-data` | `fundamentals/`, `estimates/`, `estimate_snapshots/`, `short_interest/`, `catalyst_signals/` |
| `tyche-ingest-news` | `news_articles/`, `signals/intelligence/news.parquet` (batched checkpoints) |
| `tyche-ingest-edgar` | `filings_8k/`, `insider_transactions/`, intelligence signal Parquet |

**Morning Tue–Sat 2:30 AM** — prior trading day (Massive flatfile ~2 AM):

| Step | Job | Outputs |
|------|-----|---------|
| Parallel | `tyche-ingest-options-flatfiles` + `tyche-alpha-batch` | options/IV/derived + `alpha_signals*.parquet` |
| Optional | `tyche-run-demand-gate` | `ml/alpha_dataset.parquet`, `ml/alpha_results/`, optional `ml/models/big_move_sustained_*` (~4–8h) |
| Sequential | `tyche-stocks-conviction-batch` | `signals/stocks/conviction.parquet` |
| Sequential | `tyche-stocks-derived-batch` | `signals/stocks/deep_dips.parquet`, `signals/stocks/history_summary.parquet` |
| Sequential | `tyche-candidate-universe-batch` | `signals/universe/options_candidates.parquet`, `signals/universe/csp_scan_tickers.parquet`, `signals/universe/stocks_candidates.parquet` |
| Sequential | `tyche-options-chain-prep-batch` | `signals/options/options_chain_contracts.parquet` (from flatfiles) |
| Sequential | `tyche-options-scanner-batch` | `signals/options/scanner.parquet` (CSP scan over `csp_scan_tickers`) |
| Sequential | `tyche-publish-signals` | `published/routes/*.json` |
| Sequential | `tyche-audit-snapshots` | estimate snapshot audits |

**Not in evening:** demand gate, flatfiles, alpha, publish. Evening `ingest-demand-data` ingests estimates; gate runs the next morning (optional).

### Morning SLA (target vs current)

**Product target:** prior-session computed signals in `published/routes/*.json` **ready by ~7 AM PT**
so the laptop/Cloud Run app can load scanner, conviction, stocks, and alpha without inline scans.

| Milestone | Target (PT) | Typical / observed |
|-----------|-------------|----------------------|
| Massive options flatfile available | ~2:00 AM | Vendor-dependent |
| Scheduler fires `tyche-morning-pipeline` | 2:30 AM | ✓ |
| Flatfile ingest complete | ~6:30 AM | ✓ (often ~4h after start) |
| **Publish (UI-ready JSON)** | **~7:00 AM** | **~12:30 PM** (2026-06-27) ✗ |

**Why publish is late:** `tyche-run-demand-gate` is wired **sequentially after** flatfiles+alpha
and **blocks** stocks batches, options chain/scanner, and publish until it finishes (~4–8h;
~5h observed). The UI path does **not** need gate output — publish uses alpha-batch + stocks
conviction + options scanner artifacts, not gate-promoted `big_move_sustained_*` models.

**Recommended workflow v2 (TODO — not implemented):**

1. **Decouple gate from UI path** — run `tyche-run-demand-gate` in parallel (evening branch or
   fire-and-forget second workflow) so conviction → scanner → publish starts when flatfiles
   complete (~6:30 AM PT).
2. **Evening stocks batches** — run `tyche-stocks-conviction-batch` + `tyche-stocks-derived-batch`
   after evening OHLCV ingest (6 PM PT) so morning only waits on flatfiles + options chain.
3. **`TYCHE_DEMAND_GATE_REUSE_DATASET=true`** — skip ~90 min dataset rebuild when
   `ml/alpha_dataset.parquet` exists on GCS.

See `docs/alpha/cloud_signals_slice67_completion_note.md` and spec §10.

**Manual options-only slice** (no flatfiles/gate): `tyche-options-morning-slice` — use when
re-running scanner after conviction/universe/chain artifacts already exist.

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
| Evening ingest (4 jobs) | 2 | 4 GiB | 8h |
| `tyche-alpha-batch` | 4 | 8 GiB | 8h |
| `tyche-stocks-conviction-batch` | 4 | 8 GiB | 8h |
| `tyche-stocks-derived-batch` | 4 | 8 GiB | 8h |
| `tyche-candidate-universe-batch` | 2 | 4 GiB | 8h |
| `tyche-options-snapshot-batch` | 2 | 4 GiB | 8h |
| `tyche-run-demand-gate` | **8** | **32 GiB** | 8h |
| publish / audit / nightly | 1–4 | 2–8 GiB | 8h |

Full table: spec §10 and `deploy_jobs.sh`.

`ingest-data` previously used 4h and timed out on GCS cap reprice (~3.5k tickers).
All jobs now use `28800s` (8h). Cloud Run max is 168h.

**Workflow vs job timeout:** Cloud Run Jobs can run 8h, but the old
`googleapis.run.v2.projects.locations.jobs.run` connector defaults to a **30-minute**
LRO wait — workflows failed at ~30m while jobs kept running. Evening/morning YAMLs now
`http.post` the `:run` endpoint (non-blocking) and poll `executions.get` every 45s.
Re-deploy workflows after editing: `./infra/gcp/deploy_workflow.sh`.

**Ingest session dates (Pacific):** evening jobs set `TYCHE_INGEST_WINDOW=evening`
(Pacific today); morning jobs set `morning` (Pacific yesterday). Prevents UTC
containers from fetching the wrong Polygon session day.

Re-apply after editing: `./infra/gcp/deploy_jobs.sh` (rebuild with `--build` when Python changes).

**Demand gate memory / reuse / OOM:** spec **§10.1** (build ~16 GiB, walk-forward **32 GiB**, `TYCHE_DEMAND_GATE_REUSE_DATASET`, exit -9 diagnosis).

`deploy_jobs.sh --build` runs **ruff F821/F822/F823** (undefined names only — not unused-import
F401) and Cloud Run job unit tests (`test_alpha_batch`, `test_gcp_jobs`, `test_ingest_dates`,
`test_cloud_stocks_conviction`, `test_cloud_stocks_derived`) before pushing the image.

Intelligence jobs export aggregate signals to `signals/intelligence/*.parquet`;
`publish_signals` reads those in GCS mode (no local `news.db` required).

With `TYCHE_DATA_BACKEND=gcs`, the local backend auto-disables APScheduler
(`scheduler_enabled=false`) so laptop jobs do not duplicate Cloud Run.

### Local backend (GCS read mode)

Point the laptop API at cloud artifacts — no Cloud Run redeploy needed for read-path fixes:

```bash
# backend/.env or shell
TYCHE_DATA_BACKEND=gcs
TYCHE_GCS_BUCKET=tyche-data-prod
# ADC: gcloud auth application-default login
```

Restart the backend after Python changes (`./scripts/stop-backend.sh && ./scripts/start-backend.sh`).
The API reads `published/routes/*.json` and `signals/` from GCS; Tradier/Gemini still hit live APIs.

**Deploy vs restart:** UI/API fixes in `published_routes.py`, route handlers, etc. → restart only.
Batch ingest/publish logic changes → `./infra/gcp/deploy_jobs.sh --build` + re-run the job.
Optional: `gcloud run jobs execute tyche-publish-signals --wait` to rewrite published JSON with
clean `null` (not required if read sanitization is deployed locally).

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

**Options morning slice** (sequential — conviction → universe → chain prep → scanner → publish):

```bash
gcloud workflows run tyche-options-morning-slice \
  --location=us-central1 --project=tyche-platform
```

Deploy the workflow first: `./infra/gcp/deploy_workflow.sh`. Poll progress in Cloud Console → Workflows, or:

```bash
gcloud workflows executions list tyche-options-morning-slice \
  --location=us-central1 --project=tyche-platform --limit=1
```

Expect ~30–90 minutes end-to-end depending on conviction batch size. Run after evening ingest completes and `deploy_jobs.sh --build` picks up the latest job image.

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
| `stocks-conviction-batch` | `execute` → `signals/stocks/conviction.parquet` |
| `stocks-derived-batch` | deep dips + `history_summary` export |
| `candidate-universe-batch` | metadata-first options/stocks candidate universes |
| `options-snapshot-batch` | Tradier chain fetch for candidate tickers |
| `publish-signals` | `stocks_alpha`, `stocks_conviction`, `stocks_deep_dips`, `stocks_history`, `intelligence`, … |
| `ingest-news` / `ingest-edgar` | `pipeline` + intelligence checkpoint progress |

Run manifests remain at `gs://tyche-data-prod/runs/{job}/{run_id}/manifest.json`
for post-hoc summaries; live progress is in Cloud Logging.
