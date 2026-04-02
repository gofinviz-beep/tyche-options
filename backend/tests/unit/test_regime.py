"""Tests for tyche.risk.regime — market regime detection and risk scaling."""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from tyche.risk.regime import (
    RegimeDetector,
    RegimeResult,
    RegimeScaling,
    RegimeState,
    apply_regime_scaling,
    filter_by_min_conviction,
)


# ── Helpers ──────────────────────────────────────────────────────────────

def _uptrend_ohlcv(n: int = 60, start: float = 100.0) -> pd.DataFrame:
    """Generate a steadily rising OHLCV DataFrame."""
    prices = [start + i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 1 for p in prices],
        "low": [p - 0.5 for p in prices],
        "close": prices,
        "volume": [1_000_000] * n,
    })


def _downtrend_ohlcv(n: int = 60, start: float = 130.0) -> pd.DataFrame:
    """Generate a steadily falling OHLCV DataFrame."""
    prices = [start - i * 0.5 for i in range(n)]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 0.5 for p in prices],
        "low": [p - 1 for p in prices],
        "close": prices,
        "volume": [1_000_000] * n,
    })


def _volatile_ohlcv(n: int = 60) -> pd.DataFrame:
    """Generate highly volatile sideways OHLCV data."""
    rng = np.random.RandomState(42)
    base = 100.0
    returns = rng.normal(0, 0.05, n)  # 5% daily std → very high vol
    prices = [base]
    for r in returns[1:]:
        prices.append(prices[-1] * (1 + r))
    return pd.DataFrame({
        "open": prices,
        "high": [p * 1.02 for p in prices],
        "low": [p * 0.98 for p in prices],
        "close": prices,
        "volume": [1_000_000] * n,
    })


def _flat_ohlcv(n: int = 60, price: float = 100.0) -> pd.DataFrame:
    """Nearly flat sideways data."""
    rng = np.random.RandomState(99)
    noise = rng.normal(0, 0.001, n)
    prices = [price * (1 + n_) for n_ in noise]
    return pd.DataFrame({
        "open": prices,
        "high": [p + 0.1 for p in prices],
        "low": [p - 0.1 for p in prices],
        "close": prices,
        "volume": [1_000_000] * n,
    })


# ── RegimeDetector ───────────────────────────────────────────────────────

class TestRegimeDetector:
    def test_uptrend_low_vol_is_risk_on(self):
        detector = RegimeDetector()
        result = detector.detect(_uptrend_ohlcv())
        assert result.state == RegimeState.RISK_ON
        assert result.trend_signal == "uptrend"
        assert result.vol_percentile == "normal"

    def test_downtrend_is_risk_off(self):
        detector = RegimeDetector()
        result = detector.detect(_downtrend_ohlcv())
        assert result.state == RegimeState.RISK_OFF
        assert result.trend_signal == "downtrend"

    def test_high_volatility_is_risk_off(self):
        detector = RegimeDetector(vol_high_threshold=0.20)
        data = _volatile_ohlcv()
        result = detector.detect(data)
        assert result.state == RegimeState.RISK_OFF

    def test_flat_market_is_neutral(self):
        detector = RegimeDetector()
        result = detector.detect(_flat_ohlcv())
        assert result.state in (RegimeState.NEUTRAL, RegimeState.RISK_ON)

    def test_insufficient_data_returns_neutral(self):
        detector = RegimeDetector()
        short = pd.DataFrame({"close": [100, 101, 102]})
        result = detector.detect(short)
        assert result.state == RegimeState.NEUTRAL
        assert result.trend_signal == "insufficient_data"

    def test_none_data_returns_neutral(self):
        detector = RegimeDetector()
        result = detector.detect(None)  # type: ignore[arg-type]
        assert result.state == RegimeState.NEUTRAL

    def test_to_dict_keys(self):
        detector = RegimeDetector()
        result = detector.detect(_uptrend_ohlcv())
        d = result.to_dict()
        assert "state" in d
        assert "trend_signal" in d
        assert "realised_vol" in d
        assert "scaling" in d
        assert "max_positions_scale" in d["scaling"]

    def test_custom_scaling_map(self):
        custom = {
            RegimeState.RISK_ON: RegimeScaling(max_positions_scale=2.0),
            RegimeState.NEUTRAL: RegimeScaling(max_positions_scale=1.5),
            RegimeState.RISK_OFF: RegimeScaling(max_positions_scale=0.3),
        }
        detector = RegimeDetector(scaling_map=custom)
        result = detector.detect(_uptrend_ohlcv())
        assert result.scaling.max_positions_scale == 2.0


