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
from tyche.conviction.features import ConvictionFeatureEngine


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


def _fresh_uptrend(n=80, start=100.0, gain=0.5, streak_days=8):
    """Uptrend with a mid-series dip that resets the days-above-EMAs streak.

    Produces a series where the last ``streak_days`` bars are clearly above
    both EMAs, keeping the streak in the 5-10 sweet spot required by Gate 3.
    """
    dip_at = n - streak_days - 2
    prices = []
    for i in range(n):
        base = start + i * gain
        if dip_at <= i < dip_at + 2:
            base -= gain * 20
        prices.append(base)
    return prices


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
        signal = engine.analyze("UP", _make_ohlcv(_fresh_uptrend(80)))
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

    def test_raw_conviction_preserved_on_csp_override(self, engine):
        """raw_conviction keeps genuine assessment even when conviction_level is overridden."""
        prices = _uptrend(70, start=100.0, gain=0.5)
        peak = prices[-1]
        for i in range(10):
            prices.append(peak - (i + 1) * 0.05)
        signal = engine.analyze("PB", _make_ohlcv(prices))
        if signal.trend_state in (TrendState.PULLBACK_TO_8EMA, TrendState.PULLBACK_TO_21EMA):
            assert signal.raw_conviction in ("high", "medium", "low")
            if not signal.csp_eligible:
                assert signal.conviction_level == "low"
                assert signal.raw_conviction != "none"

    def test_raw_conviction_matches_when_csp_eligible(self, engine):
        signal = engine.analyze("UP", _make_ohlcv(_fresh_uptrend(80)))
        if signal.csp_eligible:
            assert signal.raw_conviction == signal.conviction_level

    def test_raw_conviction_in_insufficient_data(self, engine):
        signal = engine.analyze("X", _make_ohlcv([100.0] * 10))
        assert signal.raw_conviction == "none"
        assert signal.conviction_level == "none"

    def test_to_dict(self, engine):
        d = engine.analyze("D", _make_ohlcv(_uptrend(80))).to_dict()
        assert d["ticker"] == "D"
        assert isinstance(d["trend_state"], str)
        assert "ema_8" in d
        assert "raw_conviction" in d

    def test_batch(self, engine):
        data = {
            "UP": _make_ohlcv(_fresh_uptrend(80)),
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


class TestComputePriorStreak:
    """Tests for _compute_prior_streak static method."""

    def test_simple_pullback_after_uptrend(self):
        above = pd.Series([False] * 5 + [True] * 10 + [False] * 3)
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 10

    def test_no_prior_uptrend(self):
        above = pd.Series([False] * 10)
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 0

    def test_all_above(self):
        above = pd.Series([True] * 20)
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 20

    def test_single_day_pullback(self):
        above = pd.Series([True] * 8 + [False])
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 8

    def test_multiple_pullback_bars(self):
        above = pd.Series([True] * 6 + [False] * 5)
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 6

    def test_earlier_streak_ignored(self):
        above = pd.Series([True] * 3 + [False] * 2 + [True] * 7 + [False] * 2)
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 7

    def test_empty_series(self):
        above = pd.Series([], dtype=bool)
        assert ConvictionFeatureEngine._compute_prior_streak(above) == 0


class TestPullbackCSPEligibility:
    """Tests for pullback CSP path (Gate 3b)."""

    @pytest.fixture
    def engine(self):
        return ConvictionEngine(
            ema_fast=8, ema_slow=21,
            pullback_proximity_pct=2.0,
            pullback_csp_enabled=True,
            min_prior_streak=5,
        )

    @pytest.fixture
    def engine_disabled(self):
        return ConvictionEngine(
            ema_fast=8, ema_slow=21,
            pullback_proximity_pct=2.0,
            pullback_csp_enabled=False,
            min_prior_streak=5,
        )

    def _pullback_to_21ema(self, n=80, start=100.0, gain=0.5, pullback_bars=3, prior_streak=8):
        """Uptrend that ends with a pullback toward the 21-EMA.

        The stock rises for ``prior_streak`` days above both EMAs, then
        pulls back gently toward the 21-EMA.
        """
        base_len = n - pullback_bars
        prices = [start + i * gain for i in range(base_len)]
        peak = prices[-1]
        ema_21_approx = start + (base_len - 12) * gain
        for i in range(pullback_bars):
            frac = (i + 1) / pullback_bars
            prices.append(peak - frac * (peak - ema_21_approx) * 0.85)
        return prices

    def test_pullback_csp_eligible_with_prior_streak(self, engine):
        prices = self._pullback_to_21ema(n=80, prior_streak=8)
        signal = engine.analyze("PB", _make_ohlcv(prices))
        if signal.trend_state in (TrendState.PULLBACK_TO_8EMA, TrendState.PULLBACK_TO_21EMA):
            assert signal.prior_streak > 0
            if signal.prior_streak >= 5 and signal.ema_21_slope > 0:
                assert signal.csp_eligible is True
                assert signal.conviction_level in ("high", "medium")

    def test_pullback_csp_disabled(self, engine_disabled):
        prices = self._pullback_to_21ema(n=80, prior_streak=8)
        signal = engine_disabled.analyze("PB", _make_ohlcv(prices))
        if signal.trend_state in (TrendState.PULLBACK_TO_8EMA, TrendState.PULLBACK_TO_21EMA):
            assert signal.csp_eligible is False
            gate_names = [g.gate for g in signal.gate_results]
            assert "Pullback Prior Streak" in gate_names
            pullback_gate = next(g for g in signal.gate_results if g.gate == "Pullback Prior Streak")
            assert pullback_gate.passed is False
            assert "disabled" in pullback_gate.actual

    def test_prior_streak_too_short(self):
        engine = ConvictionEngine(
            ema_fast=8, ema_slow=21,
            pullback_proximity_pct=2.0,
            pullback_csp_enabled=True,
            min_prior_streak=20,
        )
        prices = [100.0 + i * 0.3 for i in range(70)]
        peak = prices[-1]
        for i in range(10):
            prices.append(peak - (i + 1) * 0.3)
        signal = engine.analyze("SHORT", _make_ohlcv(prices))
        if signal.trend_state in (TrendState.PULLBACK_TO_8EMA, TrendState.PULLBACK_TO_21EMA):
            assert signal.csp_eligible is False

    def test_uptrend_path_unaffected(self, engine):
        """Uptrend CSPs still work through Gate 3a."""
        signal = engine.analyze("UP", _make_ohlcv(_fresh_uptrend(80)))
        assert signal.trend_state in (TrendState.STRONG_UPTREND, TrendState.UPTREND)
        assert signal.csp_eligible is True
        gate_names = [g.gate for g in signal.gate_results]
        assert "Days Above EMAs" in gate_names

    def test_prior_streak_in_to_dict(self, engine):
        signal = engine.analyze("D", _make_ohlcv(_uptrend(80)))
        d = signal.to_dict()
        assert "prior_streak" in d

    def test_pullback_extension_cap_bypassed(self, engine):
        """Pullback CSPs skip extension cap gate (stock is pulling back, not extended)."""
        prices = self._pullback_to_21ema(n=80)
        signal = engine.analyze("PB", _make_ohlcv(prices))
        if signal.trend_state in (TrendState.PULLBACK_TO_8EMA, TrendState.PULLBACK_TO_21EMA):
            ext_gate = next(g for g in signal.gate_results if g.gate == "Extension Cap")
            assert ext_gate.passed is True
            assert "pullback" in ext_gate.actual.lower()


class TestBatchSortingWithPullbacks:
    """Tests for the improved sorting: pullback CSPs ranked by conviction then prior streak."""

    @pytest.fixture
    def engine(self):
        return ConvictionEngine(
            ema_fast=8, ema_slow=21,
            pullback_proximity_pct=2.0,
            pullback_csp_enabled=True,
            min_prior_streak=5,
        )

    def test_batch_sorted_by_conviction_first(self, engine):
        data = {
            "STRONG": _make_ohlcv(_fresh_uptrend(80, start=50, gain=1.0)),
            "WEAK": _make_ohlcv(_downtrend(80)),
        }
        signals = engine.analyze_batch(data)
        order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        levels = [order.get(s.conviction_level, 99) for s in signals]
        assert levels == sorted(levels)


class TestEngineCache:
    """Tests for per-ticker conviction result caching."""

    def test_cache_hit_returns_equivalent_signal(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_uptrend(80))
        sig1 = engine.analyze("AAPL", df)
        sig2 = engine.analyze("AAPL", df)
        assert sig1.to_dict() == sig2.to_dict()
        assert engine.cache_size == 1

    def test_different_tickers_cached_separately(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_uptrend(80))
        sig_a = engine.analyze("AAPL", df)
        sig_b = engine.analyze("GOOG", df)
        assert sig_a.ticker != sig_b.ticker
        assert engine.cache_size == 2
        sig_a2 = engine.analyze("AAPL", df)
        assert sig_a2.to_dict() == sig_a.to_dict()

    def test_date_change_invalidates_cache(self):
        engine = ConvictionEngine()
        df1 = _make_ohlcv(_uptrend(80), start_date=date(2026, 1, 1))
        sig1 = engine.analyze("AAPL", df1)
        assert engine.cache_size == 1

        df2 = _make_ohlcv(_uptrend(80), start_date=date(2026, 1, 2))
        sig2 = engine.analyze("AAPL", df2)
        assert sig2 is not sig1
        assert engine.cache_size == 1

    def test_invalidate_cache_clears_all(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_uptrend(80))
        engine.analyze("AAPL", df)
        engine.analyze("GOOG", df)
        assert engine.cache_size == 2

        engine.invalidate_cache()
        assert engine.cache_size == 0

    def test_batch_populates_cache(self):
        engine = ConvictionEngine()
        data = {
            "AAPL": _make_ohlcv(_uptrend(80)),
            "GOOG": _make_ohlcv(_uptrend(80)),
        }
        engine.analyze_batch(data)
        assert engine.cache_size == 2

    def test_batch_reuses_cached_signals(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_uptrend(80))
        original = engine.analyze("AAPL", df)
        assert engine.cache_size == 1

        data = {"AAPL": df, "GOOG": df}
        signals = engine.analyze_batch(data)
        aapl_sig = next(s for s in signals if s.ticker == "AAPL")
        assert aapl_sig.to_dict() == original.to_dict()

    def test_insufficient_data_not_cached(self):
        engine = ConvictionEngine(min_bars=50)
        df = _make_ohlcv(_uptrend(10))
        engine.analyze("AAPL", df)
        assert engine.cache_size == 0


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
