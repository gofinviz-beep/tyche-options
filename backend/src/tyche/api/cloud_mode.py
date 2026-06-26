"""Cloud/GCS read-only API guards for artifact-served routes."""

from __future__ import annotations

from fastapi import HTTPException

from tyche.config import TycheSettings


def use_artifact_read_path(settings: TycheSettings) -> bool:
    """True when normal page loads should read published/signal artifacts only."""
    return bool(
        settings.api_prefer_published_signals
        or settings.data_backend == "gcs"
        or not settings.api_allow_local_db_fallback
    )


def cloud_inline_compute_blocked(settings: TycheSettings) -> bool:
    """True when inline universe scans must not run in-process."""
    return settings.data_backend == "gcs" and not settings.allow_inline_scan


def require_inline_compute_allowed(
    settings: TycheSettings,
    *,
    operation: str,
    job_hint: str,
) -> None:
    """Raise 409 when cloud mode forbids inline compute for *operation*."""
    if not cloud_inline_compute_blocked(settings):
        return
    raise HTTPException(
        status_code=409,
        detail=(
            f"Cloud mode does not run {operation} inline. "
            f"Trigger the scheduled GCP batch job ({job_hint}) or set "
            "TYCHE_ALLOW_INLINE_SCAN=true for local-dev override."
        ),
    )
