"""Tests for the Black-Scholes IV calculator."""

from __future__ import annotations

import math

import numpy as np
import pytest

from tyche.market_data.iv_calculator import bs_put_price, compute_iv, compute_iv_batch


class TestBsPutPrice:
    def test_atm_put_has_positive_value(self) -> None:
        price = bs_put_price(S=100, K=100, T=30 / 365, r=0.05, sigma=0.30)
        assert price > 0
        assert price < 100

    def test_deep_otm_put_is_cheap(self) -> None:
        price = bs_put_price(S=100, K=60, T=30 / 365, r=0.05, sigma=0.30)
        assert price < 0.01

    def test_deep_itm_put_near_intrinsic(self) -> None:
        price = bs_put_price(S=80, K=120, T=30 / 365, r=0.05, sigma=0.30)
        intrinsic = 120 * math.exp(-0.05 * 30 / 365) - 80
        assert price >= intrinsic - 0.01

    def test_zero_time_returns_intrinsic(self) -> None:
        price = bs_put_price(S=90, K=100, T=0, r=0.05, sigma=0.30)
        assert price == pytest.approx(10.0, abs=0.01)

    def test_zero_vol_returns_intrinsic(self) -> None:
        price = bs_put_price(S=90, K=100, T=30 / 365, r=0.05, sigma=0)
        assert price >= 0

    def test_higher_vol_increases_price(self) -> None:
        low_vol = bs_put_price(S=100, K=100, T=30 / 365, r=0.05, sigma=0.20)
        high_vol = bs_put_price(S=100, K=100, T=30 / 365, r=0.05, sigma=0.50)
        assert high_vol > low_vol

    def test_longer_dte_increases_price(self) -> None:
        short = bs_put_price(S=100, K=100, T=10 / 365, r=0.05, sigma=0.30)
        long = bs_put_price(S=100, K=100, T=60 / 365, r=0.05, sigma=0.30)
        assert long > short


class TestComputeIv:
    def test_round_trip_atm(self) -> None:
        """Compute a put price at known IV, then recover the IV."""
        known_iv = 0.30
        price = bs_put_price(S=100, K=100, T=30 / 365, r=0.05, sigma=known_iv)
        recovered = compute_iv(price, 100, 100, 30, 0.05)
        assert recovered == pytest.approx(known_iv, abs=1e-4)

    def test_round_trip_otm(self) -> None:
        known_iv = 0.40
        price = bs_put_price(S=100, K=90, T=30 / 365, r=0.05, sigma=known_iv)
        recovered = compute_iv(price, 100, 90, 30, 0.05)
        assert recovered == pytest.approx(known_iv, abs=1e-3)

    def test_round_trip_high_iv(self) -> None:
        known_iv = 1.50
        price = bs_put_price(S=100, K=100, T=30 / 365, r=0.05, sigma=known_iv)
        recovered = compute_iv(price, 100, 100, 30, 0.05)
        assert recovered == pytest.approx(known_iv, abs=1e-3)

    def test_zero_option_price_returns_nan(self) -> None:
        assert math.isnan(compute_iv(0, 100, 100, 30))

    def test_negative_price_returns_nan(self) -> None:
        assert math.isnan(compute_iv(-1, 100, 100, 30))

    def test_zero_underlying_returns_nan(self) -> None:
        assert math.isnan(compute_iv(5, 0, 100, 30))

    def test_zero_strike_returns_nan(self) -> None:
        assert math.isnan(compute_iv(5, 100, 0, 30))

    def test_zero_dte_returns_nan(self) -> None:
        assert math.isnan(compute_iv(5, 100, 100, 0))

    def test_very_cheap_option(self) -> None:
        """Options at $0.01 should still produce a valid IV."""
        iv = compute_iv(0.01, 100, 100, 30)
        assert not math.isnan(iv)
        assert iv > 0

    @pytest.mark.parametrize(
        "S,K,dte,expected_range",
        [
            (100, 100, 30, (0.1, 2.0)),
            (200, 200, 60, (0.1, 2.0)),
            (50, 45, 14, (0.1, 3.0)),
        ],
    )
    def test_realistic_scenarios(
        self, S: float, K: float, dte: int, expected_range: tuple[float, float]
    ) -> None:
        price = bs_put_price(S, K, dte / 365, 0.05, 0.35)
        iv = compute_iv(price, S, K, dte)
        assert expected_range[0] < iv < expected_range[1]


class TestComputeIvBatch:
    def test_batch_returns_correct_length(self) -> None:
        records = [
            {"option_close": bs_put_price(100, 100, 30 / 365, 0.05, 0.30),
             "underlying_close": 100, "strike": 100, "dte": 30},
            {"option_close": bs_put_price(100, 95, 30 / 365, 0.05, 0.25),
             "underlying_close": 100, "strike": 95, "dte": 30},
        ]
        result = compute_iv_batch(records)
        assert len(result) == 2
        assert result[0] == pytest.approx(0.30, abs=1e-3)
        assert result[1] == pytest.approx(0.25, abs=1e-3)

    def test_batch_with_bad_records(self) -> None:
        records = [
            {"option_close": 0, "underlying_close": 100, "strike": 100, "dte": 30},
            {"option_close": 5, "underlying_close": 100, "strike": 100, "dte": 0},
        ]
        result = compute_iv_batch(records)
        assert len(result) == 2
        assert math.isnan(result[0])
        assert math.isnan(result[1])

    def test_empty_batch(self) -> None:
        result = compute_iv_batch([])
        assert len(result) == 0
