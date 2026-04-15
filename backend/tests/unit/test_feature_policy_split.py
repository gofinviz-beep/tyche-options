"""Tests for the feature/policy split architecture.

Validates:
- ConvictionFeatureEngine produces FeatureSignal independently
- CSPEligibilityPolicy evaluates gates statelessly
- Feature cache and CSP policy are isolated
- Blast radius: small batch doesn't overwrite full cache
- Config changes recompute policy without re-running features
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

from tyche.conviction.features import (
    ConvictionFeatureEngine,
    FeatureSignal,
    TrendState,
    compute_conviction_score,
)
from tyche.conviction.csp_policy import CSPEligibilityPolicy
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import ConvictionSignalStore


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


def _fresh_uptrend(n=80, start=100.0, gain=0.5, streak_days=8):
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


def _pullback_data(n=80, start=100.0, gain=0.5, pullback_bars=3):
    prices = []
    for i in range(n - pullback_bars):
        prices.append(start + i * gain)
    peak = prices[-1]
    for i in range(pullback_bars):
        prices.append(peak - (i + 1) * gain * 2)
    return prices


class TestFeatureEngineIsolation:
    """ConvictionFeatureEngine works independently of CSP policy."""

    def test_produces_feature_signal(self):
        engine = ConvictionFeatureEngine()
        df = _make_ohlcv(_fresh_uptrend(80))
        signal = engine.analyze("AAPL", df)

        assert isinstance(signal, FeatureSignal)
        assert signal.ticker == "AAPL"
        assert signal.trend_state in TrendState
        assert signal.raw_conviction in ("high", "medium", "low", "none")
        assert signal.ema_8 > 0
        assert signal.ema_21 > 0
        assert signal.as_of_date is not None

    def test_no_csp_fields_on_feature_signal(self):
        engine = ConvictionFeatureEngine()
        df = _make_ohlcv(_fresh_uptrend(80))
        signal = engine.analyze("AAPL", df)

        assert not hasattr(signal, "csp_eligible")
        assert not hasattr(signal, "conviction_level")
        assert not hasattr(signal, "gate_results")

    def test_cache_is_per_engine_instance(self):
        engine_a = ConvictionFeatureEngine()
        engine_b = ConvictionFeatureEngine()
        df = _make_ohlcv(_fresh_uptrend(80))

        engine_a.analyze("AAPL", df)
        assert engine_a.cache_size == 1
        assert engine_b.cache_size == 0

    def test_batch_returns_unsorted(self):
        engine = ConvictionFeatureEngine()
        data = {
            "ZZZ": _make_ohlcv(_fresh_uptrend(80)),
            "AAA": _make_ohlcv(_downtrend(80)),
        }
        signals = engine.analyze_batch(data)
        tickers = [s.ticker for s in signals]
        assert "ZZZ" in tickers
        assert "AAA" in tickers

    def test_insufficient_data_returns_marker(self):
        engine = ConvictionFeatureEngine(min_bars=50)
        df = _make_ohlcv(_fresh_uptrend(10))
        signal = engine.analyze("SHORT", df)
        assert signal.trend_state == TrendState.INSUFFICIENT_DATA
        assert signal.raw_conviction == "none"


class TestCSPPolicyIsolation:
    """CSPEligibilityPolicy is stateless — no cache, no disk."""

    def test_evaluate_returns_policy_fields(self):
        policy = CSPEligibilityPolicy()
        feature = FeatureSignal(
            ticker="AAPL",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
        )
        result = policy.evaluate(feature)

        assert "csp_eligible" in result
        assert "conviction_level" in result
        assert "gate_results" in result
        assert isinstance(result["gate_results"], list)
        assert len(result["gate_results"]) >= 3

    def test_same_feature_different_configs(self):
        feature = FeatureSignal(
            ticker="AAPL",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
        )

        wide = CSPEligibilityPolicy(min_days_above_emas=5, max_days_above_emas=10)
        narrow = CSPEligibilityPolicy(min_days_above_emas=2, max_days_above_emas=3)

        wide_result = wide.evaluate(feature)
        narrow_result = narrow.evaluate(feature)

        assert wide_result["csp_eligible"] is True
        assert narrow_result["csp_eligible"] is False

    def test_pullback_path_disabled(self):
        feature = FeatureSignal(
            ticker="PULL",
            trend_state=TrendState.PULLBACK_TO_21EMA,
            raw_conviction="high",
            prior_streak=10,
            ema_21_slope=0.5,
        )
        policy = CSPEligibilityPolicy(pullback_csp_enabled=False)
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is False

    def test_pullback_path_enabled(self):
        feature = FeatureSignal(
            ticker="PULL",
            trend_state=TrendState.PULLBACK_TO_21EMA,
            raw_conviction="high",
            prior_streak=10,
            ema_21_slope=0.5,
        )
        policy = CSPEligibilityPolicy(pullback_csp_enabled=True, min_prior_streak=5)
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is True

    def test_insufficient_data_always_ineligible(self):
        feature = FeatureSignal(
            ticker="BAD",
            trend_state=TrendState.INSUFFICIENT_DATA,
        )
        policy = CSPEligibilityPolicy()
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is False
        assert result["conviction_level"] == "none"

    def test_extension_cap_blocks_overextended(self):
        feature = FeatureSignal(
            ticker="EXT",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=5.0,
        )
        policy = CSPEligibilityPolicy(max_extension_pct=3.0)
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is False

    def test_extension_cap_bypassed_for_pullback(self):
        feature = FeatureSignal(
            ticker="PB",
            trend_state=TrendState.PULLBACK_TO_8EMA,
            raw_conviction="medium",
            price_to_8ema_pct=-1.0,
            prior_streak=8,
            ema_21_slope=0.5,
        )
        policy = CSPEligibilityPolicy(max_extension_pct=3.0, min_prior_streak=5)
        result = policy.evaluate(feature)
        ext_gate = next(g for g in result["gate_results"] if g.gate == "Extension Cap")
        assert ext_gate.passed is True


class TestCSPPolicyRSIGate:
    """RSI overbought gate (Gate 4) — optional, controlled by max_rsi config."""

    def test_rsi_gate_disabled_by_default(self):
        """max_rsi=0 (default) means no RSI gate — overbought tickers pass."""
        policy = CSPEligibilityPolicy()
        feature = FeatureSignal(
            ticker="OB",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
            rsi_14=80.0,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is True
        gate_names = [g.gate for g in result["gate_results"]]
        assert "RSI Overbought" not in gate_names

    def test_rsi_gate_blocks_overbought(self):
        policy = CSPEligibilityPolicy(max_rsi=70.0)
        feature = FeatureSignal(
            ticker="OB",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
            rsi_14=76.0,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is False
        rsi_gate = next(g for g in result["gate_results"] if g.gate == "RSI Overbought")
        assert rsi_gate.passed is False
        assert "76.0" in rsi_gate.actual

    def test_rsi_gate_passes_normal(self):
        policy = CSPEligibilityPolicy(max_rsi=70.0)
        feature = FeatureSignal(
            ticker="OK",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
            rsi_14=55.0,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is True
        rsi_gate = next(g for g in result["gate_results"] if g.gate == "RSI Overbought")
        assert rsi_gate.passed is True

    def test_rsi_gate_at_boundary(self):
        """RSI exactly at threshold should pass (≤ check)."""
        policy = CSPEligibilityPolicy(max_rsi=70.0)
        feature = FeatureSignal(
            ticker="EDGE",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
            rsi_14=70.0,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is True

    def test_rsi_gate_skips_when_no_data(self):
        """RSI=None should pass (data unavailable, no penalty)."""
        policy = CSPEligibilityPolicy(max_rsi=70.0)
        feature = FeatureSignal(
            ticker="NODATA",
            trend_state=TrendState.STRONG_UPTREND,
            raw_conviction="high",
            days_above_both_emas=7,
            price_to_8ema_pct=1.5,
            rsi_14=None,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is True
        rsi_gate = next(g for g in result["gate_results"] if g.gate == "RSI Overbought")
        assert rsi_gate.passed is True
        assert "n/a" in rsi_gate.actual

    def test_rsi_gate_skipped_when_prior_gate_fails(self):
        """If trend gate fails, RSI gate should show as skipped."""
        policy = CSPEligibilityPolicy(max_rsi=70.0)
        feature = FeatureSignal(
            ticker="DOWN",
            trend_state=TrendState.DOWNTREND,
            raw_conviction="none",
            rsi_14=80.0,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is False

    def test_rsi_gate_in_insufficient_data(self):
        """RSI gate shows in insufficient data result when enabled."""
        policy = CSPEligibilityPolicy(max_rsi=70.0)
        feature = FeatureSignal(
            ticker="BAD",
            trend_state=TrendState.INSUFFICIENT_DATA,
        )
        result = policy.evaluate(feature)
        gate_names = [g.gate for g in result["gate_results"]]
        assert "RSI Overbought" in gate_names

    def test_rsi_gate_works_on_pullback_path(self):
        """Pullback path + overbought RSI should block eligibility."""
        policy = CSPEligibilityPolicy(max_rsi=70.0, min_prior_streak=5)
        feature = FeatureSignal(
            ticker="PB_OB",
            trend_state=TrendState.PULLBACK_TO_21EMA,
            raw_conviction="high",
            prior_streak=10,
            ema_21_slope=0.5,
            rsi_14=75.0,
        )
        result = policy.evaluate(feature)
        assert result["csp_eligible"] is False
        rsi_gate = next(g for g in result["gate_results"] if g.gate == "RSI Overbought")
        assert rsi_gate.passed is False


class TestBlastRadiusIsolation:
    """Small batch doesn't corrupt cached data from large batch."""

    def test_small_batch_preserves_large_cache(self):
        engine = ConvictionFeatureEngine()
        big_data = {
            f"TICK{i}": _make_ohlcv(_fresh_uptrend(80, start=100 + i))
            for i in range(20)
        }
        engine.analyze_batch(big_data)
        assert engine.cache_size == 20

        small_data = {"TICK0": big_data["TICK0"]}
        engine.analyze_batch(small_data)
        assert engine.cache_size == 20

    def test_disk_store_overwrite_on_new_signals(self, tmp_path):
        store = ConvictionSignalStore(data_dir=str(tmp_path))
        engine = ConvictionFeatureEngine(signal_store=store)

        big_data = {
            f"TICK{i}": _make_ohlcv(_fresh_uptrend(80, start=100 + i))
            for i in range(10)
        }
        engine.analyze_batch(big_data)
        assert store.exists

        rows = store.read_signals(store.get_cached_date())
        assert len(rows) == 10

        small_data = {"TICK0": big_data["TICK0"]}
        engine.analyze_batch(small_data)
        rows_after = store.read_signals(store.get_cached_date())
        assert len(rows_after) == 10

    def test_invalidate_is_per_engine(self):
        engine_a = ConvictionFeatureEngine()
        engine_b = ConvictionFeatureEngine()
        df = _make_ohlcv(_fresh_uptrend(80))

        engine_a.analyze("X", df)
        engine_b.analyze("Y", df)
        assert engine_a.cache_size == 1
        assert engine_b.cache_size == 1

        engine_a.invalidate_cache()
        assert engine_a.cache_size == 0
        assert engine_b.cache_size == 1


