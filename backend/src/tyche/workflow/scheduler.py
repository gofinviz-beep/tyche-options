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
