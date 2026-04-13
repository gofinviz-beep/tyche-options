"""Tests for csp_safety_prob flowing through the conviction pipeline."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tyche.conviction.engine import ConvictionEngine, ConvictionSignal, _feature_to_signal
from tyche.conviction.features import FeatureSignal, TrendState


def _make_feature(**overrides) -> FeatureSignal:
    defaults = {
        "ticker": "AAPL",
        "trend_state": TrendState.PULLBACK_TO_21EMA,
        "raw_conviction": "medium",
        "last_close": 180.0,
        "ema_8": 182.0,
        "ema_21": 179.0,
        "ema_8_slope": 0.5,
        "ema_21_slope": 0.3,
        "price_to_8ema_pct": -1.1,
        "price_to_21ema_pct": 0.56,
        "as_of_date": date.today(),
        "conviction_score": 0.65,
        "csp_safety_prob": 0.88,
    }
    defaults.update(overrides)
    return FeatureSignal(**defaults)


class TestFeatureSignalCspSafetyProb:
    def test_field_exists(self):
        sig = _make_feature()
        assert sig.csp_safety_prob == 0.88

    def test_defaults_to_none(self):
        sig = FeatureSignal(ticker="X", trend_state=TrendState.CONSOLIDATION)
        assert sig.csp_safety_prob is None

    def test_to_dict_includes_field(self):
        sig = _make_feature(csp_safety_prob=0.92)
        d = sig.to_dict()
        assert "csp_safety_prob" in d
        assert d["csp_safety_prob"] == 0.92

    def test_to_dict_none(self):
        sig = _make_feature(csp_safety_prob=None)
        d = sig.to_dict()
        assert d["csp_safety_prob"] is None


class TestConvictionSignalCspSafetyProb:
    def test_field_propagated_from_feature(self):
        feature = _make_feature(csp_safety_prob=0.85)
        policy_result = {
            "conviction_level": "medium",
            "csp_eligible": True,
            "gate_results": [],
        }
        signal = _feature_to_signal(feature, policy_result)
        assert signal.csp_safety_prob == 0.85

    def test_to_dict_includes_field(self):
        signal = ConvictionSignal(
            ticker="MSFT",
            trend_state=TrendState.UPTREND,
            conviction_level="high",
            csp_safety_prob=0.77,
        )
        d = signal.to_dict()
        assert "csp_safety_prob" in d
        assert d["csp_safety_prob"] == 0.77


class TestConvictionEngineWithPredictor:
    @pytest.fixture
    def mock_predictor(self):
        predictor = MagicMock()
        predictor.is_available = True
        predictor.predict.return_value = 0.91
        predictor.predict_batch.return_value = {"AAPL": 0.91, "MSFT": 0.85}
        return predictor

    @pytest.fixture
    def sample_df(self):
        import numpy as np

        n = 60
        close = 180.0 + np.cumsum(np.random.randn(n) * 0.5)
        return pd.DataFrame({
            "date": pd.date_range("2026-01-01", periods=n).strftime("%Y-%m-%d"),
            "open": close - 0.1,
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.random.randint(1_000_000, 5_000_000, n),
        })

    def test_analyze_calls_predictor(self, mock_predictor, sample_df):
        engine = ConvictionEngine(csp_predictor=mock_predictor)
        signal = engine.analyze("AAPL", sample_df)
        mock_predictor.predict.assert_called_once()
        assert signal.csp_safety_prob == 0.91

    def test_analyze_without_predictor(self, sample_df):
        engine = ConvictionEngine()
        signal = engine.analyze("AAPL", sample_df)
        assert signal.csp_safety_prob is None

    def test_analyze_batch_calls_predict_batch(self, mock_predictor, sample_df):
        engine = ConvictionEngine(csp_predictor=mock_predictor)
        signals = engine.analyze_batch({"AAPL": sample_df, "MSFT": sample_df})
        mock_predictor.predict_batch.assert_called_once()
        for s in signals:
            assert s.csp_safety_prob is not None