class TestWrapperBackwardCompat:
    """ConvictionEngine wrapper produces identical results to the old engine."""

    def test_analyze_returns_conviction_signal(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_fresh_uptrend(80))
        signal = engine.analyze("AAPL", df)

        assert isinstance(signal, ConvictionSignal)
        assert hasattr(signal, "csp_eligible")
        assert hasattr(signal, "conviction_level")
        assert hasattr(signal, "gate_results")
        assert signal.gate_results is not None
        assert len(signal.gate_results) >= 3

    def test_batch_sorted_by_conviction(self):
        engine = ConvictionEngine()
        data = {
            "UP": _make_ohlcv(_fresh_uptrend(80)),
            "DN": _make_ohlcv(_downtrend(80)),
        }
        signals = engine.analyze_batch(data)
        levels = [s.conviction_level for s in signals]
        order = {"high": 0, "medium": 1, "low": 2, "none": 3}
        assert all(
            order.get(levels[i], 99) <= order.get(levels[i + 1], 99)
            for i in range(len(levels) - 1)
        )

    def test_to_dict_has_all_fields(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_fresh_uptrend(80))
        d = engine.analyze("AAPL", df).to_dict()

        required_keys = {
            "ticker", "trend_state", "conviction_level", "raw_conviction",
            "csp_eligible", "last_close", "ema_8", "ema_21",
            "ema_8_slope", "ema_21_slope", "price_to_8ema_pct",
            "price_to_21ema_pct", "volume_declining_on_pullback",
            "avg_volume_20d", "latest_volume", "days_above_both_emas",
            "prior_streak", "as_of_date", "gate_results",
        }
        assert required_keys.issubset(d.keys())

    def test_feature_engine_accessible(self):
        engine = ConvictionEngine()
        assert isinstance(engine.feature_engine, ConvictionFeatureEngine)
        assert isinstance(engine.csp_policy, CSPEligibilityPolicy)

    def test_cache_delegates_to_feature_engine(self):
        engine = ConvictionEngine()
        df = _make_ohlcv(_fresh_uptrend(80))
        engine.analyze("AAPL", df)
        assert engine.cache_size == 1
        assert engine.feature_engine.cache_size == 1

        engine.invalidate_cache()
        assert engine.cache_size == 0
        assert engine.feature_engine.cache_size == 0


