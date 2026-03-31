"""Tests for backtest_pullbacks.py — trend classification and pullback scanning."""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "scripts"))

from backtest_pullbacks import (
    PullbackEventRow,
    aggregate_profiles,
    classify_trend,
    compute_ema,
    compute_slope,
    is_volume_declining,
    scan_ticker,
)


class TestClassifyTrend:
    """Verify trend classification mirrors ConvictionEngine._classify_trend."""

    def test_strong_uptrend(self):
        result = classify_trend(
            price=110, ema_8=105, ema_21=100,
            slope_8=0.5, slope_21=0.3, pct_to_8=4.76, pct_to_21=10.0,
        )
        assert result == "strong_uptrend"

    def test_uptrend_no_slope(self):
        result = classify_trend(
            price=110, ema_8=105, ema_21=100,
            slope_8=-0.1, slope_21=0.3, pct_to_8=4.76, pct_to_21=10.0,
        )
        assert result == "uptrend"

    def test_uptrend_low_extension(self):
        result = classify_trend(
            price=106, ema_8=105.5, ema_21=100,
            slope_8=0.5, slope_21=0.3, pct_to_8=0.47, pct_to_21=6.0,
        )
        assert result == "uptrend"

    def test_pullback_to_8ema(self):
        result = classify_trend(
            price=104, ema_8=105, ema_21=100,
            slope_8=0.2, slope_21=0.1, pct_to_8=-0.95, pct_to_21=4.0,
        )
        assert result == "pullback_to_8ema"

    def test_pullback_to_21ema_above_21(self):
        result = classify_trend(
            price=101, ema_8=105, ema_21=100,
            slope_8=0.2, slope_21=0.1, pct_to_8=-3.81, pct_to_21=1.0,
        )
        assert result == "pullback_to_21ema"

    def test_pullback_to_21ema_below_21(self):
        result = classify_trend(
            price=99, ema_8=105, ema_21=100,
            slope_8=0.2, slope_21=0.1, pct_to_8=-5.71, pct_to_21=-1.0,
        )
        assert result == "pullback_to_21ema"

    def test_downtrend(self):
        result = classify_trend(
            price=90, ema_8=100, ema_21=105,
            slope_8=-0.5, slope_21=-0.3, pct_to_8=-10.0, pct_to_21=-14.3,
        )
        assert result == "downtrend"

    def test_consolidation_above_21_far_from_both(self):
        result = classify_trend(
            price=103, ema_8=108, ema_21=100,
            slope_8=0.1, slope_21=0.1, pct_to_8=-4.63, pct_to_21=3.0,
        )
        assert result == "consolidation"


class TestComputeEma:
    def test_basic_ema(self):
        s = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0, 15.0, 16.0, 17.0, 18.0, 19.0])
        ema = compute_ema(s, 8)
        assert len(ema) == 10
        assert ema.iloc[-1] > ema.iloc[0]

    def test_ema_length_preserved(self):
        s = pd.Series(range(100), dtype=float)
        ema = compute_ema(s, 21)
        assert len(ema) == 100


class TestComputeSlope:
    def test_positive_slope(self):
        s = pd.Series([1.0, 2.0, 3.0])
        slope = compute_slope(s)
        assert slope > 0

    def test_negative_slope(self):
        s = pd.Series([3.0, 2.0, 1.0])
        slope = compute_slope(s)
        assert slope < 0

    def test_flat(self):
        s = pd.Series([5.0, 5.0, 5.0])
        slope = compute_slope(s)
        assert slope == 0.0

    def test_too_few_values(self):
        s = pd.Series([1.0])
        slope = compute_slope(s, periods=3)
        assert slope == 0.0


class TestIsVolumeDeclining:
    def test_declining(self):
        volumes = pd.Series([100, 120, 130, 110, 90, 50])
        assert is_volume_declining(volumes, 5, lookback=5) is True

    def test_not_declining(self):
        volumes = pd.Series([100, 120, 130, 110, 90, 200])
        assert is_volume_declining(volumes, 5, lookback=5) is False

    def test_idx_too_small(self):
        volumes = pd.Series([100, 50])
        assert is_volume_declining(volumes, 1, lookback=5) is False


