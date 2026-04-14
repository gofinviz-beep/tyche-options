"""APScheduler setup — schedules trading workflows during market hours."""

from __future__ import annotations

from datetime import time
from typing import Any, Callable, Coroutine

import structlog
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

logger = structlog.get_logger()


class WorkflowScheduler:
    """Manages scheduled trading workflows.

    All jobs run only during US market hours (Mon-Fri, 9:30-16:00 ET).
    """

    def __init__(self) -> None:
        self._scheduler = AsyncIOScheduler(timezone="US/Eastern")
        self._jobs: dict[str, str] = {}

    def schedule_morning_scan(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 9,
        minute: int = 35,
    ) -> None:
        """Schedule the morning scan (default 9:35 AM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="morning_scan",
            replace_existing=True,
            name="Morning CSP/CC Scan",
        )
        self._jobs["morning_scan"] = job.id
        logger.info("scheduled_morning_scan", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_order_monitor(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        interval_minutes: int = 15,
    ) -> None:
        """Schedule the order monitor (default every 15 min during market hours)."""
        job = self._scheduler.add_job(
            func,
            IntervalTrigger(minutes=interval_minutes),
            id="order_monitor",
            replace_existing=True,
            name="Order Monitor",
        )
        self._jobs["order_monitor"] = job.id
        logger.info("scheduled_order_monitor", interval_min=interval_minutes)

    def schedule_midday_review(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 12,
        minute: int = 30,
    ) -> None:
        """Schedule the midday position review (default 12:30 PM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="midday_review",
            replace_existing=True,
            name="Midday Position Review",
        )
        self._jobs["midday_review"] = job.id
        logger.info("scheduled_midday_review", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_daily_digest(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 16,
        minute: int = 0,
    ) -> None:
        """Schedule the daily conviction digest email (default 4:00 PM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="daily_digest",
            replace_existing=True,
            name="Daily Conviction Digest",
        )
        self._jobs["daily_digest"] = job.id
        logger.info("scheduled_daily_digest", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_ohlcv_refresh(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 16,
        minute: int = 2,
    ) -> None:
        """Schedule the OHLCV data refresh (default 4:02 PM ET, right after close)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="ohlcv_refresh",
            replace_existing=True,
            name="OHLCV Daily Refresh",
        )
        self._jobs["ohlcv_refresh"] = job.id
        logger.info("scheduled_ohlcv_refresh", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_exit_monitor(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 16,
        minute: int = 5,
    ) -> None:
        """Schedule the stock exit monitor (default 4:05 PM ET, after close)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="exit_monitor",
            replace_existing=True,
            name="Stock Exit Monitor",
        )
        self._jobs["exit_monitor"] = job.id
        logger.info("scheduled_exit_monitor", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_options_snapshot(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 16,
        minute: int = 10,
    ) -> None:
        """Schedule the daily options chain snapshot (default 4:10 PM ET).

        Runs after OHLCV refresh (4:02) and exit monitor (4:05) to avoid
        contention.  Captures live options chains from Tradier for all
        large-cap tickers and persists them to the OptionsChainStore.
        """
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="options_snapshot",
            replace_existing=True,
            name="Options Chain Snapshot",
        )
        self._jobs["options_snapshot"] = job.id
        logger.info("scheduled_options_snapshot", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_news_ingest(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        interval_minutes: int = 30,
    ) -> None:
        """Schedule the news ingestion pipeline.

        Runs every ``interval_minutes`` during market hours (Mon-Fri 9:30-16:00).
        Off-hours runs are less frequent (every 2h) — handled by the pipeline
        itself, not the scheduler.
        """
        job = self._scheduler.add_job(
            func,
            IntervalTrigger(minutes=interval_minutes),
            id="news_ingest",
            replace_existing=True,
            name="News Ingest Pipeline",
        )
        self._jobs["news_ingest"] = job.id
        logger.info("scheduled_news_ingest", interval_min=interval_minutes)

    def schedule_edgar_ingest(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        interval_minutes: int = 120,
    ) -> None:
        """Schedule the EDGAR filing ingestion pipeline.

        Runs every ``interval_minutes`` (default 2 hours). 8-K filings are
        infrequent per company so a less aggressive schedule than news is appropriate.
        """
        job = self._scheduler.add_job(
            func,
            IntervalTrigger(minutes=interval_minutes),
            id="edgar_ingest",
            replace_existing=True,
            name="EDGAR Ingest Pipeline",
        )
        self._jobs["edgar_ingest"] = job.id
        logger.info("scheduled_edgar_ingest", interval_min=interval_minutes)

    def schedule_ml_retrain(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        day: int = 1,
        hour: int = 2,
        minute: int = 0,
    ) -> None:
        """Schedule the monthly ML model retrain (default 1st of month, 2 AM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day=day,
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="ml_retrain",
            replace_existing=True,
            name="ML Model Retrain",
        )
        self._jobs["ml_retrain"] = job.id
        logger.info("scheduled_ml_retrain", day=day, time=f"{hour:02d}:{minute:02d} ET")

    def schedule_conviction_batch(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 16,
        minute: int = 8,
    ) -> None:
        """Schedule conviction batch after OHLCV refresh (default 4:08 PM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="conviction_batch",
            replace_existing=True,
            name="Conviction Batch Upsert",
        )
        self._jobs["conviction_batch"] = job.id
        logger.info("scheduled_conviction_batch", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_bridge_tradier_iv(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 16,
        minute: int = 45,
    ) -> None:
        """Schedule Tradier IV bridge after options snapshot (default 4:45 PM ET).

        Must run after the options snapshot job (~30 min for 1k+ tickers at
        120 RPM) has written snapshot data to OptionsChainStore.
        """
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="bridge_tradier_iv",
            replace_existing=True,
            name="Bridge Tradier IV",
        )
        self._jobs["bridge_tradier_iv"] = job.id
        logger.info("scheduled_bridge_tradier_iv", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_correlation_refresh(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        day: int = 28,
        hour: int = 22,
        minute: int = 0,
    ) -> None:
        """Schedule monthly correlation refresh (default 28th of month, 10 PM ET).

        Runs before the monthly ML retrain (1st of month) so fresh
        correlation features are available for dataset building.
        """
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day=day,
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="correlation_refresh",
            replace_existing=True,
            name="Correlation Refresh",
        )
        self._jobs["correlation_refresh"] = job.id
        logger.info("scheduled_correlation_refresh", day=day, time=f"{hour:02d}:{minute:02d} ET")

    def schedule_etf_refresh(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 3,
        minute: int = 0,
    ) -> None:
        """Schedule quarterly ETF constituent refresh (Mar/Jun/Sep/Dec 1st, 3 AM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                month="3,6,9,12",
                day=1,
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="etf_refresh",
            replace_existing=True,
            name="ETF Constituent Refresh",
        )
        self._jobs["etf_refresh"] = job.id
        logger.info("scheduled_etf_refresh", months="3,6,9,12", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_quarterly_meta(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 3,
        minute: int = 30,
    ) -> None:
        """Schedule quarterly ticker meta refresh — sector + institutional (Mar/Jun/Sep/Dec)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                month="3,6,9,12",
                day=1,
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="quarterly_meta",
            replace_existing=True,
            name="Quarterly Meta Refresh",
        )
        self._jobs["quarterly_meta"] = job.id
        logger.info("scheduled_quarterly_meta", months="3,6,9,12", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_weekly_meta(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 2,
        minute: int = 0,
    ) -> None:
        """Schedule weekly ticker metadata refresh (Sundays 2 AM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="sun",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="weekly_meta",
            replace_existing=True,
            name="Weekly Meta Refresh",
        )
        self._jobs["weekly_meta"] = job.id
        logger.info("scheduled_weekly_meta", time=f"{hour:02d}:{minute:02d} ET")

    def schedule_eod_journal(
        self,
        func: Callable[..., Coroutine[Any, Any, Any]],
        hour: int = 15,
        minute: int = 50,
    ) -> None:
        """Schedule the end-of-day journal (default 3:50 PM ET)."""
        job = self._scheduler.add_job(
            func,
            CronTrigger(
                day_of_week="mon-fri",
                hour=hour,
                minute=minute,
                timezone="US/Eastern",
            ),
            id="eod_journal",
            replace_existing=True,
            name="EOD Journal",
        )
        self._jobs["eod_journal"] = job.id
        logger.info("scheduled_eod_journal", time=f"{hour:02d}:{minute:02d} ET")

    def start(self) -> None:
        self._scheduler.start()
        logger.info("workflow_scheduler_started", jobs=list(self._jobs.keys()))

    def shutdown(self) -> None:
        self._scheduler.shutdown(wait=False)
        logger.info("workflow_scheduler_shutdown")

    @property
    def running(self) -> bool:
        return self._scheduler.running

    def get_job_status(self) -> dict[str, Any]:
        """Get status of all scheduled jobs."""
        status: dict[str, Any] = {}
        for name, job_id in self._jobs.items():
            job = self._scheduler.get_job(job_id)
            if job:
                next_run = getattr(job, "next_run_time", None)
                status[name] = {
                    "next_run": str(next_run) if next_run else None,
                    "pending": job.pending,
                }
            else:
                status[name] = {"status": "not_found"}
        return status
