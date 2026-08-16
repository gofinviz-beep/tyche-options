"""Tests for tyche.ml.features — vectorised feature extraction."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tyche.conviction.features import TrendState
from tyche.ml.features import (
    FEATURE_COLS,
    NEIGHBOR_FEATURE_COLS,
    _classify_trend_vec,
    _ema,
    _prior_streak_series,
    _rsi_series,
    _slope_series,
    _streak_above,
    add_neighbor_features,
    build_sector_map,
    extract_ticker_features,
)


def _make_ohlcv(n: int = 100, base: float = 100.0, trend: float = 0.002) -> pd.DataFrame:
    """Generate synthetic uptrending OHLCV data."""
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(n)]
    rng = np.random.default_rng(42)
    noise = rng.normal(0, 0.5, n)
    closes = base + np.cumsum(np.full(n, trend * base) + noise)
    return pd.DataFrame({
        "date": dates,
        "open": closes - rng.uniform(0, 1, n),
        "high": closes + rng.uniform(0, 2, n),
        "low": closes - rng.uniform(0, 2, n),
        "close": closes,
        "volume": rng.integers(500_000, 5_000_000, n),
    })


class TestEMA:
    def test_output_length(self):
        s = pd.Series(range(50), dtype=float)
        result = _ema(s, 8)
        assert len(result) == 50

    def test_matches_pandas_ewm(self):
        s = pd.Series(np.random.default_rng(1).random(100))
        expected = s.ewm(span=21, adjust=False).mean()
        result = _ema(s, 21)
        pd.testing.assert_series_equal(result, expected)


class TestRSI:
    def test_rsi_range(self):
        close = pd.Series(np.random.default_rng(2).random(100) * 50 + 50)
        rsi = _rsi_series(close, 14)
        valid = rsi.dropna()
        assert (valid >= 0).all() and (valid <= 100).all()

    def test_rising_prices_high_rsi(self):
        close = pd.Series(np.linspace(50, 100, 50))
        rsi = _rsi_series(close, 14)
        assert rsi.iloc[-1] > 70

    def test_falling_prices_low_rsi(self):
        close = pd.Series(np.linspace(100, 50, 50))
        rsi = _rsi_series(close, 14)
        assert rsi.iloc[-1] < 30


class TestSlopeSeries:
    def test_positive_slope_on_uptrend(self):
        s = pd.Series(np.linspace(10, 20, 20))
        slopes = _slope_series(s, 3)
        assert slopes.iloc[-1] > 0

    def test_flat_series_zero_slope(self):
        s = pd.Series(np.full(20, 50.0))
        slopes = _slope_series(s, 3)
        assert abs(slopes.iloc[-1]) < 1e-10


class TestStreakAbove:
    def test_continuous_true(self):
        mask = pd.Series([True] * 10)
        streaks = _streak_above(mask)
        assert streaks.iloc[-1] == 10

    def test_reset_on_false(self):
        mask = pd.Series([True, True, True, False, True, True])
        streaks = _streak_above(mask)
        assert streaks.iloc[2] == 3
        assert streaks.iloc[3] == 0
        assert streaks.iloc[5] == 2


class TestPriorStreak:
    def test_prior_streak_after_pullback(self):
        above = pd.Series([True, True, True, True, True, False, False])
        result = _prior_streak_series(above)
        assert result.iloc[5] == 5
        assert result.iloc[6] == 5

    def test_no_prior_streak_while_above(self):
        above = pd.Series([True, True, True])
        result = _prior_streak_series(above)
        assert (result == 0).all()


class TestClassifyTrendVec:
    def test_strong_uptrend(self):
        n = 5
        close = pd.Series([110.0] * n)
        ema_8 = pd.Series([105.0] * n)
        ema_21 = pd.Series([100.0] * n)
        slope_8 = pd.Series([0.5] * n)
        slope_21 = pd.Series([0.3] * n)
        pct_8 = (close - ema_8) / ema_8 * 100
        pct_21 = (close - ema_21) / ema_21 * 100
        result = _classify_trend_vec(close, ema_8, ema_21, slope_8, slope_21, pct_8, pct_21)
        assert (result == TrendState.STRONG_UPTREND.value).all()

    def test_downtrend(self):
        n = 5
        close = pd.Series([90.0] * n)
        ema_8 = pd.Series([100.0] * n)
        ema_21 = pd.Series([105.0] * n)
        slope_8 = pd.Series([-0.5] * n)
        slope_21 = pd.Series([-0.3] * n)
        pct_8 = (close - ema_8) / ema_8 * 100
        pct_21 = (close - ema_21) / ema_21 * 100
        result = _classify_trend_vec(close, ema_8, ema_21, slope_8, slope_21, pct_8, pct_21)
        assert (result == TrendState.DOWNTREND.value).all()

    def test_pullback_to_8ema(self):
        n = 5
        close = pd.Series([99.5] * n)
        ema_8 = pd.Series([100.0] * n)
        ema_21 = pd.Series([97.0] * n)
        slope_8 = pd.Series([0.1] * n)
        slope_21 = pd.Series([0.1] * n)
        pct_8 = (close - ema_8) / ema_8 * 100
        pct_21 = (close - ema_21) / ema_21 * 100
        result = _classify_trend_vec(close, ema_8, ema_21, slope_8, slope_21, pct_8, pct_21)
        assert (result == TrendState.PULLBACK_TO_8EMA.value).all()


class TestExtractTickerFeatures:
    def test_output_has_all_feature_cols(self):
        ohlcv = _make_ohlcv(120)
        result = extract_ticker_features(ohlcv)
        for col in FEATURE_COLS:
            assert col in result.columns, f"Missing column: {col}"

    def test_output_rows_trimmed_by_min_bars(self):
        ohlcv = _make_ohlcv(120)
        result = extract_ticker_features(ohlcv, min_bars=60)
        assert len(result) == 120 - 60

    def test_insufficient_bars_returns_empty(self):
        ohlcv = _make_ohlcv(30)
        result = extract_ticker_features(ohlcv, min_bars=50)
        assert result.empty

    def test_with_derived_metrics(self):
        ohlcv = _make_ohlcv(100)
        derived = pd.DataFrame({
            "date": ohlcv["date"],
            "iv_rank": np.random.default_rng(3).random(100) * 100,
            "iv_percentile": np.random.default_rng(4).random(100) * 100,
            "atm_iv": np.random.default_rng(5).random(100) * 0.5,
            "vrp": np.random.default_rng(6).random(100) * 20,
            "rv_20d": np.random.default_rng(7).random(100) * 0.3,
        })
        result = extract_ticker_features(ohlcv, derived=derived, min_bars=50)
        assert result["iv_rank"].notna().any()

    def test_with_meta_data(self):
        ohlcv = _make_ohlcv(100)
        sector_map = {"Technology": 1, "Energy": 2}
        result = extract_ticker_features(
            ohlcv,
            market_cap=50e9,
            institutional_pct=0.85,
            sector="Technology",
            sector_map=sector_map,
            min_bars=50,
        )
        assert result["log_market_cap"].iloc[0] == np.log1p(50e9)
        assert result["sector_encoded"].iloc[0] == 1


class TestBuildSectorMap:
    def test_deterministic_ordering(self):
        sectors = {"AAPL": "Technology", "XOM": "Energy", "JNJ": "Healthcare"}
        mapping = build_sector_map(sectors)
        assert mapping["Energy"] == 1
        assert mapping["Healthcare"] == 2
        assert mapping["Technology"] == 3

    def test_empty_sectors_skipped(self):
        sectors = {"AAPL": "Technology", "XOM": ""}
        mapping = build_sector_map(sectors)
        assert "" not in mapping


class TestAddNeighborFeatures:
    def test_adds_neighbor_columns(self):
        rng = np.random.default_rng(10)
        n = 50
        df = pd.DataFrame({
            "date": [date(2024, 1, 1)] * n,
            "ticker": [f"T{i}" for i in range(n)],
            "sector_encoded": [1] * 25 + [2] * 25,
            "rsi_14": rng.random(n) * 100,
            "ema_8_slope": rng.random(n),
            "ema_21_slope": rng.random(n),
            "return_5d": rng.random(n) * 0.1,
            "price_to_8ema_pct": rng.random(n) * 5 - 2,
            "price_to_21ema_pct": rng.random(n) * 5 - 2,
            "iv_rank": rng.random(n) * 100,
            "vrp": rng.random(n) * 20,
        })
        result = add_neighbor_features(df)
        for col in NEIGHBOR_FEATURE_COLS:
            assert col in result.columns, f"Missing neighbor col: {col}"

    def test_sector_breadth_is_fraction(self):
        df = pd.DataFrame({
            "date": [date(2024, 1, 1)] * 4,
            "ticker": ["A", "B", "C", "D"],
            "sector_encoded": [1, 1, 1, 1],
            "rsi_14": [50.0] * 4,
            "ema_8_slope": [0.1] * 4,
            "ema_21_slope": [0.1] * 4,
            "return_5d": [0.01] * 4,
            "price_to_8ema_pct": [1.0, 1.0, -1.0, -1.0],
            "price_to_21ema_pct": [2.0, 2.0, 2.0, -1.0],
            "iv_rank": [50.0] * 4,
            "vrp": [10.0] * 4,
        })
        result = add_neighbor_features(df)
        assert result["sector_breadth_8ema"].iloc[0] == 0.5
        assert result["sector_breadth_21ema"].iloc[0] == 0.75
