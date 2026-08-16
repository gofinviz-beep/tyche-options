"""Load API keys from GCP Secret Manager into process env (GCP-F).

Cloud Run Jobs use workload identity — no JSON key file. Secrets are fetched
once at process start before :func:`tyche.config.get_settings` runs.

Mapping uses Secret Manager secret *ids* (not full resource names). Each id is
written to the corresponding ``TYCHE_*`` environment variable only when that
variable is not already set (local overrides still work).
"""

from __future__ import annotations

import os
from typing import Any, Final

import structlog

logger = structlog.get_logger()

# Secret Manager secret id -> TYCHE_ env var
#
# The Cloud Run API service gets these via `gcloud run deploy --set-secrets`
# rather than this module, so it needs no extra call at startup. They stay
# mapped here so the batch jobs (which do hydrate at process start) and the
# seed script share one list.
SECRET_TO_ENV: Final[dict[str, str]] = {
    "POLYGON_API_KEY": "TYCHE_POLYGON_API_KEY",
    "FINNHUB_API_KEY": "TYCHE_FINNHUB_API_KEY",
    "MASSIVE_S3_ACCESS_KEY": "TYCHE_MASSIVE_S3_ACCESS_KEY",
    "MASSIVE_S3_SECRET_KEY": "TYCHE_MASSIVE_S3_SECRET_KEY",
    "GEMINI_API_KEY": "TYCHE_GEMINI_API_KEY",
    "EDGAR_USER_AGENT_EMAIL": "TYCHE_EDGAR_USER_AGENT_EMAIL",
    # Live broker calls (quotes, chains, account) — needed by the API service.
    "TRADIER_API_TOKEN": "TYCHE_TRADIER_API_TOKEN",
    "TRADIER_ACCOUNT_ID": "TYCHE_TRADIER_ACCOUNT_ID",
}


def _secret_manager_client() -> Any | None:
    try:
        from google.cloud import secretmanager

        return secretmanager.SecretManagerServiceClient()
    except ImportError as exc:
        logger.error("gcp_secrets_import_failed", error=str(exc))
        return None


def should_load_gcp_secrets() -> bool:
    """Return True when Secret Manager hydration should run."""
    explicit = os.environ.get("TYCHE_LOAD_GCP_SECRETS", "").strip().lower()
    if explicit in ("1", "true", "yes", "on"):
        return True
    if explicit in ("0", "false", "no", "off"):
        return False
    return os.environ.get("TYCHE_RUN_ENV", "").strip().lower() in ("prod", "cloud")


def hydrate_env_from_secret_manager(
    *,
    project_id: str | None = None,
    secret_map: dict[str, str] | None = None,
) -> list[str]:
    """Fetch secrets and set env vars. Returns secret ids that were loaded."""
    project = (project_id or os.environ.get("TYCHE_GCP_PROJECT_ID") or "").strip()
    if not project:
        logger.warning("gcp_secrets_skipped_no_project")
        return []

    mapping = secret_map or SECRET_TO_ENV
    loaded: list[str] = []

    client = _secret_manager_client()
    if client is None:
        return []

    for secret_id, env_key in mapping.items():
        if os.environ.get(env_key):
            continue
        name = f"projects/{project}/secrets/{secret_id}/versions/latest"
        try:
            response = client.access_secret_version(request={"name": name})
            value = response.payload.data.decode("utf-8").strip()
        except Exception as exc:
            logger.warning(
                "gcp_secret_fetch_failed",
                secret_id=secret_id,
                error=str(exc),
            )
            continue
        if value:
            os.environ[env_key] = value
            loaded.append(secret_id)
            logger.info("gcp_secret_loaded", secret_id=secret_id, env_key=env_key)

    return loaded


def bootstrap_gcp_runtime() -> list[str]:
    """Hydrate secrets when configured for cloud job execution."""
    if not should_load_gcp_secrets():
        return []
    return hydrate_env_from_secret_manager()
