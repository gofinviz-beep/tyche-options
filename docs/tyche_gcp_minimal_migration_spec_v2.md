# Tyche GCP Minimal Migration Spec v2 — Precomputed Signals Architecture

Audience: Cursor Composer 2.5 Fast operating in `tyche-options-main`.

Purpose: achieve **cloud-computed, locally-viewed** Tyche — not merely lift-and-shift Parquet to GCS. GCP scheduled jobs ingest, compute, downsample, and publish compact artifacts; the local backend/frontend read only `signals/` and `published/` outputs. Normal UI routes must not scan `raw/` or `curated/` data. Backend/frontend stay local for now; BigQuery and Cloud Run **service** migration are out of scope for minimal v1.

This is an infrastructure spec, separate from the Multi-Bagger Discovery Engine spec.

**Operational runbook:** `infra/gcp/README.md`

---

## 0.1 Implementation status (June 2026)

| Phase | Status | Notes |
|-------|--------|-------|
| GCP-A Storage abstraction | **Done** | `tyche/storage/` — `local` \| `gcs` via `StoreBackend`; `json_io.sanitize_for_json` (NaN → `null`) |
| GCP-B Store migrations | **Done** | OHLCV, options, demand, news, filings, alpha stores GCS-aware |
| GCP-C Publisher | **Done** | `workflow/publish_signals.py` → `published/routes/*.json`; intelligence rows sanitized before write |
| GCP-D Route repositories | **Partial** | See **Route coverage** below — alpha + intelligence wired; options pages still live-compute |
| GCP-E GCS migration script | **Done** | `scripts/migrate_data_to_gcs.py` |
| GCP-F Cloud Run Jobs | **Done** | 10 jobs in `infra/gcp/deploy_jobs.sh`; 8h timeouts; `deploy_jobs.sh --build` pre-flight (ruff F821 + job unit tests) |
| GCP-G Workflows + Scheduler | **Done** | Evening (6 PM PT) + morning (2:30 AM PT); **non-blocking** `:run` + poll (not 30m LRO `jobs.run`) |
| GCP-H Local backend → GCS | **Partial** | `TYCHE_DATA_BACKEND=gcs` + ADC works for alpha + intelligence; options/scanner still Tradier; see §20 |

**Flat GCS layout (current):** production uses the same relative paths as `backend/data/` at the bucket root (e.g. `ohlcv_daily/`, `signals/intelligence/`, `published/routes/`) — not the `raw/`/`curated/` prefix tree in §3 (future normalization optional).

**Intelligence in cloud:** rollups computed in memory from article/filing Parquet → `signals/intelligence/*.parquet`. No `news.db` in Cloud Run jobs. Batched checkpoints every 100 tickers under `signals/intelligence/_checkpoints/`.

**Demand manifest fields:** `guidance_tickers_fetched` (Benzinga returned records) vs `guidance_catalysts_written` (raise/cut rows persisted). `guidance` mirrors `guidance_catalysts_written`.

**Demand gate schedule:** `tyche-run-demand-gate` is **morning only** (not evening). Evening runs `tyche-ingest-demand-data` (estimates/fundamentals ingest); gate runs **after** that data lands, in parallel with flatfiles+alpha, then before publish. Optional — publish proceeds if gate fails.

**Pacific ingest session dates:** `market_data/ingest_dates.py` + per-job `TYCHE_INGEST_WINDOW=evening|morning` in `deploy_jobs.sh`. Evening → Pacific **today**; morning → Pacific **yesterday**. Region-independent (UTC Cloud Run, any GCP region, local laptop).

**Job observability:** `tyche/ops/job_progress.py` emits `job_phase` / `job_progress` to Cloud Logging; subprocess jobs stream stdout (`gcp_jobs._run_subprocess`). See `infra/gcp/README.md` § Observability.

**Local `ingest_data.py`:** passes `storage_context_from_settings()` to `OHLCVStore` / `TickerMetaStore` / `IntradayStore`. `IntradayStore` and `OptionsChainStore` use `context_for_data_access()` for `_MetadataCache` — same pattern as OHLCV; cloud batch jobs do not use `IntradayStore`.

**Published JSON NaN (June 2026 fix):** intelligence Parquet missing datetimes (`last_positive_at`, etc.) become `nan` in dict rows. `write_json` now sanitizes + `allow_nan=False`; `published_routes` sanitizes on read for legacy GCS artifacts. Local backend restart suffices; cloud job redeploy only needed to change batch publish code.

### Route coverage (GCP-D detail)

