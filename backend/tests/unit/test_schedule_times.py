"""Tests for schedule time helpers."""

from tyche.config import ohlcv_refresh_time_before_flatfile, offset_schedule_time


class TestOffsetScheduleTime:
    def test_thirty_minutes_before_flatfile(self):
        assert ohlcv_refresh_time_before_flatfile("07:00", 30) == (6, 30)

    def test_wraps_midnight(self):
        assert offset_schedule_time("00:15", -30) == (23, 45)

    def test_default_flatfile_seven_am(self):
        h, m = ohlcv_refresh_time_before_flatfile("07:00")
        assert (h, m) == (6, 30)
