"""Structured progress logging for long-running Cloud Run batch jobs.

Emit ``job_phase`` at step boundaries and ``job_progress`` during loops so
Cloud Logging shows live state (phase, done/total, ETA) without tailing manifests.
"""

from __future__ import annotations

import time
from typing import Any

import structlog

_logger = structlog.get_logger()


def log_job_phase(
    job: str,
    phase: str,
    *,
    status: str = "start",
    logger: structlog.stdlib.BoundLogger | None = None,
    **extra: Any,
) -> None:
    """Log a pipeline phase boundary (start / complete / skip)."""
    (logger or _logger).info(
        "job_phase",
        job=job,
        phase=phase,
        status=status,
        **extra,
    )


def log_job_progress(
    job: str,
    phase: str,
    *,
    done: int,
    total: int,
    logger: structlog.stdlib.BoundLogger | None = None,
    start_time: float | None = None,
    **extra: Any,
) -> None:
    """Log fractional progress with optional ETA from *start_time* (monotonic)."""
    pct = round(100.0 * done / total, 1) if total else 0.0
    payload: dict[str, Any] = {
        "job": job,
        "phase": phase,
        "done": done,
        "total": total,
        "pct": pct,
        **extra,
    }
    if start_time is not None and done > 0:
        elapsed = time.monotonic() - start_time
        rate = done / elapsed if elapsed > 0 else 0.0
        remaining = total - done
        payload["elapsed_min"] = round(elapsed / 60, 1)
        if rate > 0 and remaining > 0:
            payload["eta_min"] = round(remaining / rate / 60, 1)
    (logger or _logger).info("job_progress", **payload)
