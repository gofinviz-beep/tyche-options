"""Tests for Pacific ingest session date resolution."""

from __future__ import annotations

from datetime import date, datetime
from unittest.mock import patch
from zoneinfo import ZoneInfo

from tyche.market_data.ingest_dates import (
    ingest_end_date,
    pacific_today,
    pacific_yesterday,
)

# 2026-06-10 18:02 PDT (evening pipeline) = 2026-06-11 01:02 UTC
EVENING_UTC = datetime(2026, 6, 11, 1, 2, tzinfo=ZoneInfo("UTC"))
# 2026-06-11 02:30 PDT (morning pipeline) = 2026-06-11 09:30 UTC
MORNING_UTC = datetime(2026, 6, 11, 9, 30, tzinfo=ZoneInfo("UTC"))


class TestPacificToday:
    def test_evening_before_utc_midnight_still_pacific_today(self):
        assert pacific_today(now=EVENING_UTC) == date(2026, 6, 10)

    def test_morning_after_utc_midnight(self):
        assert pacific_today(now=MORNING_UTC) == date(2026, 6, 11)


class TestIngestEndDate:
    @patch("tyche.market_data.ingest_dates.pacific_today")
    def test_evening_window_pacific_today(self, mock_today):
        mock_today.return_value = date(2026, 6, 10)
        assert ingest_end_date("evening") == date(2026, 6, 10)

    @patch("tyche.market_data.ingest_dates.pacific_today")
    def test_morning_window_pacific_yesterday(self, mock_today):
        mock_today.return_value = date(2026, 6, 11)
        assert ingest_end_date("morning") == date(2026, 6, 10)

    @patch("tyche.market_data.ingest_dates.pacific_today")
    def test_job_name_evening_fallback(self, mock_today):
        mock_today.return_value = date(2026, 6, 10)
        assert ingest_end_date(None, job_name="ingest-data") == date(2026, 6, 10)

    @patch("tyche.market_data.ingest_dates.pacific_today")
    def test_job_name_morning_fallback(self, mock_today):
        mock_today.return_value = date(2026, 6, 11)
        assert ingest_end_date(None, job_name="ingest-options-flatfiles") == date(
            2026, 6, 10
        )

    @patch("tyche.market_data.ingest_dates.pacific_today")
    def test_unset_defaults_to_pacific_yesterday(self, mock_today):
        mock_today.return_value = date(2026, 6, 11)
        assert ingest_end_date(None) == date(2026, 6, 10)

    def test_pacific_yesterday_follows_today(self):
        assert pacific_yesterday(now=EVENING_UTC) == date(2026, 6, 9)

    def test_dst_boundary_still_pacific_calendar(self):
        # 2026-03-08 10:00 UTC = 02:00 PST (spring forward morning)
        spring = datetime(2026, 3, 8, 10, 0, tzinfo=ZoneInfo("UTC"))
        assert pacific_today(now=spring) == date(2026, 3, 8)
        # 2026-11-01 08:00 UTC = 01:00 PDT (fall back morning)
        fall = datetime(2026, 11, 1, 8, 0, tzinfo=ZoneInfo("UTC"))
        assert pacific_today(now=fall) == date(2026, 11, 1)