| Route / page | Published read | Signals fallback | Live compute fallback | Cloud publish status |
|--------------|----------------|------------------|----------------------|----------------------|
| `/stocks/alpha/` | ✅ `get_stock_alpha_scan` | ✅ alpha Parquet | recompute API | ✅ `stocks_alpha.json` |
| `/intelligence/news` | ✅ `get_intelligence_news_rows` | ✅ `signals/intelligence/news.parquet` | `news.db` / rebuild (local) | ✅ |
| `/intelligence/filings` | ✅ `get_intelligence_filing_rows` | ✅ filings Parquet | local DB (local) | ✅ |
| `/stocks/conviction` | ✅ `get_stocks_conviction_rows` | — | ✅ local `conviction.db` | ⚠️ empty on cloud (no SQLite in jobs) |
| `/options/scanner` | ❌ | ❌ | ✅ Tradier morning scan | placeholder JSON only |
| `/options/conviction` | ❌ | ❌ | ✅ live engine | placeholder |
| `/stocks/deep-dips` | ❌ | ❌ | ✅ live engine | placeholder |
| Options monitor / explore / CC | ❌ | ❌ | ✅ live | placeholders |

**Implication:** GCS-mode laptop can serve Alpha + Intelligence from published artifacts. Options Conviction, Scanner, and Stocks Conviction (without local `conviction.db` refresh) still need live compute or local DB — not yet full cloud-computed UI.

---

## 0. North-star design

Tyche should become a **cloud-computed, locally-viewed** application first.

```text
GCP jobs:
  ingest raw data
  update curated Parquet
  compute features
  compute page-specific signals/snapshots
  publish compact artifacts

Local backend/frontend:
  read compact precomputed artifacts from GCS
  serve UI quickly
  avoid scanning raw 10-year OHLCV/options files on every page
```

Later:

```text
Cloud Run backend:
  reads the same compact artifacts
  stays fast because heavy work is already precomputed

Cloud Run/frontend/static hosting:
  reads API responses or published JSON snapshots
```

---

## 1. Non-negotiable requirements

1. Data moves from `backend/data/` on laptop to GCS.
2. Scheduled jobs run inside GCP, not on the laptop.
3. GCP jobs update Parquet files and publish compact derived snapshots.
4. Backend/frontend may continue running on laptop initially.
5. Local backend must point to GCS and read only required precomputed outputs for each page.
6. Raw/curated data should remain Parquet in GCS.
7. Do not migrate to BigQuery in minimal v1.
8. Do not force the local app to download or scan the full GCS data lake.
9. Every scheduled job writes a run manifest.
10. Every page should have a documented artifact contract.
11. Cloud Run service migration is a later phase, not required for minimal v1.
12. Realtime/near-realtime should follow the same pattern: cloud jobs compute lagged snapshots; app reads compact snapshots.
13. Authentication uses workload identity and Application Default Credentials — never committed service-account JSON keys or `.env` secrets.
14. Cloud Run Jobs run as `tyche-jobs@tyche-platform.iam.gserviceaccount.com`; do not use the App Engine default service account for new jobs.

---

## 2. Target minimal GCP architecture

Use:

- Google Cloud Storage: canonical Parquet data lake and compact published artifacts.
- Cloud Run Jobs: scheduled batch scripts.
- Cloud Scheduler: cron triggers for Cloud Run Jobs.
- Secret Manager: API keys.
- Artifact Registry: job container images.
- Cloud Logging: logs.
- Optional later: Pub/Sub for event fanout.
- Optional later: Cloud Run Service for backend API.
- Optional later: Cloud CDN / Cloud Storage static hosting for published JSON snapshots.
- Optional later: BigQuery only if SQL analytics becomes necessary.

Existing service accounts (see §13):

- `tyche-jobs@tyche-platform.iam.gserviceaccount.com` — Cloud Run Jobs runtime
- `tyche-workflow@tyche-platform.iam.gserviceaccount.com` — deployment / CI orchestration
- `tyche-ui@tyche-platform.iam.gserviceaccount.com` — future read-only Cloud Run API

Do not use BigQuery for the first migration.
Do not use the App Engine default service account for new batch jobs.

---

## 3. GCS bucket layout

Use one primary bucket per environment:

```text
gs://tyche-data-{env}/
```

Recommended layout:

