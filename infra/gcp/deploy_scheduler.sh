#!/usr/bin/env bash
# Deploy Cloud Scheduler triggers for Tyche workflows (GCP-G).
#
# Two windows (America/Los_Angeles):
#   6:00 PM Mon–Fri  → tyche-evening-pipeline (same-day OHLCV + demand + news + EDGAR)
#   2:30 AM Tue–Sat  → tyche-morning-pipeline (prior-day options flatfiles + alpha, publish)
#
# Cron: evening `1-5` (Mon–Fri), morning `2-6` (Tue–Sat). No Sun/Mon morning; no Sat/Sun evening.
#
# Usage:
#   source infra/gcp/config.env
#   ./infra/gcp/deploy_workflow.sh
#   ./infra/gcp/deploy_scheduler.sh
#   ./infra/gcp/deploy_scheduler.sh --legacy-per-job

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

: "${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"
: "${TYCHE_GCP_REGION:?Set TYCHE_GCP_REGION}"
: "${TYCHE_SCHEDULER_SA:?Set TYCHE_SCHEDULER_SA}"

SCHEDULER_LOCATION="${TYCHE_SCHEDULER_LOCATION:-us-central1}"
WORKFLOW_LOCATION="${TYCHE_WORKFLOW_LOCATION:-${TYCHE_GCP_REGION}}"
TIMEZONE="America/Los_Angeles"
EVENING_CRON="0 18 * * 1-5"
MORNING_CRON="30 2 * * 2-6"
MODE="${1:-workflow}"

run_uri() {
  local job="$1"
  echo "https://${TYCHE_GCP_REGION}-run.googleapis.com/apis/run.googleapis.com/v1/namespaces/${TYCHE_GCP_PROJECT_ID}/jobs/${job}:run"
}

workflow_uri() {
  local workflow_id="$1"
  echo "https://workflowexecutions.googleapis.com/v1/projects/${TYCHE_GCP_PROJECT_ID}/locations/${WORKFLOW_LOCATION}/workflows/${workflow_id}/executions"
}

deploy_scheduler() {
  local sched_name="$1"
  local cron="$2"
  local uri="$3"
  local body="${4:-}"

  echo "==> Scheduler ${sched_name}: ${cron} (${TIMEZONE})"

  local body_args=()
  if [[ -n "${body}" ]]; then
    body_args+=(--message-body="${body}")
  fi

  if gcloud scheduler jobs describe "$sched_name" \
      --location="$SCHEDULER_LOCATION" \
      --project="$TYCHE_GCP_PROJECT_ID" &>/dev/null; then
    local update_args=("${body_args[@]}")
    if [[ -n "${body}" ]]; then
      update_args+=(--update-headers="Content-Type=application/json")
    fi
    gcloud scheduler jobs update http "$sched_name" \
      --project="$TYCHE_GCP_PROJECT_ID" \
      --location="$SCHEDULER_LOCATION" \
      --schedule="$cron" \
      --time-zone="$TIMEZONE" \
      --uri="$uri" \
      --http-method=POST \
      --oauth-service-account-email="$TYCHE_SCHEDULER_SA" \
      --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
      "${update_args[@]}"
  else
    local create_args=("${body_args[@]}")
    if [[ -n "${body}" ]]; then
      create_args+=(--headers="Content-Type=application/json")
    fi
    gcloud scheduler jobs create http "$sched_name" \
      --project="$TYCHE_GCP_PROJECT_ID" \
      --location="$SCHEDULER_LOCATION" \
      --schedule="$cron" \
      --time-zone="$TIMEZONE" \
      --uri="$uri" \
      --http-method=POST \
      --oauth-service-account-email="$TYCHE_SCHEDULER_SA" \
      --oauth-token-scope="https://www.googleapis.com/auth/cloud-platform" \
      "${create_args[@]}"
  fi
}

workflow_exists() {
  local workflow_id="$1"
  gcloud workflows describe "${workflow_id}" \
    --location="$WORKFLOW_LOCATION" \
    --project="$TYCHE_GCP_PROJECT_ID" &>/dev/null
}

remove_scheduler() {
  local name="$1"
  if gcloud scheduler jobs describe "$name" \
      --location="$SCHEDULER_LOCATION" \
      --project="$TYCHE_GCP_PROJECT_ID" &>/dev/null; then
    echo "==> Removing scheduler ${name}"
    gcloud scheduler jobs delete "$name" \
      --location="$SCHEDULER_LOCATION" \
      --project="$TYCHE_GCP_PROJECT_ID" \
      --quiet
  fi
}

if [[ "$MODE" == "--legacy-per-job" ]]; then
  remove_scheduler tyche-sched-evening-pipeline
  remove_scheduler tyche-sched-morning-pipeline
  remove_scheduler tyche-sched-nightly-pipeline
  deploy_scheduler tyche-sched-ingest-data              "$EVENING_CRON" "$(run_uri tyche-ingest-data)"
  deploy_scheduler tyche-sched-ingest-demand-data       "$EVENING_CRON" "$(run_uri tyche-ingest-demand-data)"
  deploy_scheduler tyche-sched-ingest-news              "$EVENING_CRON" "$(run_uri tyche-ingest-news)"
  deploy_scheduler tyche-sched-ingest-edgar             "$EVENING_CRON" "$(run_uri tyche-ingest-edgar)"
  deploy_scheduler tyche-sched-ingest-options-flatfiles "$MORNING_CRON" "$(run_uri tyche-ingest-options-flatfiles)"
  deploy_scheduler tyche-sched-alpha-batch              "$MORNING_CRON" "$(run_uri tyche-alpha-batch)"
  deploy_scheduler tyche-sched-publish-signals        "0 6 * * 2-6"  "$(run_uri tyche-publish-signals)"
  deploy_scheduler tyche-sched-audit-snapshots          "15 6 * * 2-6" "$(run_uri tyche-audit-snapshots)"
  echo "==> Legacy parallel per-job schedulers deployed in ${SCHEDULER_LOCATION}"
  exit 0
fi

for wf in tyche-evening-pipeline tyche-morning-pipeline; do
  if ! workflow_exists "$wf"; then
    echo "ERROR: workflow ${wf} is not deployed. Run ./infra/gcp/deploy_workflow.sh first."
    exit 1
  fi
done

remove_scheduler tyche-sched-nightly-pipeline

deploy_scheduler \
  tyche-sched-evening-pipeline \
  "$EVENING_CRON" \
  "$(workflow_uri tyche-evening-pipeline)" \
  '{}'

deploy_scheduler \
  tyche-sched-morning-pipeline \
  "$MORNING_CRON" \
  "$(workflow_uri tyche-morning-pipeline)" \
  '{}'

echo "==> Evening (6 PM Mon–Fri) + morning (2:30 AM Tue–Sat) workflow schedulers deployed"
