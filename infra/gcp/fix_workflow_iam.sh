#!/usr/bin/env bash
# Grant tyche-workflow SA permission to create workflow executions (fixes Scheduler 403).
#
# Cloud Scheduler POSTs to workflowexecutions.googleapis.com using this SA.
# There is NO `gcloud workflows add-iam-policy-binding` — use project-level binding.
#
# Usage:
#   source infra/gcp/config.env
#   ./infra/gcp/fix_workflow_iam.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

: "${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"
: "${TYCHE_SCHEDULER_SA:?Set TYCHE_SCHEDULER_SA}"

echo "==> Granting roles/workflows.invoker to ${TYCHE_SCHEDULER_SA} on ${TYCHE_GCP_PROJECT_ID}"

gcloud projects add-iam-policy-binding "${TYCHE_GCP_PROJECT_ID}" \
  --member="serviceAccount:${TYCHE_SCHEDULER_SA}" \
  --role="roles/workflows.invoker" \
  --condition=None

echo "==> Done. Re-test scheduler or run:"
echo "    gcloud workflows run tyche-morning-pipeline --location=${TYCHE_GCP_REGION:-us-central1} --project=${TYCHE_GCP_PROJECT_ID}"