```text
raw/
  ohlcv/
  options_flatfiles/
  vendor_payloads/
    massive/
    finnhub/
    benzinga/
    edgar/

curated/
  prices/
  options_history/
  options_iv/
  fundamentals/
  estimates/
  estimate_snapshots/
  guidance/
  catalysts/
  short_interest/
  filings/
  ticker_meta.parquet

features/
  latest_features.parquet
  alpha_dataset.parquet
  discovery_dataset.parquet
  options_features.parquet
  stock_features.parquet

signals/
  alpha/
    alpha_signals.parquet
    alpha_signals_sustained.parquet
    latest_top.parquet
  discovery/
    discovery_signals.parquet
    latest_top.parquet
  options/
    scanner.parquet
    conviction.parquet
    explore.parquet
    monitor.parquet
    covered_calls.parquet
  stocks/
    conviction.parquet
    deep_dips.parquet
    history_summary.parquet
  intelligence/
    news.parquet
    filings.parquet
    insider.parquet

published/
  manifest.json
  routes/
    options.json
    options_scanner.json
    options_conviction.json
    options_explore.json
    options_monitor.json
    options_covered_calls.json
    stocks.json
    stocks_alpha.json
    stocks_conviction.json
    stocks_deep_dips.json
    stocks_history.json
    intelligence.json
    intelligence_news.json
    intelligence_filings.json
    intelligence_insider.json
  route_manifests/

models/
  conservative/
  discovery/

reports/
  funnel_audits/
  missed_winners/
  estimate_snapshot_audits/
  demand_gate/
  job_health/

runs/
  ingest_data/
  ingest_options_flatfiles/
  ingest_demand_data/
  run_demand_gate/
  alpha_batch/
  publish_signals/
  logs/
  manifests/
  locks/
```

Principle:

```text
raw/ and curated/ are for compute jobs.
signals/ are compact Parquet contracts for backend/API use.
published/ are ultra-compact route-level JSON contracts for UI/API cache use.
```

---

## 4. Page-to-artifact contract

Each page must read from compact precomputed artifacts, not raw/curated source files.

### Options pages

| Route | Primary artifact | Notes |
|---|---|---|
| `/options` | `published/routes/options.json` and/or `signals/options/monitor.parquet` | Overview counts, watchlist, summary cards. |
| `/options/scanner` | `signals/options/scanner.parquet` or `published/routes/options_scanner.json` | Precomputed scanner rows with filters already materialized. |
| `/options/conviction` | `signals/options/conviction.parquet` | Ranked conviction candidates. |
| `/options/explore` | `signals/options/explore.parquet` | Exploration dataset, reduced columns only. |
| `/options/monitor` | `signals/options/monitor.parquet` | Positions/watchlist/alerts. |
| `/options/covered-calls` | `signals/options/covered_calls.parquet` | Precomputed covered-call candidates. |

### Stock pages

| Route | Primary artifact | Notes |
|---|---|---|
| `/stocks/` | `published/routes/stocks.json` and/or `signals/stocks/history_summary.parquet` | Summary, universe stats, top movers. |
| `/stocks/alpha/` | `signals/alpha/alpha_signals_sustained.parquet` plus `published/routes/stocks_alpha.json` | Conservative alpha page. |
| `/stocks/conviction` | `signals/stocks/conviction.parquet` | Precomputed conviction rows. |
| `/stocks/deep-dips` | `signals/stocks/deep_dips.parquet` | Precomputed deep-dip candidates. |
| `/stocks/history` | `signals/stocks/history_summary.parquet` | Compact historical summaries, not raw OHLCV. |

### Intelligence pages

| Route | Primary artifact | Notes |
|---|---|---|
| `/intelligence` | `published/routes/intelligence.json` | Summary counts, latest evidence, alerts. |
| `/intelligence/news` | `signals/intelligence/news.parquet` or `published/routes/intelligence_news.json` | Classified and scored news, compact only. |
| `/intelligence/filings` | `signals/intelligence/filings.parquet` | Classified filings and material events. |
| `/intelligence/insider` | `signals/intelligence/insider.parquet` | Form 4 / insider summaries. |

Rule:

```text
If a page needs data, there must be a corresponding signals/ or published/routes artifact.
If no artifact exists, add a publisher job. Do not let the route scan raw/curated files directly.
```

---

## 5. Raw, curated, signal, and published layers

### Raw layer

Preserve vendor data and raw ingested files. Do not use raw layer directly from UI routes.

### Curated layer

Normalized Parquet stores used by compute jobs. Local backend should avoid reading them during normal page loads.

### Features/signals layer

Compact computed datasets. Local backend may read these.

### Published layer

Very small JSON or slim Parquet files optimized for route responses. Local backend should prefer `published/routes/*.json` when available.

---

## 6. Backend data-access rule

API route handlers must not read `raw/` or full `curated/` datasets unless explicitly marked as admin/debug routes.

Normal UI routes must read from `signals/` or `published/`.

Implementation:

- Create route-level repository functions:
  - `get_options_scanner_rows()`
  - `get_stock_alpha_rows()`
  - `get_intelligence_news_rows()`
- These should read `published/` first, then `signals/`.
- Curated fallback should be disabled by default.

Add config:

```python
api_prefer_published_signals: bool = True
api_allow_curated_fallback: bool = False
published_max_age_minutes: int = 180
```

---

## 7. Required config

File:

```text
backend/src/tyche/config.py
```

Add or ensure:

