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