class TestConfigChangeRecomputation:
    """Policy changes take effect without re-running feature computation."""

    def test_same_features_different_policy(self, tmp_path):
        store = ConvictionSignalStore(data_dir=str(tmp_path))
        feature_engine = ConvictionFeatureEngine(signal_store=store)

        data = {"AAPL": _make_ohlcv(_fresh_uptrend(80, streak_days=8))}
        features = feature_engine.analyze_batch(data)
        assert len(features) == 1

        aapl_feature = features[0]

        wide_policy = CSPEligibilityPolicy(min_days_above_emas=5, max_days_above_emas=10)
        narrow_policy = CSPEligibilityPolicy(min_days_above_emas=2, max_days_above_emas=3)

        wide_result = wide_policy.evaluate(aapl_feature)
        narrow_result = narrow_policy.evaluate(aapl_feature)

        assert wide_result["csp_eligible"] != narrow_result["csp_eligible"] or \
            aapl_feature.days_above_both_emas <= 3


class TestConvictionScore:
    """Tests for the 0-1 conviction score computed from FeatureSignal fields."""

    def _base_signal(self, **overrides) -> FeatureSignal:
        defaults = dict(
            ticker="TEST",
            trend_state=TrendState.PULLBACK_TO_21EMA,
            raw_conviction="high",
            last_close=100.0,
            ema_8=101.0,
            ema_21=100.5,
            ema_8_slope=0.1,
            ema_21_slope=0.6,
            price_to_8ema_pct=-1.0,
            price_to_21ema_pct=-0.5,
            volume_declining_on_pullback=True,
            avg_volume_20d=1_000_000,
            latest_volume=800_000,
            days_above_both_emas=0,
            prior_streak=15,
            ema_50=98.0,
            ema_50_slope=0.15,
            rsi_14=45.0,
            iv_rank=55.0,
            vrp=30.0,
        )
        defaults.update(overrides)
        return FeatureSignal(**defaults)

    def test_perfect_pullback_scores_high(self):
        sig = self._base_signal()
        score = compute_conviction_score(sig)
        assert score >= 0.80

    def test_score_bounded_0_to_1(self):
        sig = self._base_signal()
        score = compute_conviction_score(sig)
        assert 0.0 <= score <= 1.0

    def test_downtrend_scores_zero(self):
        sig = self._base_signal(
            trend_state=TrendState.DOWNTREND,
            raw_conviction="none",
            prior_streak=0,
            ema_21_slope=-0.1,
            volume_declining_on_pullback=False,
            rsi_14=30.0,
            iv_rank=None,
            vrp=None,
        )
        score = compute_conviction_score(sig)
        assert score <= 0.15

    def test_oversold_50ema_scores_reasonably(self):
        sig = self._base_signal(
            trend_state=TrendState.OVERSOLD_50EMA,
            prior_streak=15,
            rsi_14=25.0,
            volume_declining_on_pullback=True,
        )
        score = compute_conviction_score(sig)
        assert 0.4 < score <= 1.0

    def test_oversold_21ema_scores_reasonably(self):
        sig = self._base_signal(
            trend_state=TrendState.OVERSOLD_21EMA,
            prior_streak=10,
            rsi_14=28.0,
        )
        score = compute_conviction_score(sig)
        assert 0.3 < score <= 1.0

    def test_oversold_rsi_sweet_spot(self):
        """For oversold states, RSI 30-40 is the sweet spot (backtest-validated)."""
        sweet_spot = compute_conviction_score(
            self._base_signal(
                trend_state=TrendState.OVERSOLD_50EMA,
                rsi_14=35.0,
                prior_streak=15,
            )
        )
        too_deep = compute_conviction_score(
            self._base_signal(
                trend_state=TrendState.OVERSOLD_50EMA,
                rsi_14=20.0,
                prior_streak=15,
            )
        )
        mild = compute_conviction_score(
            self._base_signal(
                trend_state=TrendState.OVERSOLD_50EMA,
                rsi_14=45.0,
                prior_streak=15,
            )
        )
        assert sweet_spot > too_deep
        assert sweet_spot > mild

    def test_trend_state_ordering(self):
        strong = compute_conviction_score(
            self._base_signal(trend_state=TrendState.STRONG_UPTREND, days_above_both_emas=10)
        )
        pullback_21 = compute_conviction_score(
            self._base_signal(trend_state=TrendState.PULLBACK_TO_21EMA)
        )
        pullback_8 = compute_conviction_score(
            self._base_signal(trend_state=TrendState.PULLBACK_TO_8EMA)
        )
        uptrend = compute_conviction_score(
            self._base_signal(trend_state=TrendState.UPTREND, days_above_both_emas=10)
        )
        assert strong > uptrend
        assert pullback_21 > pullback_8

    def test_longer_streak_scores_higher(self):
        short = compute_conviction_score(self._base_signal(prior_streak=3))
        long = compute_conviction_score(self._base_signal(prior_streak=12))
        assert long > short

    def test_rsi_sweet_spot(self):
        ideal = compute_conviction_score(self._base_signal(rsi_14=40.0))
        elevated = compute_conviction_score(self._base_signal(rsi_14=65.0))
        overbought = compute_conviction_score(self._base_signal(rsi_14=75.0))
        assert ideal > elevated
        assert elevated > overbought

    def test_iv_rank_sweet_spot(self):
        sweet = compute_conviction_score(self._base_signal(iv_rank=55.0))
        low = compute_conviction_score(self._base_signal(iv_rank=10.0))
        spiked = compute_conviction_score(self._base_signal(iv_rank=95.0))
        assert sweet > low
        assert sweet > spiked

    def test_vrp_bonus(self):
        positive = compute_conviction_score(self._base_signal(vrp=20.0))
        zero = compute_conviction_score(self._base_signal(vrp=0.0))
        none = compute_conviction_score(self._base_signal(vrp=None))
        assert positive > zero
        assert zero == none

    def test_volume_declining_bonus(self):
        declining = compute_conviction_score(
            self._base_signal(volume_declining_on_pullback=True)
        )
        rising = compute_conviction_score(
            self._base_signal(volume_declining_on_pullback=False)
        )
        assert declining > rising
        assert declining - rising == pytest.approx(0.05, abs=0.001)

    def test_missing_iv_no_penalty(self):
        with_iv = compute_conviction_score(self._base_signal(iv_rank=55.0, vrp=15.0))
        no_iv = compute_conviction_score(self._base_signal(iv_rank=None, vrp=None))
        assert with_iv > no_iv
        assert no_iv > 0.5

    def test_score_in_to_dict(self):
        sig = self._base_signal()
        sig.conviction_score = compute_conviction_score(sig)
        d = sig.to_dict()
        assert "conviction_score" in d
        assert d["conviction_score"] > 0