```python
data_backend: str = "local"  # local | gcs
data_root: str = "data"
gcs_bucket: str | None = None
gcs_prefix: str = ""
gcp_project_id: str | None = None
run_env: str = "dev"

api_prefer_published_signals: bool = True
api_allow_curated_fallback: bool = False
published_max_age_minutes: int = 180
```

Local mode:

```text
TYCHE_DATA_BACKEND=local
TYCHE_DATA_ROOT=data
```

GCS mode:

```text
TYCHE_DATA_BACKEND=gcs
TYCHE_GCS_BUCKET=tyche-data-prod
TYCHE_GCP_PROJECT_ID=...
TYCHE_API_PREFER_PUBLISHED_SIGNALS=true
TYCHE_API_ALLOW_CURATED_FALLBACK=false
```

---

## 8. Storage abstraction

Create:

```text
backend/src/tyche/storage/paths.py
backend/src/tyche/storage/parquet_io.py
backend/src/tyche/storage/json_io.py
```

Functions:

```python
resolve_data_path(relative_path: str) -> str | Path
is_gcs_path(path: str | Path) -> bool
join_uri(*parts: str) -> str

read_parquet(path_or_relative: str) -> pd.DataFrame
write_parquet(df: pd.DataFrame, path_or_relative: str, *, atomic: bool = True) -> None
exists(path_or_relative: str) -> bool
list_files(prefix_or_relative: str, suffix: str | None = None) -> list[str]

read_json(path_or_relative: str) -> dict | list
write_json(obj: Any, path_or_relative: str, *, atomic: bool = True) -> None
```

Use `fsspec/gcsfs` for `gs://` support.

Rules:

- Keep local mode working.
- GCS mode authenticates via Application Default Credentials (`gcloud auth application-default login` locally; workload identity in Cloud Run Jobs).
- Never require or commit a service-account JSON credentials file.
- Use temp path + promote for canonical writes.
- Avoid concurrent writes to the same object.
- Do not assume POSIX rename semantics on GCS.
- **`write_json` must sanitize Parquet NaN/NA → `null`** (`json_io.sanitize_for_json`, `allow_nan=False`). Intelligence rollups can leave missing datetimes as `nan` in dict rows — without sanitization, published JSON gets non-standard `NaN` tokens and Pydantic 500s on read.

Commit:

```text
storage: add local and GCS IO abstraction
```

---

## 9. Publisher layer

Add a publisher layer that produces page-level artifacts.

Create:

```text
backend/src/tyche/workflow/publish_signals.py
backend/scripts/publish_signals.py
```

Inputs:

```text
signals/
features/
reports/
```

Outputs:

```text
published/routes/*.json
published/manifest.json
published/route_manifests/*.json
```

Responsibilities:

1. Downsample heavy Parquet to route-specific JSON.
2. Keep only columns required by the page.
3. Limit rows for initial page load.
4. Include `as_of`, `run_id`, `row_count`, `source_paths`, and `generated_at`.
5. Write route manifests.
6. Validate freshness.
7. Fail loudly if a required upstream artifact is missing.
8. Sanitize intelligence Parquet rows (`sanitize_json_records`) before embedding in route JSON.

Example route artifact:

```json
{
  "route": "/stocks/alpha/",
  "as_of": "2026-06-09",
  "generated_at": "2026-06-09T03:10:00Z",
  "row_count": 200,
  "source_paths": ["signals/alpha/alpha_signals_sustained.parquet"],
  "data": []
}
```

Rules:

- Published JSON is cache/serving layer, not canonical source.
- Canonical source remains Parquet in `signals/` and `curated/`.
- If JSON grows too large, publish slim Parquet plus JSON manifest.

Commit:

```text
publish: add route-level signal publisher
```

---

## 10. Scheduled GCP jobs

Use **Cloud Workflows** to orchestrate **Cloud Run Jobs** (triggered by Cloud Scheduler).

### Cloud Run Jobs (10)

| Job | Entry | CPU / Mem | Timeout |
|-----|-------|-----------|---------|
| All 10 jobs | (see `deploy_jobs.sh`) | varies | **8h** (`28800s`) |

`ingest-data` exceeded the prior 4h limit on GCS (OHLCV bootstrap + per-ticker cap
reprice). Uniform 8h avoids premature task termination until §21 multi-task sharding.

All jobs run `--tasks=1` (single container). In-process `asyncio` concurrency only — see §21.

Entry point: `backend/scripts/run_gcp_job.py` → `tyche/ops/gcp_jobs.py`. Runtime SA: `tyche-jobs@tyche-platform.iam.gserviceaccount.com`.

### Job outputs (flat bucket paths)

