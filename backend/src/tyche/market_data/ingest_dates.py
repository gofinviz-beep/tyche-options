"""US Pacific session dates for ingest pipelines (region-independent).

Uses IANA ``America/Los_Angeles`` via ``zoneinfo`` — correct for PST/PDT and
independent of the host/container timezone (UTC on Cloud Run, local on a laptop,
any GCP/AWS region). Never use ``date.today()`` for market-session boundaries.

Evening jobs (6 PM PT, Mon–Fri): Pacific *today* (session that just closed).
Morning jobs (2:30 AM PT, Tue–Sat): Pacific *yesterday* (prior day; options flatfile leads).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

import structlog

logger = structlog.get_logger()

PACIFIC_TZ = ZoneInfo("America/Los_Angeles")
IngestWindow = Literal["evening", "morning"]

EVENING_JOBS: frozenset[str] = frozenset(
    {
        "ingest-data",
        "ingest-demand-data",
        "ingest-news",
        "ingest-edgar",
    }
)
MORNING_JOBS: frozenset[str] = frozenset(
    {
        "ingest-options-flatfiles",
        "alpha-batch",
        "run-demand-gate",
        "publish-signals",
        "audit-snapshots",
    }
)


def pacific_now() -> datetime:
    """Current wall-clock time in US Pacific."""
    return datetime.now(tz=PACIFIC_TZ)


def pacific_today(*, now: datetime | None = None) -> date:
    """Today's calendar date in US Pacific (PST/PDT)."""
    dt = now or pacific_now()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(PACIFIC_TZ).date()


def pacific_yesterday(*, now: datetime | None = None) -> date:
    """Prior calendar date in US Pacific."""
    return pacific_today(now=now) - timedelta(days=1)


def ingest_end_date(
    window: str | None,
    *,
    job_name: str | None = None,
) -> date:
    """Resolve the OHLCV/options session end date for a pipeline window.

    Args:
        window: ``evening`` (Pacific today) or ``morning`` (Pacific yesterday).
        job_name: Fallback when ``window`` is unset (nightly in-process chain).
    """
    w = (window or "").strip().lower()
    if not w and job_name:
        if job_name in EVENING_JOBS:
            w = "evening"
        elif job_name in MORNING_JOBS:
            w = "morning"

    today_pt = pacific_today()
    if w == "evening":
        return today_pt
    if w == "morning":
        return today_pt - timedelta(days=1)

    # Unset window: Pacific yesterday (safe ingest default on any host TZ)
    return today_pt - timedelta(days=1)


def resolve_ingest_end_date(
    settings_window: str | None,
    job_name: str | None = None,
) -> date:
    """Convenience wrapper used by job runners."""
    end = ingest_end_date(settings_window, job_name=job_name)
    logger.info(
        "ingest_end_date_resolved",
        window=(settings_window or "").strip().lower() or None,
        job=job_name,
        pacific_today=pacific_today().isoformat(),
        end_date=end.isoformat(),
    )
    return end
