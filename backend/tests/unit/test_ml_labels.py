"""Tests for tyche.ml.labels — forward-looking label construction."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from tyche.ml.labels import (
    CSP_STRIKE_OFFSET,
    compute_labels_vectorized,
)


def _make_ohlcv(
    n: int = 100,
    base: float = 100.0,
    trend: float = 0.0,
) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(n)]
    rng = np.random.default_rng(42)
    closes = base + np.cumsum(np.full(n, trend) + rng.normal(0, 0.3, n))
    highs = closes + rng.uniform(0, 1, n)
    lows = closes - rng.uniform(0, 1, n)
    return pd.DataFrame({
        "date": dates,
        "open": closes,
        "high": highs,
        "low": lows,
        "close": closes,
        "volume": rng.integers(100_000, 1_000_000, n),
    })


class TestComputeLabelsVectorized:
    def test_output_columns(self):
        ohlcv = _make_ohlcv(100)
        labels = compute_labels_vectorized(ohlcv)
        expected = [
            "forward_return_5d", "forward_return_10d", "forward_return_14d", "forward_return_20d",
            "max_drawdown_5d", "max_drawdown_10d", "max_drawdown_14d", "max_drawdown_20d",
            "max_gain_5d", "max_gain_10d", "max_gain_14d", "max_gain_20d",
            "direction_5d", "direction_10d", "direction_14d", "direction_20d",
            "csp_win_5d", "csp_win_14d",
            "pullback_recovery_5d", "pullback_recovery_10d",
        ]
        for col in expected:
            assert col in labels.columns, f"Missing: {col}"

    def test_forward_return_correctness(self):
        ohlcv = _make_ohlcv(30, base=100.0, trend=0.0)
        labels = compute_labels_vectorized(ohlcv)
        close = ohlcv["close"]
        expected_5d = (close.shift(-5) - close) / close
        actual = labels["forward_return_5d"]
        valid_mask = actual.notna() & expected_5d.notna()
        np.testing.assert_allclose(
            actual[valid_mask].values,
            expected_5d[valid_mask].values,
            rtol=1e-10,
        )

    def test_direction_categories(self):
        ohlcv = _make_ohlcv(100)
        labels = compute_labels_vectorized(ohlcv)
        valid = labels["direction_5d"].dropna()
        assert set(valid.unique()).issubset({-1, 0, 1})

    def test_csp_win_is_binary(self):
        ohlcv = _make_ohlcv(100)
        labels = compute_labels_vectorized(ohlcv)
        valid = labels["csp_win_14d"].dropna()
        assert set(valid.unique()).issubset({0.0, 1.0})

    def test_csp_win_with_flat_market(self):
        """In a flat market with small moves, 5% OTM CSP should mostly win."""
        n = 100
        dates = [date(2024, 1, 2) + timedelta(days=i) for i in range(n)]
        closes = np.full(n, 100.0)
        lows = np.full(n, 99.5)
        ohlcv = pd.DataFrame({
            "date": dates,
            "open": closes,
            "high": np.full(n, 100.5),
            "low": lows,
            "close": closes,
            "volume": np.full(n, 1_000_000),
        })
        ema_21 = pd.Series(np.full(n, 100.0))
        labels = compute_labels_vectorized(ohlcv, support_ema=ema_21)
        valid = labels["csp_win_14d"].dropna()
        assert valid.mean() == 1.0

    def test_max_drawdown_is_negative_or_zero(self):
        ohlcv = _make_ohlcv(100)
        labels = compute_labels_vectorized(ohlcv)
        valid = labels["max_drawdown_14d"].dropna()
        assert (valid <= 0).all()

    def test_nan_at_end_due_to_insufficient_forward_data(self):
        ohlcv = _make_ohlcv(50)
        labels = compute_labels_vectorized(ohlcv)
        assert labels["forward_return_20d"].iloc[-1] != labels["forward_return_20d"].iloc[-1]

    def test_pullback_recovery_is_binary(self):
        ohlcv = _make_ohlcv(100)
        labels = compute_labels_vectorized(ohlcv)
        valid = labels["pullback_recovery_5d"].dropna()
        assert set(valid.unique()).issubset({0.0, 1.0})

    def test_custom_support_ema(self):
        ohlcv = _make_ohlcv(60)
        custom_ema = pd.Series(np.full(60, 200.0))
        labels = compute_labels_vectorized(ohlcv, support_ema=custom_ema)
        valid = labels["csp_win_14d"].dropna()
        assert valid.mean() == 0.0