| Job | Outputs |
|---|---|
| `tyche-ingest-data` | `ohlcv_daily/`, repriced `ticker_meta.parquet` |
| `tyche-ingest-options-flatfiles` | `options_history/`, `options_iv/`, `derived/` |
| `tyche-ingest-demand-data` | `fundamentals/`, `estimates/`, `estimate_snapshots/`, `short_interest/`, `catalyst_signals/` (Benzinga guidance → D-CAT) |
| `tyche-ingest-news` | `news_articles/`, `signals/intelligence/news.parquet` |
| `tyche-ingest-edgar` | `filings_8k/`, `insider_transactions/`, `signals/intelligence/filings.parquet`, `insider.parquet` |
| `tyche-alpha-batch` | `alpha_signals.parquet`, `alpha_signals_sustained.parquet` |
| `tyche-run-demand-gate` | `ml/alpha_results/demand_gate_verdict.json`, optional `ml/models/big_move_sustained_*.json` |
| `tyche-publish-signals` | `published/routes/*.json`, `published/manifest.json` |
| `tyche-audit-snapshots` | `reports/estimate_snapshot_audits/`, `reports/job_health/` |

---

## 11. Recommended schedule

Timezone: `America/Los_Angeles`. **Tue–Sat** processes the previous trading session.

### Two-window model (implemented)

Cloud Workflows run **parallel** evening jobs and **parallel-then-sequential** morning jobs.

**Evening 6:00 PM PT** — `tyche-evening-pipeline` (`infra/gcp/workflows/evening-pipeline.yaml`):

| Parallel branch | Job |
|-----------------|-----|
| OHLCV + cap reprice | `tyche-ingest-data` |
| Finnhub + Benzinga + SI | `tyche-ingest-demand-data` |
| News classify + rollup | `tyche-ingest-news` |
| EDGAR + insider + rollup | `tyche-ingest-edgar` |

Sources available after market close: Polygon grouped daily, Finnhub estimates, Benzinga guidance (`GET /benzinga/v1/guidance` via Massive key), news, EDGAR.

**Not in evening:** `tyche-run-demand-gate`, `tyche-ingest-options-flatfiles`, `tyche-alpha-batch`, `tyche-publish-signals`, `tyche-audit-snapshots`. Evening is ingest-only (four parallel branches above).

**Ingest session dates:** Cloud Run jobs set `TYCHE_INGEST_WINDOW=evening|morning`. `market_data/ingest_dates.py` resolves end dates in `America/Los_Angeles` (evening → Pacific today, morning → yesterday) — independent of container UTC or laptop host timezone.

**Morning 2:30 AM PT** — `tyche-morning-pipeline` (`infra/gcp/workflows/morning-pipeline.yaml`):

| Step | Jobs |
|------|------|
| Parallel | `tyche-ingest-options-flatfiles` + `tyche-alpha-batch` |
| Optional | `tyche-run-demand-gate` (failure does not block publish) |
| Sequential | `tyche-publish-signals` → `tyche-audit-snapshots` |

Massive options flatfiles land ~2 AM PT; morning window starts at 2:30 AM.

### Demand gate vs publish (morning)

| Job | Blocks UI? | Depends on |
|-----|------------|------------|
| `tyche-alpha-batch` | **Yes** — Alpha page reads `alpha_signals*.parquet` | OHLCV + demand stores (evening ingest) |
| `tyche-publish-signals` | **Yes** — frontend reads `published/routes/*.json` | Alpha batch (+ conviction/intelligence artifacts) |
| `tyche-run-demand-gate` | **No** — retrains optional `big_move_sustained_*` XGBoost models | Fresh evening demand data; builds/reuses `ml/alpha_dataset.parquet` |
| `tyche-ingest-options-flatfiles` | **No** for Alpha/publish — IV/options history only | Massive S3 flat file (~2 AM) |

Gate runs **after** evening `ingest-demand-data` (estimates/fundamentals) and **after** the parallel flatfiles+alpha step in the workflow YAML — so it sees fresh demand Parquet and can run while alpha artifacts already exist. Typical cloud runtime **4–8h** (dataset build + 6 walk-forward runs + optional promotion). Manual recovery: alpha done → gate (optional) → publish.

### Schedule rationale

- **Wall-clock:** evening jobs overlap (~3h demand + parallel news/EDGAR/OHLCV) instead of one 4h+ sequential chain.
- **Laptop:** with `TYCHE_DATA_BACKEND=gcs`, `scheduler_enabled=false` (default) — APScheduler does not duplicate cloud work.
- **Publish:** runs only after morning compute; fails loudly if required upstream artifacts are missing.
- **Local CLI parity:** `scripts/ingest_data.py`, `ingest_demand_data.py`, `ingest_options_flatfiles.py`, `run_demand_gate.py` work on the laptop against `backend/data/` (`local`) or GCS (`gcs` + ADC) — independent of Cloud Run.

