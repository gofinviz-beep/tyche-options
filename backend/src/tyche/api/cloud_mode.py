"""Cloud/GCS read-only API guards for artifact-served routes.

The guard exists because a full-universe scan in GCS mode means reading thousands
of Parquet objects synchronously, which blocks the event loop and hangs the whole
API. That reasoning does not extend to every live operation: fetching one option
chain for a ticker the user just typed costs a single broker call. Those are
distinguished here as *bounded* work, so features like Options Explore and Covered
Calls analysis keep working in the cloud deployment.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, HTTPException

from tyche.api.deps import get_settings
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


def bounded_inline_compute_blocked(settings: TycheSettings) -> bool:
    """True when even bounded, per-ticker live work must not run in-process.

    Bounded work is scoped to tickers the caller named, so its cost is
    proportional to the request rather than to the universe.
    """
    if not cloud_inline_compute_blocked(settings):
        return False
    return not settings.allow_bounded_inline_compute


def require_inline_compute_allowed(
    settings: TycheSettings,
    *,
    operation: str,
    job_hint: str,
    bounded: bool = False,
) -> None:
    """Raise 409 when cloud mode forbids inline compute for *operation*.

    Args:
        settings: Active settings.
        operation: Human-readable operation name for the error detail.
        job_hint: Batch job the caller should trigger instead.
        bounded: True when the work is limited to caller-specified tickers. These
            are allowed in cloud mode unless ``allow_bounded_inline_compute`` is
            turned off, since they do not scan the universe.
    """
    blocked = (
        bounded_inline_compute_blocked(settings)
        if bounded
        else cloud_inline_compute_blocked(settings)
    )
    if not blocked:
        return

    override = (
        "TYCHE_ALLOW_BOUNDED_INLINE_COMPUTE=true"
        if bounded
        else "TYCHE_ALLOW_INLINE_SCAN=true"
    )
    raise HTTPException(
        status_code=409,
        detail=(
            f"Cloud mode does not run {operation} inline. "
            f"Trigger the scheduled GCP batch job ({job_hint}) or set "
            f"{override} for local-dev override."
        ),
    )


def inline_compute_guard(*, operation: str, job_hint: str) -> Callable[..., None]:
    """Build a route-level dependency that rejects blocked inline compute.

    Use this instead of calling :func:`require_inline_compute_allowed` in the
    handler body when a route declares expensive ``Depends()`` parameters.
    FastAPI solves decorator-level dependencies before signature ones, so the
    409 is returned without constructing an engine, opening a store, or loading
    a model the request will never use.
    """

    def _guard(settings: TycheSettings = Depends(get_settings)) -> None:
        require_inline_compute_allowed(
            settings, operation=operation, job_hint=job_hint
        )

    return _guard