# ── Regime Transitions ───────────────────────────────────────────────────

class TestRegimeTransitions:
    def test_transition_uptrend_to_downtrend(self):
        """Concatenating up then down data should shift regime."""
        detector = RegimeDetector()
        up = _uptrend_ohlcv(40)
        down = _downtrend_ohlcv(40, start=float(up["close"].iloc[-1]))
        combined = pd.concat([up, down], ignore_index=True)
        result = detector.detect(combined)
        assert result.state in (RegimeState.RISK_OFF, RegimeState.NEUTRAL)

    def test_vol_regime_shift(self):
        """Low-vol uptrend → suddenly volatile should move toward risk_off."""
        detector = RegimeDetector(vol_high_threshold=0.25)
        calm = _uptrend_ohlcv(40)
        wild = _volatile_ohlcv(40)
        combined = pd.concat([calm, wild], ignore_index=True)
        result = detector.detect(combined)
        assert result.state != RegimeState.RISK_ON


# ── Scaling Application ─────────────────────────────────────────────────

class TestApplyRegimeScaling:
    def test_risk_on_no_change(self):
        regime = RegimeResult(
            state=RegimeState.RISK_ON,
            scaling=RegimeScaling(max_positions_scale=1.0, concentration_cap_scale=1.0),
            trend_signal="uptrend", realised_vol=0.15, vol_percentile="normal",
        )
        pos, conc = apply_regime_scaling(regime, 8, 25.0)
        assert pos == 8
        assert conc == 25.0

    def test_risk_off_scales_down(self):
        regime = RegimeResult(
            state=RegimeState.RISK_OFF,
            scaling=RegimeScaling(max_positions_scale=0.5, concentration_cap_scale=0.6),
            trend_signal="downtrend", realised_vol=0.35, vol_percentile="high",
        )
        pos, conc = apply_regime_scaling(regime, 8, 25.0)
        assert pos == 4
        assert conc == 15.0

    def test_neutral_partial_scale(self):
        regime = RegimeResult(
            state=RegimeState.NEUTRAL,
            scaling=RegimeScaling(max_positions_scale=0.75, concentration_cap_scale=0.8),
            trend_signal="mixed", realised_vol=0.22, vol_percentile="elevated",
        )
        pos, conc = apply_regime_scaling(regime, 8, 25.0)
        assert pos == 6
        assert conc == 20.0

    def test_minimum_one_position(self):
        regime = RegimeResult(
            state=RegimeState.RISK_OFF,
            scaling=RegimeScaling(max_positions_scale=0.01),
            trend_signal="downtrend", realised_vol=0.5, vol_percentile="high",
        )
        pos, _ = apply_regime_scaling(regime, 2, 25.0)
        assert pos >= 1


# ── Conviction Filter ────────────────────────────────────────────────────

class TestFilterByMinConviction:
    def _make_candidate(self, symbol: str):
        from tyche.strategy.strategies.base import ScoredCandidate
        from datetime import date
        return ScoredCandidate(
            symbol=symbol, option_symbol=f"{symbol}P", option_type="put",
            strike=100, expiration=date(2026, 4, 10), dte=8,
            bid=1.5, ask=1.7, mid=1.6, volume=100, open_interest=500,
            implied_volatility=0.3, underlying_price=105, strategy="csp",
        )

    def test_medium_passes_all(self):
        from dataclasses import dataclass

        @dataclass
        class _Sig:
            conviction_level: str

        candidates = [self._make_candidate("A"), self._make_candidate("B")]
        signals = {
            "A": _Sig("high"),
            "B": _Sig("medium"),
        }
        result = filter_by_min_conviction(candidates, "medium", signals)
        assert len(result) == 2

    def test_high_filters_non_high(self):
        from dataclasses import dataclass

        @dataclass
        class _Sig:
            conviction_level: str

        candidates = [self._make_candidate("A"), self._make_candidate("B"), self._make_candidate("C")]
        signals = {
            "A": _Sig("high"),
            "B": _Sig("medium"),
            "C": _Sig("high"),
        }
        result = filter_by_min_conviction(candidates, "high", signals)
        assert len(result) == 2
        assert all(r.symbol in ("A", "C") for r in result)

    def test_no_signal_excluded_when_high(self):
        candidates = [self._make_candidate("A")]
        result = filter_by_min_conviction(candidates, "high", {})
        assert len(result) == 0

    def test_empty_candidates(self):
        assert filter_by_min_conviction([], "high", {}) == []