### Workflow gotchas

Parallel branches must use **unique step names** (e.g. `run_ingest_data`, not four copies of `run`) — Cloud Workflows rejects duplicate names in a `parallel` block.

**Do not use blocking `googleapis.run.v2.projects.locations.jobs.run`.** The connector LRO defaults to **30 minutes** (`Timeout of 1800 seconds exceeded`) while jobs run 3–8h on GCS. Implemented YAMLs use `http.post` to `:run` (returns immediately) + poll `executions.get` every 45s. Re-deploy: `./infra/gcp/deploy_workflow.sh`.

---

## 12. Run manifests

Every scheduled job writes:

```text
runs/{job_name}/{run_id}/manifest.json
```

Required fields:

```json
{
  "run_id": "...",
  "job_name": "...",
  "started_at": "...",
  "ended_at": "...",
  "status": "success|failed",
  "git_sha": "...",
  "data_backend": "gcs",
  "input_paths": [],
  "output_paths": [],
  "published_paths": [],
  "tickers_requested": 0,
  "tickers_succeeded": 0,
  "tickers_failed": 0,
  "warnings": [],
  "errors": []
}
```

Add helper:

```text
backend/src/tyche/ops/run_manifest.py
```

Commit:

```text
ops: add run manifest helper
```

---

## 13. Identity, credentials, and IAM

### Service accounts (existing — do not create new keys)

| Account | Email | Role |
|---|---|---|
| Runtime (Cloud Run Jobs) | `tyche-jobs@tyche-platform.iam.gserviceaccount.com` | Executes batch scripts; reads/writes GCS; reads Secret Manager |
| Deployment / orchestration | `tyche-workflow@tyche-platform.iam.gserviceaccount.com` | Builds/pushes images; deploys Cloud Run Jobs; may invoke jobs as `tyche-jobs` |
| Future read-only UI/API | `tyche-ui@tyche-platform.iam.gserviceaccount.com` | Reserved for later Cloud Run service — read `signals/` and `published/` only |

### Required IAM bindings

| Principal | Role / binding | Purpose |
|---|---|---|
| `tyche-jobs@tyche-platform.iam.gserviceaccount.com` | `roles/storage.objectAdmin` on `gs://tyche-data-{env}` | Read/write Parquet, signals, published artifacts, run manifests |
| `tyche-jobs@tyche-platform.iam.gserviceaccount.com` | `roles/secretmanager.secretAccessor` (existing) | API keys at runtime |
| `tyche-jobs@tyche-platform.iam.gserviceaccount.com` | `roles/logging.logWriter` | Cloud Logging |
| `tyche-workflow@tyche-platform.iam.gserviceaccount.com` | `roles/iam.serviceAccountUser` on `tyche-jobs` | Deploy/run jobs that execute as `tyche-jobs` |
| `tyche-workflow@tyche-platform.iam.gserviceaccount.com` | `roles/artifactregistry.writer` | Build and push job container images |
| Deploying principal (human or CI) | `roles/cloudscheduler.admin` | Create/update Cloud Scheduler triggers only on the principal that owns deployment |

### Credentials policy

- **Never commit** service-account JSON keys, `.env` secrets, or credential files to the repo.
- **Do not require** a downloaded service-account JSON for local development or Cloud Run Jobs.
- **Do not use** the App Engine default service account (`{project-number}@appspot.gserviceaccount.com`) for new jobs — it typically carries broad Editor permissions.

**Local development** — Application Default Credentials:

```bash
gcloud auth application-default login
```

**Cloud Run Jobs** — set runtime service account on each job definition:

```text
tyche-jobs@tyche-platform.iam.gserviceaccount.com
```

**Deployment commands** — run as an authenticated `gcloud` user, or later as `tyche-workflow` if CI/CD is added. No key file mount.

---

## 14. Local backend reading from GCS

Local command:

```bash
gcloud auth application-default login
export TYCHE_DATA_BACKEND=gcs
export TYCHE_GCS_BUCKET=tyche-data-prod
export TYCHE_GCP_PROJECT_ID=tyche-platform
export TYCHE_API_PREFER_PUBLISHED_SIGNALS=true
export TYCHE_API_ALLOW_CURATED_FALLBACK=false
cd backend
uvicorn tyche.api.main:app --reload
```

Rules:

- Use Application Default Credentials locally (no service-account JSON).
- Do not require local copy of `backend/data/`.
- Normal page load reads `published/routes/*.json` or `signals/*.parquet`.
- Route handlers must not scan `curated/prices/` or `curated/options_history/`.

Acceptance:

