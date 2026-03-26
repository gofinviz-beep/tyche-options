"""Tests for the 8/21 EMA conviction engine."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tyche.conviction.engine import (
    ConvictionEngine,
    TrendState,
    compute_ema,
    compute_slope,
)


def _make_ohlcv(prices, volumes=None, start_date=None):
    n = len(prices)
    start = start_date or date(2026, 1, 1)
    dates = [start + timedelta(days=i) for i in range(n)]
    if volumes is None:
        volumes = [1_000_000] * n
    return pd.DataFrame({
        "date": dates,
        "open": [p * 0.99 for p in prices],
        "high": [p * 1.01 for p in prices],
        "low": [p * 0.98 for p in prices],
        "close": prices,
        "volume": volumes,
        "vwap": prices,
    })


def _uptrend(n=80, start=100.0, gain=0.5):
    return [start + i * gain for i in range(n)]


def _downtrend(n=80, start=200.0, loss=0.5):
    return [start - i * loss for i in range(n)]


class TestComputeEMA:
    def test_length_matches(self):
        assert len(compute_ema(pd.Series(range(50)), 8)) == 50

    def test_converges_to_constant(self):
        assert abs(compute_ema(pd.Series([100.0] * 50), 21).iloc[-1] - 100.0) < 0.01

    def test_fast_reacts_quicker(self):
        s = pd.Series([100.0] * 30 + [110.0] * 20)
        assert compute_ema(s, 8).iloc[-1] > compute_ema(s, 21).iloc[-1]


class TestComputeSlope:
    def test_positive(self):
        assert compute_slope(pd.Series([1, 2, 3, 4, 5]), 3) > 0

    def test_negative(self):
        assert compute_slope(pd.Series([5, 4, 3, 2, 1]), 3) < 0

    def test_flat(self):
        assert abs(compute_slope(pd.Series([5.0] * 5), 3)) < 0.001

    def test_insufficient(self):
        assert compute_slope(pd.Series([1.0, 2.0]), 5) == 0.0


class TestConvictionEngine:
    @pytest.fixture
    def engine(self):
        return ConvictionEngine(ema_fast=8, ema_slow=21, pullback_proximity_pct=2.0)

    def test_insufficient_data(self, engine):
        signal = engine.analyze("X", _make_ohlcv([100.0] * 10))
        assert signal.trend_state == TrendState.INSUFFICIENT_DATA
        assert signal.conviction_level == "none"
        assert signal.csp_eligible is False

    def test_strong_uptrend(self, engine):
        signal = engine.analyze("UP", _make_ohlcv(_uptrend(80)))
        assert signal.trend_state in (TrendState.STRONG_UPTREND, TrendState.UPTREND)
        assert signal.conviction_level in ("high", "medium")
        assert signal.csp_eligible is True
        assert signal.last_close > signal.ema_8 > 0

    def test_downtrend(self, engine):
        signal = engine.analyze("DN", _make_ohlcv(_downtrend(80)))
        assert signal.trend_state == TrendState.DOWNTREND
        assert signal.conviction_level == "none"
        assert signal.csp_eligible is False

    def test_signal_fields(self, engine):
        signal = engine.analyze("F", _make_ohlcv(_uptrend(80)))
        assert signal.ticker == "F"
        assert isinstance(signal.ema_8_slope, float)
        assert isinstance(signal.ema_21_slope, float)
        assert signal.avg_volume_20d > 0
        assert signal.as_of_date is not None

    def test_to_dict(self, engine):
        d = engine.analyze("D", _make_ohlcv(_uptrend(80))).to_dict()
        assert d["ticker"] == "D"
        assert isinstance(d["trend_state"], str)
        assert "ema_8" in d

    def test_batch(self, engine):
        data = {
            "UP": _make_ohlcv(_uptrend(80)),
            "DN": _make_ohlcv(_downtrend(80)),
            "SHORT": _make_ohlcv([100.0] * 10),
        }
        signals = engine.analyze_batch(data)
        assert len(signals) == 3
        assert next(s for s in signals if s.ticker == "UP").csp_eligible
        assert not next(s for s in signals if s.ticker == "DN").csp_eligible

    def test_batch_sorted(self, engine):
        data = {
            "STRONG": _make_ohlcv(_uptrend(80, start=50, gain=1.0)),
            "WEAK": _make_ohlcv(_downtrend(80)),
        }
        signals = engine.analyze_batch(data)
        order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        levels = [order.get(s.conviction_level, 99) for s in signals]
        assert levels == sorted(levels)

    def test_volume_declining_on_shallow_pullback(self, engine):
        """Shallow pullback on low volume after strong uptrend."""
        prices = _uptrend(70, start=100.0, gain=0.5)
        peak = prices[-1]
        for i in range(10):
            prices.append(peak - (i + 1) * 0.05)
        volumes = [1_000_000] * 70 + [200_000] * 10
        signal = engine.analyze("V", _make_ohlcv(prices, volumes))
        assert signal.volume_declining_on_pullback is True

    def test_ema_slopes_uptrend(self, engine):
        signal = engine.analyze("S", _make_ohlcv(_uptrend(80, gain=1.0)))
        assert signal.ema_8_slope > 0
        assert signal.ema_21_slope > 0

    def test_streak_counted(self, engine):
        signal = engine.analyze("ST", _make_ohlcv(_uptrend(80, gain=0.8)))
        assert signal.days_above_both_emas > 0


class TestTrendState:
    def test_all_string_values(self):
        for state in TrendState:
            assert isinstance(state.value, str)

    def test_coverage(self):
        eligible = {
            TrendState.STRONG_UPTREND, TrendState.UPTREND,
            TrendState.PULLBACK_TO_8EMA, TrendState.PULLBACK_TO_21EMA,
        }
        non_eligible = {
            TrendState.CONSOLIDATION, TrendState.DOWNTREND,
            TrendState.INSUFFICIENT_DATA,
        }
        assert len(eligible) + len(non_eligible) == len(TrendState)
