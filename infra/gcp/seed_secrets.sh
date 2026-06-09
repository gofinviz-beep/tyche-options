#!/usr/bin/env bash
# Create or update Secret Manager secrets for Cloud Run Jobs.
#
# Secret *ids* must match backend/src/tyche/ops/gcp_secrets.py (not TYCHE_ prefix).
#
# Usage (recommended — reads values from backend/.env, never commits them):
#   source infra/gcp/config.env
#   ./infra/gcp/seed_secrets.sh --from-dotenv
#
# Or pass values via environment:
#   POLYGON_API_KEY=... FINNHUB_API_KEY=... ./infra/gcp/seed_secrets.sh
#
# List existing secrets:
#   ./infra/gcp/seed_secrets.sh --list

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# shellcheck source=/dev/null
source "${SCRIPT_DIR}/config.env" 2>/dev/null || source "${SCRIPT_DIR}/config.env.example"

PROJECT="${TYCHE_GCP_PROJECT_ID:?Set TYCHE_GCP_PROJECT_ID}"

# secret_id -> TYCHE_ env var in backend/.env (quoted keys: set -u treats [KEY] as $KEY)
SECRET_IDS=(
  POLYGON_API_KEY
  FINNHUB_API_KEY
  MASSIVE_S3_ACCESS_KEY
  MASSIVE_S3_SECRET_KEY
  GEMINI_API_KEY
  EDGAR_USER_AGENT_EMAIL
)

env_var_for_secret() {
  case "$1" in
    POLYGON_API_KEY) echo TYCHE_POLYGON_API_KEY ;;
    FINNHUB_API_KEY) echo TYCHE_FINNHUB_API_KEY ;;
    MASSIVE_S3_ACCESS_KEY) echo TYCHE_MASSIVE_S3_ACCESS_KEY ;;
    MASSIVE_S3_SECRET_KEY) echo TYCHE_MASSIVE_S3_SECRET_KEY ;;
    GEMINI_API_KEY) echo TYCHE_GEMINI_API_KEY ;;
    EDGAR_USER_AGENT_EMAIL) echo TYCHE_EDGAR_USER_AGENT_EMAIL ;;
    *) echo "" ;;
  esac
}

list_secrets() {
  echo "==> Secrets in project ${PROJECT}:"
  gcloud secrets list --project="$PROJECT" --format="table(name,createTime)" || true
}

upsert_secret() {
  local secret_id="$1"
  local value="$2"
  if [[ -z "${value}" ]]; then
    echo "  SKIP ${secret_id} (empty value)"
    return 0
  fi
  if gcloud secrets describe "$secret_id" --project="$PROJECT" &>/dev/null; then
    echo -n "$value" | gcloud secrets versions add "$secret_id" \
      --project="$PROJECT" --data-file=-
    echo "  UPDATED ${secret_id}"
  else
    echo -n "$value" | gcloud secrets create "$secret_id" \
      --project="$PROJECT" --replication-policy=automatic --data-file=-
    echo "  CREATED ${secret_id}"
  fi
}

load_dotenv() {
  local dotenv="${REPO_ROOT}/backend/.env"
  if [[ ! -f "$dotenv" ]]; then
    echo "ERROR: ${dotenv} not found" >&2
    exit 1
  fi
  # Export TYCHE_* keys only (no eval of arbitrary shell)
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" =~ ^[[:space:]]*$ ]] && continue
    if [[ "$line" =~ ^(TYCHE_[A-Z0-9_]+)=(.*)$ ]]; then
      export "${BASH_REMATCH[1]}=${BASH_REMATCH[2]}"
    fi
  done < "$dotenv"
}

FROM_DOTENV=false
if [[ "${1:-}" == "--list" ]]; then
  list_secrets
  exit 0
fi
if [[ "${1:-}" == "--from-dotenv" ]]; then
  FROM_DOTENV=true
fi

if $FROM_DOTENV; then
  echo "==> Loading values from backend/.env"
  load_dotenv
fi

echo "==> Upserting secrets in project ${PROJECT}"
for secret_id in "${SECRET_IDS[@]}"; do
  env_var="$(env_var_for_secret "$secret_id")"
  value=""
  if [[ -n "$env_var" ]]; then
    value="${!env_var-}"
  fi
  upsert_secret "$secret_id" "$value"
done

echo "==> Done. Verify tyche-jobs SA has roles/secretmanager.secretAccessor"
list_secrets