- `/stocks/alpha/` reads `published/routes/stocks_alpha.json` or `signals/alpha/alpha_signals_sustained.parquet`.
- `/options/scanner` reads `published/routes/options_scanner.json` or `signals/options/scanner.parquet`.
- `/intelligence/news` reads `published/routes/intelligence_news.json` or `signals/intelligence/news.parquet`.
- Local UI remains responsive.

---

## 15. One-time data migration

Add:

```text
backend/scripts/migrate_data_to_gcs.py
```

Args:

```text
--local-data-root data
--gcs-uri gs://tyche-data-prod
--dry-run
--include raw,curated,features,signals,models,reports
--delete-extra false
```

Behavior:

- Upload current `backend/data/` to GCS.
- Preserve relative paths.
- Skip temp/cache files.
- Generate manifest under `runs/migration/{timestamp}/manifest.json`.

Acceptance:

- Dry run shows file count and bytes.
- Real run uploads.
- Random Parquet readback succeeds.

---

## 16. Container and deployment

Add:

```text
backend/Dockerfile.jobs
infra/gcp/
```

Docker requirements:

- Python version matching repo.
- Cloud dependencies:
  - `gcsfs`
  - `fsspec`
  - `google-cloud-storage`
  - `google-cloud-secret-manager`
- No service account JSON key mounted in the container.
- Cloud Run Job uses workload identity: `tyche-jobs@tyche-platform.iam.gserviceaccount.com` (see §13).

Each Cloud Run Job definition must set:

```yaml
serviceAccountName: tyche-jobs@tyche-platform.iam.gserviceaccount.com
```

Deployment is performed by an authenticated `gcloud` user or, when CI/CD exists, `tyche-workflow@tyche-platform.iam.gserviceaccount.com` with `roles/iam.serviceAccountUser` on `tyche-jobs`.

Secrets (Secret Manager — accessed at runtime by `tyche-jobs`):

```text
FINNHUB_API_KEY
MASSIVE_API_KEY
POLYGON_API_KEY if still used
BENZINGA_API_KEY if separate
LLM gateway / OPENAI key if used
```

---

## 17. Near-realtime extension path

Do not build full realtime in minimal v1.

Design now so it can be added later:

```text
more frequent Cloud Run Jobs
  -> update raw/curated recent data
  -> compute rolling signals
  -> publish route snapshots
  -> local/cloud app reads published snapshots
```

Possible future schedules:

```text
quotes snapshot every 5-15 min
options scanner every 15-30 min
news/filings every 5-15 min
publish_signals after each compute group
```

Rule:

```text
The app still reads compact published artifacts. It does not become a raw-data compute engine.
```

---

## 18. Non-goals for minimal v1

Do not do these yet:

- BigQuery migration.
- Full Cloud Run backend API service.
- Frontend hosting migration.
- CDN for raw Parquet.
- Kubernetes.
- Pub/Sub fanout.
- Full realtime streaming.
- Complex distributed locking.

---

## 19. Cursor work packets

**Start here:** GCP-A only. Do not begin store migrations, publishers, or Cloud Run deployment until the storage abstraction is merged and tested in local mode.

### GCP-A — Storage abstraction (first)

```text
Implement storage abstraction only.
Add local|gcs config, paths.py, parquet_io.py, json_io.py.
Keep local behavior working.
Do not change alpha/model/scoring logic.
Do not require or commit service-account JSON credentials.
Local GCS testing uses: gcloud auth application-default login
```

### GCP-B — Migrate scheduled-job stores/scripts

```text
Migrate stores/scripts used by ingest_data.py, ingest_options_flatfiles.py, ingest_demand_data.py, run_demand_gate.py, alpha batch, estimate snapshots, price stores, options stores.
Use storage abstraction.
Keep local mode working.
```

### GCP-C — Publisher layer

```text
Add publish_signals workflow and script.
Create route-level artifacts under published/routes/ for options, stocks, alpha, and intelligence pages.
Do not let UI/API routes depend on raw/curated scans.
```

### GCP-D — Route repository layer

```text
Add backend route-data repository functions that read published/ first, then signals/.
Disable curated fallback by default.
Patch listed routes to use compact artifacts.
```

### GCP-E — One-time GCS migration

```text
Add migrate_data_to_gcs.py with dry-run and manifest.
Upload backend/data to GCS preserving relative paths.
Verify sample readback.
```

### GCP-F — Cloud Run Jobs ✅

```text
DONE: Dockerfile.jobs, deploy_jobs.sh, run_gcp_job.py, gcp_jobs.py (10 jobs incl. ingest-news, ingest-edgar).
Runtime SA: tyche-jobs@tyche-platform.iam.gserviceaccount.com. Secrets via Secret Manager.
Observability: tyche/ops/job_progress.py emits job_phase + job_progress to Cloud Logging;
  subprocess scripts stream stdout (gcp_jobs._run_subprocess). Runbook: infra/gcp/README.md § Observability.
```

