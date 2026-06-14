#!/usr/bin/env bash
# Deploy (or update) Tyche Cloud Run Jobs (GCP-F).
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project "$TYCHE_GCP_PROJECT_ID"
#   docker build + push (see README.md)
#   source infra/gcp/config.env
#
# Usage:
#   ./infra/gcp/deploy_jobs.sh
#   ./infra/gcp/deploy_jobs.sh --build   # build + push image first

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

BUILD=false
if [[ "${1:-}" == "--build" ]]; then
  BUILD=true
fi

: "${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"
: "${TYCHE_GCP_REGION:?Set TYCHE_GCP_REGION}"
: "${TYCHE_JOBS_IMAGE:?Set TYCHE_JOBS_IMAGE}"
: "${TYCHE_JOBS_SA:?Set TYCHE_JOBS_SA}"
: "${TYCHE_JOB_ENV:?Set TYCHE_JOB_ENV}"

if $BUILD; then
  echo "==> Pre-build checks (ruff undefined-name + Cloud Run job unit tests)"
  (
    cd "$BACKEND_DIR"
    # F821/F822/F823 only — not full F (F401 unused imports block deploy on legacy debt).
    .venv/bin/ruff check src/tyche --select F821,F822,F823
    .venv/bin/python -m pytest \
      tests/unit/test_alpha_batch.py \
      tests/unit/test_gcp_jobs.py \
      tests/unit/test_ingest_dates.py \
      -q --no-cov
  )
  echo "==> Building and pushing ${TYCHE_JOBS_IMAGE} (linux/amd64 for Cloud Run)"
  gcloud auth configure-docker "${TYCHE_GCP_REGION}-docker.pkg.dev" --quiet
  # Cloud Run is amd64. Docker Desktop on Apple Silicon defaults to arm64 —
  # pushing that image causes immediate "Application failed to start".
  docker buildx build \
    --platform linux/amd64 \
    -f "$BACKEND_DIR/Dockerfile.jobs" \
    -t "$TYCHE_JOBS_IMAGE" \
    --push \
    "$BACKEND_DIR"
fi

deploy_job() {
  local name="$1"
  local job_arg="$2"
  local cpu="$3"
  local memory="$4"
  local timeout="$5"
  local task_count="${6:-1}"
  local extra_env="${7:-}"

  local env_vars="$TYCHE_JOB_ENV"
  if [[ -n "${extra_env}" ]]; then
    env_vars="${env_vars},${extra_env}"
  fi

  echo "==> Deploying Cloud Run Job: ${name}"

  if gcloud run jobs describe "$name" --region="$TYCHE_GCP_REGION" --project="$TYCHE_GCP_PROJECT_ID" &>/dev/null; then
    gcloud run jobs update "$name" \
      --project="$TYCHE_GCP_PROJECT_ID" \
      --region="$TYCHE_GCP_REGION" \
      --image="$TYCHE_JOBS_IMAGE" \
      --service-account="$TYCHE_JOBS_SA" \
      --tasks="$task_count" \
      --cpu="$cpu" \
      --memory="$memory" \
      --task-timeout="$timeout" \
      --max-retries=0 \
      --args="${job_arg}" \
      --set-env-vars="$env_vars"
  else
    gcloud run jobs create "$name" \
      --project="$TYCHE_GCP_PROJECT_ID" \
      --region="$TYCHE_GCP_REGION" \
      --image="$TYCHE_JOBS_IMAGE" \
      --service-account="$TYCHE_JOBS_SA" \
      --tasks="$task_count" \
      --cpu="$cpu" \
      --memory="$memory" \
      --task-timeout="$timeout" \
      --max-retries=0 \
      --args="${job_arg}" \
      --set-env-vars="$env_vars"
  fi
}

# Task timeout — GCS per-ticker Parquet I/O is much slower than local NVMe.
# ingest-data (OHLCV + cap reprice over ~3.5k tickers) exceeded 4h; all jobs
# use 8h (28800s). Cloud Run max is 168h.
TIMEOUT_8H=28800s

# name, job-arg, cpu, memory, timeout, tasks, extra_env (TYCHE_INGEST_WINDOW)
# Evening 6 PM PT: Pacific today. Morning 2:30 AM PT: Pacific yesterday.
deploy_job tyche-ingest-data              ingest-data                 2 4Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=evening"
deploy_job tyche-ingest-demand-data       ingest-demand-data        2 4Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=evening"
deploy_job tyche-ingest-news              ingest-news                 2 4Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=evening"
deploy_job tyche-ingest-edgar             ingest-edgar                2 4Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=evening"
deploy_job tyche-ingest-options-flatfiles ingest-options-flatfiles  2 4Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"
deploy_job tyche-alpha-batch              alpha-batch               4 8Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"
deploy_job tyche-run-demand-gate          run-demand-gate           8 32Gi "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"
deploy_job tyche-publish-signals          publish-signals           2 4Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"
deploy_job tyche-audit-snapshots          audit-snapshots           1 2Gi  "${TIMEOUT_8H}" 1 "TYCHE_INGEST_WINDOW=morning"
# In-process fallback chain (manual runs); production uses Cloud Workflows.
deploy_job tyche-nightly-pipeline         nightly-pipeline          4 8Gi  "${TIMEOUT_8H}"

echo "==> All jobs deployed in ${TYCHE_GCP_REGION}"
