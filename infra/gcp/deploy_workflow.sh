#!/usr/bin/env bash
# Deploy Cloud Workflows orchestrators (GCP-G).
#
# Prerequisites:
#   gcloud services enable workflows.googleapis.com workflowexecutions.googleapis.com
#   source infra/gcp/config.env
#
# Usage:
#   ./infra/gcp/deploy_workflow.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

: "${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"
: "${TYCHE_GCP_REGION:?Set TYCHE_GCP_REGION}"
: "${TYCHE_SCHEDULER_SA:?Set TYCHE_SCHEDULER_SA}"

WORKFLOW_LOCATION="${TYCHE_WORKFLOW_LOCATION:-${TYCHE_GCP_REGION}}"

deploy_one() {
  local workflow_id="$1"
  local source_file="$2"
  echo "==> Deploying workflow ${workflow_id} in ${WORKFLOW_LOCATION}"
  gcloud workflows deploy "${workflow_id}" \
    --project="${TYCHE_GCP_PROJECT_ID}" \
    --location="${WORKFLOW_LOCATION}" \
    --source="${source_file}" \
    --service-account="${TYCHE_SCHEDULER_SA}"
}

deploy_one tyche-evening-pipeline "${REPO_ROOT}/infra/gcp/workflows/evening-pipeline.yaml"
deploy_one tyche-morning-pipeline "${REPO_ROOT}/infra/gcp/workflows/morning-pipeline.yaml"
deploy_one tyche-options-morning-slice "${REPO_ROOT}/infra/gcp/workflows/options-morning-slice.yaml"

echo "==> Workflows deployed in ${WORKFLOW_LOCATION}"
