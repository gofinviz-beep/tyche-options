#!/usr/bin/env bash
# Deploy (or update) the Tyche Cloud Run SERVICE — FastAPI API + built SPA in one
# container. Batch compute is separate; see deploy_jobs.sh.
#
# Prerequisites:
#   gcloud auth login
#   gcloud config set project "$TYCHE_GCP_PROJECT_ID"
#   ./infra/gcp/seed_secrets.sh --from-dotenv   # needs TRADIER_* secrets
#   ./infra/gcp/setup_service_iam.sh            # creates the tyche-ui SA + roles
#   source infra/gcp/config.env
#
# Usage:
#   ./infra/gcp/deploy_service.sh
#   ./infra/gcp/deploy_service.sh --build       # build + push image first
#   ./infra/gcp/deploy_service.sh --build --public
#
# Access control: the service deploys with --no-allow-unauthenticated. IAP must be
# enabled ONCE in the console first (the OAuth client cannot be created via API):
#   Console > Security > Identity-Aware Proxy > enable for the Cloud Run service
# then re-run this script with IAP_ENABLED=true. See README.md § Cloud Run service.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BACKEND_DIR="$REPO_ROOT/backend"
FRONTEND_DIR="$REPO_ROOT/frontend"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

BUILD=false
PUBLIC=false
for arg in "$@"; do
  case "$arg" in
    --build) BUILD=true ;;
    --public) PUBLIC=true ;;
    *) echo "Unknown argument: $arg" >&2; exit 2 ;;
  esac
done

: "${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"
: "${TYCHE_GCP_REGION:?Set TYCHE_GCP_REGION}"
: "${TYCHE_GCS_BUCKET:?Set TYCHE_GCS_BUCKET}"
: "${TYCHE_API_IMAGE:?Set TYCHE_API_IMAGE}"
: "${TYCHE_API_SERVICE:?Set TYCHE_API_SERVICE}"
: "${TYCHE_UI_SA:?Set TYCHE_UI_SA}"
: "${TYCHE_SERVICE_ENV:?Set TYCHE_SERVICE_ENV}"

IAP_ENABLED="${IAP_ENABLED:-false}"

if $BUILD; then
  echo "==> Pre-build checks"
  (
    cd "$BACKEND_DIR"
    # F821/F822/F823 only — full F would block on legacy unused-import debt.
    .venv/bin/ruff check src/tyche --select F821,F822,F823
    .venv/bin/python -m pytest \
      tests/unit/test_static_files.py \
      tests/unit/test_config.py \
      tests/unit/test_api.py \
      -q --no-cov
  )
  (
    cd "$FRONTEND_DIR"
    # Typecheck here so a broken SPA fails fast instead of inside the Node stage.
    npx tsc -b
  )

  echo "==> Building and pushing ${TYCHE_API_IMAGE} (linux/amd64 for Cloud Run)"
  gcloud auth configure-docker "${TYCHE_GCP_REGION}-docker.pkg.dev" --quiet
  # Build context is the REPO ROOT, not backend/ — the Node stage needs frontend/.
  # Cloud Run is amd64; an arm64 image from Apple Silicon fails to start.
  docker buildx build \
    --platform linux/amd64 \
    -f "$BACKEND_DIR/Dockerfile.api" \
    -t "$TYCHE_API_IMAGE" \
    --push \
    "$REPO_ROOT"
fi

# Secrets: attached as env vars by Cloud Run, so the app needs no Secret Manager
# call at startup (TYCHE_LOAD_GCP_SECRETS=false in TYCHE_SERVICE_ENV).
SECRETS="TYCHE_TRADIER_API_TOKEN=TRADIER_API_TOKEN:latest"
SECRETS="${SECRETS},TYCHE_TRADIER_ACCOUNT_ID=TRADIER_ACCOUNT_ID:latest"
SECRETS="${SECRETS},TYCHE_GEMINI_API_KEY=GEMINI_API_KEY:latest"
SECRETS="${SECRETS},TYCHE_POLYGON_API_KEY=POLYGON_API_KEY:latest"
SECRETS="${SECRETS},TYCHE_FINNHUB_API_KEY=FINNHUB_API_KEY:latest"

deploy_args=(
  "$TYCHE_API_SERVICE"
  --project="$TYCHE_GCP_PROJECT_ID"
  --region="$TYCHE_GCP_REGION"
  --image="$TYCHE_API_IMAGE"
  --service-account="$TYCHE_UI_SA"
  --set-env-vars="$TYCHE_SERVICE_ENV"
  --set-secrets="$SECRETS"
  --cpu=2
  --memory=2Gi
  # The published dataset is ~7 MiB held in-process, so one warm instance keeps
  # the artifact cache hot. Scaling out would just multiply GCS reads.
  --min-instances=1
  --max-instances=4
  # Single uvicorn worker; asyncio handles the concurrency.
  --concurrency=40
  --timeout=300
  # Full CPU during startup so a cold Python import of pandas/pyarrow is not
  # throttled to the steady-state allocation.
  --cpu-boost
  --port=8080
)

if [[ -n "${TYCHE_SQL_INSTANCE:-}" ]]; then
  echo "==> Attaching Cloud SQL instance ${TYCHE_SQL_INSTANCE}"
  deploy_args+=(--add-cloudsql-instances="$TYCHE_SQL_INSTANCE")
fi

if $PUBLIC; then
  echo "!!! WARNING: deploying PUBLICLY (--allow-unauthenticated)."
  echo "!!! This exposes account balances and positions to the internet."
  deploy_args+=(--allow-unauthenticated)
else
  deploy_args+=(--no-allow-unauthenticated)
fi

if [[ "$IAP_ENABLED" == "true" ]]; then
  deploy_args+=(--iap)
fi

echo "==> Deploying Cloud Run service: ${TYCHE_API_SERVICE}"
gcloud run deploy "${deploy_args[@]}"

URL="$(gcloud run services describe "$TYCHE_API_SERVICE" \
  --project="$TYCHE_GCP_PROJECT_ID" \
  --region="$TYCHE_GCP_REGION" \
  --format='value(status.url)')"

echo "==> Deployed: ${URL}"

if [[ "$IAP_ENABLED" != "true" ]] && ! $PUBLIC; then
  cat <<EOF

==> NEXT: the service is deployed but nothing can reach it yet.
    1. Console > Security > Identity-Aware Proxy > enable for ${TYCHE_API_SERVICE}
       (creates the OAuth client — not possible via gcloud)
    2. PROJECT_NUMBER=\$(gcloud projects describe ${TYCHE_GCP_PROJECT_ID} --format='value(projectNumber)')
       gcloud run services add-iam-policy-binding ${TYCHE_API_SERVICE} \\
         --region=${TYCHE_GCP_REGION} --project=${TYCHE_GCP_PROJECT_ID} \\
         --member="serviceAccount:service-\${PROJECT_NUMBER}@gcp-sa-iap.iam.gserviceaccount.com" \\
         --role=roles/run.invoker
    3. gcloud projects add-iam-policy-binding ${TYCHE_GCP_PROJECT_ID} \\
         --member="user:YOUR_EMAIL" --role=roles/iap.httpsResourceAccessor
    4. IAP_ENABLED=true ./infra/gcp/deploy_service.sh
EOF
fi
