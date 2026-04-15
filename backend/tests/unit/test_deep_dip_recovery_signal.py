"""Tests for deep dip recovery signal logic and market context computation."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tyche.schemas.alerts import MarketContextResponse, RecoverySignalResponse


def _make_alert(
    *,
    rsi: float = 35.0,
    ema_21_slope: float = 0.1,
    dip_classification=None,
    prior_streak: int = 10,
    ticker: str = "TEST",
):
    """Create a minimal alert-like object for _assess_recovery_signal."""
    alert = SimpleNamespace(
        ticker=ticker,
        rsi_14=rsi,
        ema_21_slope=ema_21_slope,
        prior_streak=prior_streak,
        dip_classification=dip_classification,
    )
    return alert


def _make_market_ctx(
    *, concurrent_dips: int = 150, is_broad: bool = True, spy_ret: float = -3.0,
) -> MarketContextResponse:
    return MarketContextResponse(
        concurrent_dips=concurrent_dips,
        total_universe=1200,
        market_dip_breadth=concurrent_dips / 1200,
        spy_return_5d=spy_ret,
        spy_drawdown_from_high=-5.0,
        spy_rsi_14=38.0,
        is_broad_selloff=is_broad,
    )


@pytest.fixture
def assess():
    """Import the recovery signal function from the route module."""
    from tyche.api.routes.stocks import _assess_recovery_signal
    return _assess_recovery_signal


@pytest.fixture
def compute_ctx():
    """Import the market context computation function."""
    from tyche.api.routes.stocks import _compute_market_context
    return _compute_market_context


class TestAssessRecoverySignal:
    def test_all_thresholds_met(self, assess):
        """RSI 30-50, slope > -0.5, broad selloff, $20B+ cap => fully actionable."""
        alert = _make_alert(rsi=37.0, ema_21_slope=0.2)
        ctx = _make_market_ctx(concurrent_dips=150, is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=50.0)

        assert isinstance(result, RecoverySignalResponse)
        assert result.actionable is True
        assert result.meets_all_thresholds is True
        assert "~55-58%" in result.recovery_20d_est
        assert "~73-75%" in result.recovery_40d_est
        assert result.suggested_cc_dte != ""
        assert all("PASS" in c for c in result.threshold_checks[:4])

    def test_rsi_below_sweet_spot_fails(self, assess):
        """RSI < 30 fails the RSI threshold."""
        alert = _make_alert(rsi=22.0, ema_21_slope=0.1)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=50.0)

        assert result.meets_all_thresholds is False
        assert result.actionable is False
        rsi_check = [c for c in result.threshold_checks if "RSI" in c]
        assert any("FAIL" in c for c in rsi_check)

    def test_rsi_above_sweet_spot_fails(self, assess):
        """RSI > 50 fails the RSI threshold."""
        alert = _make_alert(rsi=55.0)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=50.0)

        assert result.actionable is False
        rsi_check = [c for c in result.threshold_checks if "RSI" in c]
        assert any("FAIL" in c for c in rsi_check)

    def test_slope_too_negative_fails(self, assess):
        """21-EMA slope <= -0.5 fails the slope threshold."""
        alert = _make_alert(rsi=35.0, ema_21_slope=-0.8)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=50.0)

        assert result.meets_all_thresholds is False
        assert result.actionable is False

    def test_no_broad_selloff_but_large_cap_partially_actionable(self, assess):
        """Without broad selloff but with $20B+ cap: actionable but not meets_all."""
        alert = _make_alert(rsi=35.0, ema_21_slope=0.1)
        ctx = _make_market_ctx(concurrent_dips=40, is_broad=False)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=30.0)

        assert result.actionable is True
        assert result.meets_all_thresholds is False
        assert "~45-52%" in result.recovery_20d_est

    def test_no_broad_selloff_small_cap_not_actionable(self, assess):
        """No broad selloff + small cap => not actionable."""
        alert = _make_alert(rsi=35.0, ema_21_slope=0.1)
        ctx = _make_market_ctx(concurrent_dips=20, is_broad=False)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=5.0)

        assert result.actionable is False
        assert result.meets_all_thresholds is False

    def test_high_risk_dip_classification_blocks(self, assess):
        """Dip classified as high risk (not actionable) blocks the signal."""
        dc = SimpleNamespace(actionable=False, risk_level="high")
        alert = _make_alert(rsi=35.0, ema_21_slope=0.1, dip_classification=dc)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=50.0)

        assert result.actionable is False
        risk_check = [c for c in result.threshold_checks if "risk" in c.lower()]
        assert any("FAIL" in c for c in risk_check)

    def test_edge_rsi_30_passes(self, assess):
        """RSI exactly 30 is in the sweet spot."""
        alert = _make_alert(rsi=30.0, ema_21_slope=0.1)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=25.0)
        assert result.actionable is True

    def test_edge_rsi_50_passes(self, assess):
        """RSI exactly 50 is in the sweet spot."""
        alert = _make_alert(rsi=50.0, ema_21_slope=0.1)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=25.0)
        assert result.actionable is True

    def test_slope_exactly_minus_half_fails(self, assess):
        """Slope at -0.5 exactly still fails (needs > -0.5)."""
        alert = _make_alert(rsi=35.0, ema_21_slope=-0.5)
        ctx = _make_market_ctx(is_broad=True)
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=50.0)
        assert result.actionable is False

    def test_threshold_checks_count(self, assess):
        """Should always produce exactly 5 threshold checks."""
        alert = _make_alert()
        ctx = _make_market_ctx()
        result = assess(alert=alert, market_ctx=ctx, market_cap_b=10.0)
        assert len(result.threshold_checks) == 5


class TestComputeMarketContext:
    def _make_spy_store(self, n: int = 100):
        """Create a mock OHLCVStore with SPY data."""
        dates = pd.bdate_range(end="2026-04-10", periods=n)
        close = np.linspace(490.0, 480.0, n)
        df = pd.DataFrame({"close": close}, index=dates)
        store = MagicMock()
        store.read_ticker.return_value = df
        return store

    def test_broad_selloff_threshold(self, compute_ctx):
        """100+ concurrent dips triggers is_broad_selloff."""
        store = self._make_spy_store()
        result = compute_ctx(
            all_signals={}, oversold_count=120, total_count=1200,
            data_store=store,
        )
        assert result.is_broad_selloff is True
        assert result.concurrent_dips == 120
        assert result.total_universe == 1200
        assert result.market_dip_breadth == pytest.approx(0.1, abs=0.001)

    def test_not_broad_selloff(self, compute_ctx):
        """<100 dips => not broad."""
        store = self._make_spy_store()
        result = compute_ctx(
            all_signals={}, oversold_count=30, total_count=1200,
            data_store=store,
        )
        assert result.is_broad_selloff is False

    def test_spy_metrics_computed(self, compute_ctx):
        """SPY return, drawdown, and RSI should be computed from OHLCV."""
        store = self._make_spy_store(n=100)
        result = compute_ctx(
            all_signals={}, oversold_count=100, total_count=1000,
            data_store=store,
        )
        assert result.spy_return_5d is not None
        assert result.spy_drawdown_from_high is not None
        assert result.spy_rsi_14 is not None

    def test_spy_missing_gracefully_degrades(self, compute_ctx):
        """If SPY data is missing, metrics are None."""
        store = MagicMock()
        store.read_ticker.return_value = None
        result = compute_ctx(
            all_signals={}, oversold_count=50, total_count=500,
            data_store=store,
        )
        assert result.spy_return_5d is None
        assert result.spy_drawdown_from_high is None
        assert result.spy_rsi_14 is None
        assert result.concurrent_dips == 50

    def test_spy_exception_gracefully_degrades(self, compute_ctx):
        """If SPY read raises, metrics are None."""
        store = MagicMock()
        store.read_ticker.side_effect = Exception("no data")
        result = compute_ctx(
            all_signals={}, oversold_count=50, total_count=500,
            data_store=store,
        )
        assert result.spy_return_5d is None
        assert result.concurrent_dips == 50

    def test_zero_total_count(self, compute_ctx):
        """Zero total prevents division-by-zero in breadth."""
        store = MagicMock()
        store.read_ticker.return_value = None
        result = compute_ctx(
            all_signals={}, oversold_count=0, total_count=0,
            data_store=store,
        )
        assert result.market_dip_breadth == 0.0
