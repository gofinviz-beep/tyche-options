#!/usr/bin/env bash
# One-time IAM setup for the Tyche Cloud Run SERVICE (API + SPA).
#
# Creates the tyche-ui runtime service account and grants it the minimum it
# needs. Deliberately narrower than tyche-jobs: the service READS GCS artifacts
# and writes only to Postgres, so it never gets object write access.
#
# Usage:
#   source infra/gcp/config.env
#   ./infra/gcp/setup_service_iam.sh
#
# Idempotent — safe to re-run.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

PROJECT="${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"
BUCKET="${TYCHE_GCS_BUCKET:?Set TYCHE_GCS_BUCKET}"
UI_SA="${TYCHE_UI_SA:?Set TYCHE_UI_SA}"

SA_ID="${UI_SA%%@*}"

echo "==> Ensuring service account ${UI_SA}"
if gcloud iam service-accounts describe "$UI_SA" --project="$PROJECT" &>/dev/null; then
  echo "  exists"
else
  gcloud iam service-accounts create "$SA_ID" \
    --project="$PROJECT" \
    --display-name="Tyche Cloud Run service (API + SPA)" \
    --description="Reads published GCS artifacts, writes Cloud SQL. No object write."
  echo "  created"
fi

echo "==> Granting read-only access to gs://${BUCKET}"
# objectViewer, NOT objectAdmin: only the batch jobs produce artifacts.
gcloud storage buckets add-iam-policy-binding "gs://${BUCKET}" \
  --project="$PROJECT" \
  --member="serviceAccount:${UI_SA}" \
  --role=roles/storage.objectViewer \
  --quiet >/dev/null
echo "  roles/storage.objectViewer"

echo "==> Granting project-level roles"
for role in \
  roles/secretmanager.secretAccessor \
  roles/cloudsql.client \
  roles/logging.logWriter \
  roles/monitoring.metricWriter \
  roles/cloudtrace.agent
do
  gcloud projects add-iam-policy-binding "$PROJECT" \
    --member="serviceAccount:${UI_SA}" \
    --role="$role" \
    --condition=None \
    --quiet >/dev/null
  echo "  ${role}"
done

cat <<EOF

==> Done. ${UI_SA} can now:
      - read gs://${BUCKET} (published artifacts)
      - read Secret Manager versions (Tradier, Gemini, Polygon, Finnhub)
      - connect to Cloud SQL
      - write logs, metrics, and traces

    Deploy principal also needs roles/iam.serviceAccountUser on ${UI_SA}:
      gcloud iam service-accounts add-iam-policy-binding ${UI_SA} \\
        --project=${PROJECT} \\
        --member="user:YOUR_EMAIL" \\
        --role=roles/iam.serviceAccountUser

    Next: ./infra/gcp/seed_secrets.sh --from-dotenv
          ./infra/gcp/deploy_service.sh --build
EOF