### GCP-G — Workflows, Scheduler, manifests ✅

```text
DONE: run_manifest.py, evening + morning workflows, deploy_scheduler.sh.
Schedulers: 6:00 PM + 2:30 AM PT (Tue–Sat). Manifests at runs/{job}/{run_id}/manifest.json.
```

### GCP-H — Local backend points to GCS

```text
Run local backend with TYCHE_DATA_BACKEND=gcs.
Ensure listed routes read compact published/signals artifacts from GCS.
Do not require local backend/data.
```

---

## 20. Acceptance checklist

```text
[x] GCS bucket created (tyche-data-prod).
[x] tyche-jobs has storage.objectAdmin; Secret Accessor.
[x] tyche-workflow has serviceAccountUser on tyche-jobs + artifactregistry.writer.
[x] No service-account JSON keys committed; ADC used locally.
[x] Cloud Run Jobs use tyche-jobs@ (not App Engine default SA).
[x] Storage abstraction (GCP-A) works local and GCS.
[x] Scheduled-job stores/scripts read/write GCS.
[x] Cloud Run Jobs deployed (10 jobs).
[x] Cloud Workflows + Scheduler (6 PM + 2:30 AM PT Tue–Sat).
[x] Workflows use non-blocking job run + poll (not 30m LRO).
[x] Pacific ingest session dates (ingest_dates.py + TYCHE_INGEST_WINDOW).
[x] Secrets in Secret Manager.
[x] Run manifests written (incl. guidance_fetched vs guidance_written).
[x] Structured job progress logging (job_phase / job_progress) for all Cloud Run jobs.
[x] ingest_demand_data success on full universe (~3h cloud).
[x] alpha-batch NameError fix + deploy pre-build unit tests (June 2026).
[x] Published JSON NaN sanitization (intelligence routes).
[~] publish_signals + full evening/morning cycle verified end-to-end (publish runs; cycle sign-off pending).
[~] Local backend reads GCS published/signals via ADC (alpha + intelligence validated; options still live).
[ ] All listed frontend pages load from compact artifacts only (options/scanner/deep-dips/conviction gaps).
[ ] Cloud stocks conviction publish (conviction batch → Parquet/signals, not SQLite).
[ ] Multi-task ingest sharding (§21) — performance follow-up.
```

---

## 21. Open items / TODO

### P0 — Multi-task data ingest sharding

**Problem:** Each Cloud Run Job runs `--tasks=1`. Parallelism is in-process `asyncio` only. GCS per-ticker Parquet I/O (`write_bars`, `recompute_market_caps_from_shares`) is sequential — ~10–20× slower than local NVMe.

**Target:** Shard universe across N Cloud Run tasks using `CLOUD_RUN_TASK_INDEX` / `CLOUD_RUN_TASK_COUNT`:

```text
task_index = hash(ticker) % task_count
```

**Candidates (highest impact first):**

1. `ingest-data` — parallelize `write_bars` + cap recompute (batch GCS writes or threaded pool).
2. `ingest-options-flatfiles` — shard tickers across tasks in Phase 1/2.
3. `ingest-demand-data` — optional; API rate limits may cap benefit.
4. `ingest-news` / `ingest-edgar` — shard by ticker hash.

**Not in scope yet:** Kubernetes, Pub/Sub fanout, distributed locking.

### P1 — Route repository coverage

Finish GCP-D: all UI routes read `published/` first, `signals/` second; no curated scan on page load.

**Remaining (June 2026):**

1. **Options routes** — `publish_signals` emits placeholders for scanner, conviction, explore, monitor, covered_calls; API still live-computes via Tradier/engine.
2. **Stocks deep-dips / history** — placeholders only; routes scan OHLCV live.
3. **Cloud stocks conviction** — `publish_stocks_conviction` reads `conviction.db` (SQLite); Cloud Run has no DB → published `stocks_conviction.json` is empty. Needs cloud `conviction_batch` → Parquet or `signals/stocks/conviction.parquet` export (mirror intelligence pattern).
4. **Insider intelligence route** — published + Parquet exist; verify API reads published path (filings/news done).

### P1b — Deploy hygiene

- `deploy_jobs.sh --build`: ruff F821/F822/F823 + `test_alpha_batch`, `test_gcp_jobs`, `test_ingest_dates` before image push.
- Re-deploy jobs when batch Python changes; local backend restart only for API read-path fixes.

### P2 — GCS path normalization

Optional migration from flat `backend/data/` layout to `raw/`/`curated/`/`signals/` tree in §3.

Definition of done (minimal v1):

```text
GCP jobs update cloud data and compact route artifacts.
Laptop backend/frontend read those artifacts from GCS.
Page loads are fast because expensive downsampling happens in GCP.
```
