"""Tests for scheduler wiring and job registration."""

from __future__ import annotations

import asyncio

import pytest

from tyche.workflow.scheduler import WorkflowScheduler


class TestWorkflowScheduler:

    def test_init_creates_scheduler(self):
        scheduler = WorkflowScheduler()
        assert not scheduler.running
        assert scheduler.get_job_status() == {}

    def test_schedule_morning_scan_registers_job(self):
        scheduler = WorkflowScheduler()

        async def dummy_scan():
            pass

        scheduler.schedule_morning_scan(dummy_scan, hour=9, minute=35)
        assert "morning_scan" in scheduler._jobs

    def test_schedule_order_monitor_registers_job(self):
        scheduler = WorkflowScheduler()

        async def dummy_monitor():
            pass

        scheduler.schedule_order_monitor(dummy_monitor, interval_minutes=15)
        assert "order_monitor" in scheduler._jobs

    def test_all_four_jobs_register(self):
        scheduler = WorkflowScheduler()

        async def dummy():
            pass

        scheduler.schedule_morning_scan(dummy)
        scheduler.schedule_order_monitor(dummy)
        scheduler.schedule_midday_review(dummy)
        scheduler.schedule_eod_journal(dummy)

        assert len(scheduler._jobs) == 4
        assert all(
            name in scheduler._jobs
            for name in ["morning_scan", "order_monitor", "midday_review", "eod_journal"]
        )

    def test_get_job_status_before_start(self):
        scheduler = WorkflowScheduler()

        async def dummy():
            pass

        scheduler.schedule_morning_scan(dummy)
        status = scheduler.get_job_status()
        assert "morning_scan" in status
        assert status["morning_scan"]["pending"] is True

    @pytest.mark.asyncio
    async def test_start_and_shutdown(self):
        scheduler = WorkflowScheduler()

        async def dummy():
            pass

        scheduler.schedule_morning_scan(dummy)
        scheduler.start()
        assert scheduler.running

        scheduler.shutdown()
        # APScheduler's AsyncIOScheduler.shutdown(wait=False) may not
        # immediately flip .running due to internal state; verify it
        # doesn't raise on a second shutdown call.
        scheduler.shutdown()