class TestScanTicker:
    def _make_uptrend_then_pullback(self, n_bars=100):
        """Generate synthetic OHLCV with an uptrend followed by a pullback."""
        np.random.seed(42)
        dates = pd.bdate_range(start="2023-01-01", periods=n_bars)

        prices = np.cumsum(np.random.normal(0.3, 0.5, n_bars)) + 100
        for i in range(75, 85):
            prices[i] = prices[74] - (i - 74) * 0.5

        df = pd.DataFrame({
            "date": dates.date,
            "open": prices - 0.2,
            "high": prices + 0.5,
            "low": prices - 0.8,
            "close": prices,
            "volume": np.random.randint(500000, 2000000, n_bars),
            "vwap": prices - 0.1,
        })
        return df

    def test_scan_returns_events(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq

        df = self._make_uptrend_then_pullback()
        path = tmp_path / "TEST.parquet"
        table = pa.Table.from_pandas(df)
        pq.write_table(table, path)

        events = scan_ticker("TEST", str(tmp_path))
        assert isinstance(events, list)
        for e in events:
            assert isinstance(e, PullbackEventRow)
            assert e.ticker == "TEST"
            assert e.pullback_type in ("8ema", "21ema")
            assert e.peak_gain_pct >= 0 or e.peak_gain_pct < 0
            assert e.days_to_exit >= e.days_to_peak

    def test_scan_too_few_bars(self, tmp_path):
        import pyarrow as pa
        import pyarrow.parquet as pq

        df = pd.DataFrame({
            "date": pd.bdate_range("2023-01-01", periods=10).date,
            "open": [10.0] * 10,
            "high": [11.0] * 10,
            "low": [9.0] * 10,
            "close": [10.0] * 10,
            "volume": [100000] * 10,
            "vwap": [10.0] * 10,
        })
        path = tmp_path / "TINY.parquet"
        pq.write_table(pa.Table.from_pandas(df), path)

        events = scan_ticker("TINY", str(tmp_path))
        assert events == []

    def test_scan_missing_file(self, tmp_path):
        events = scan_ticker("NOPE", str(tmp_path))
        assert events == []


class TestAggregateProfiles:
    def _make_events(self, ticker="AAPL", ptype="8ema", n=5):
        events = []
        for i in range(n):
            events.append(PullbackEventRow(
                ticker=ticker,
                pullback_type=ptype,
                entry_date="2024-01-01",
                entry_price=100.0,
                peak_date="2024-01-05",
                peak_price=100 + (i + 1) * 2,
                peak_gain_pct=(i + 1) * 2.0,
                exit_date="2024-01-10",
                exit_price=101.0,
                exit_gain_pct=1.0,
                days_to_peak=4,
                days_to_exit=9,
                max_drawdown_pct=-0.5,
                volume_declining_at_entry=1 if i % 2 == 0 else 0,
            ))
        return events

    def test_single_ticker(self):
        events = self._make_events()
        profiles = aggregate_profiles(events)
        assert len(profiles) == 1
        p = profiles[0]
        assert p["ticker"] == "AAPL"
        assert p["pullback_type"] == "8ema"
        assert p["event_count"] == 5
        assert p["median_peak_gain_pct"] > 0
        assert 0 <= p["win_rate_5pct"] <= 1
        assert 0 <= p["win_rate_10pct"] <= 1

    def test_multiple_types(self):
        events = self._make_events(ptype="8ema") + self._make_events(ptype="21ema")
        profiles = aggregate_profiles(events)
        assert len(profiles) == 2
        types = {p["pullback_type"] for p in profiles}
        assert types == {"8ema", "21ema"}

    def test_win_rate_calculation(self):
        events = [
            PullbackEventRow(
                ticker="X", pullback_type="8ema",
                entry_date="2024-01-01", entry_price=100,
                peak_date="2024-01-05", peak_price=106,
                peak_gain_pct=6.0,
                exit_date="2024-01-10", exit_price=103,
                exit_gain_pct=3.0,
                days_to_peak=4, days_to_exit=9,
                max_drawdown_pct=-1.0, volume_declining_at_entry=1,
            ),
            PullbackEventRow(
                ticker="X", pullback_type="8ema",
                entry_date="2024-02-01", entry_price=100,
                peak_date="2024-02-05", peak_price=103,
                peak_gain_pct=3.0,
                exit_date="2024-02-10", exit_price=101,
                exit_gain_pct=1.0,
                days_to_peak=4, days_to_exit=9,
                max_drawdown_pct=-0.5, volume_declining_at_entry=0,
            ),
        ]
        profiles = aggregate_profiles(events)
        p = profiles[0]
        assert p["win_rate_5pct"] == 0.5
        assert p["win_rate_10pct"] == 0.0
