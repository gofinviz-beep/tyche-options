"""Tests for CSPSafetyPredictor inference engine."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from tyche.conviction.features import FeatureSignal, TrendState

try:
    import xgboost as xgb

    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _ML_AVAILABLE, reason="ML deps not installed")


def _make_feature_signal(**overrides) -> FeatureSignal:
    defaults = {
        "ticker": "AAPL",
        "trend_state": TrendState.PULLBACK_TO_21EMA,
        "raw_conviction": "medium",
        "last_close": 180.0,
        "ema_8": 182.0,
        "ema_21": 179.0,
        "ema_50": 175.0,
        "ema_8_slope": 0.5,
        "ema_21_slope": 0.3,
        "ema_50_slope": 0.2,
        "price_to_8ema_pct": -1.1,
        "price_to_21ema_pct": 0.56,
        "rsi_14": 45.0,
        "days_above_both_emas": 0,
        "prior_streak": 8,
        "volume_declining_on_pullback": True,
        "avg_volume_20d": 50_000_000,
        "latest_volume": 45_000_000,
        "as_of_date": date.today(),
        "iv_rank": 55.0,
        "iv_percentile": 60.0,
        "atm_iv": 0.28,
        "vrp": 5.0,
    }
    defaults.update(overrides)
    return FeatureSignal(**defaults)


def _make_ohlcv(n: int = 60) -> pd.DataFrame:
    np.random.seed(42)
    close = 180.0 + np.cumsum(np.random.randn(n) * 0.5)
    return pd.DataFrame({
        "date": pd.date_range("2026-01-01", periods=n),
        "open": close - 0.1,
        "high": close + 1.0,
        "low": close - 1.0,
        "close": close,
        "volume": np.random.randint(30_000_000, 60_000_000, n),
    })


class TestCSPSafetyPredictorUnavailable:
    def test_no_model_returns_not_available(self, tmp_path):
        from tyche.ml.inference import CSPSafetyPredictor

        predictor = CSPSafetyPredictor(data_dir=str(tmp_path))
        assert not predictor.is_available
        assert predictor.model_info is None

    def test_predict_returns_none_when_unavailable(self, tmp_path):
        from tyche.ml.inference import CSPSafetyPredictor

        predictor = CSPSafetyPredictor(data_dir=str(tmp_path))
        result = predictor.predict(_make_feature_signal(), _make_ohlcv())
        assert result is None


class TestCSPSafetyPredictorWithModel:
    @pytest.fixture
    def predictor(self, tmp_path):
        from tyche.ml.inference import CSPSafetyPredictor
        from tyche.ml.model_store import save_model
        from tyche.ml.features import FEATURE_COLS

        X = np.random.rand(200, len(FEATURE_COLS))
        y = (X[:, 0] > 0.5).astype(int)
        model = xgb.XGBClassifier(
            n_estimators=10, max_depth=3, verbosity=0, random_state=42,
        )
        model.fit(X, y, verbose=False)

        save_model(
            model, target="csp_win_5d", feature_cols=FEATURE_COLS,
            data_dir=str(tmp_path),
        )

        return CSPSafetyPredictor(data_dir=str(tmp_path))

    def test_is_available(self, predictor):
        assert predictor.is_available

    def test_model_info(self, predictor):
        info = predictor.model_info
        assert info is not None
        assert info["target"] == "csp_win_5d"
        assert info["features"] > 0

    def test_predict_returns_probability(self, predictor):
        result = predictor.predict(
            _make_feature_signal(), _make_ohlcv(),
            market_cap=200e9, institutional_pct=0.75, sector_encoded=3,
        )
        assert result is not None
        assert 0.0 <= result <= 1.0

    def test_predict_batch(self, predictor):
        items = [
            (
                _make_feature_signal(ticker="AAPL"),
                _make_ohlcv(),
                {"rv_20d": 0.2},
                {"market_cap": 200e9, "institutional_pct": 0.75, "sector_encoded": 3},
            ),
            (
                _make_feature_signal(ticker="MSFT"),
                _make_ohlcv(),
                None,
                {"market_cap": 300e9, "institutional_pct": 0.80, "sector_encoded": 3},
            ),
        ]
        results = predictor.predict_batch(items)
        assert "AAPL" in results
        assert "MSFT" in results
        assert all(0.0 <= v <= 1.0 for v in results.values() if v is not None)

    def test_predict_handles_empty_ohlcv(self, predictor):
        empty_df = pd.DataFrame(columns=["date", "open", "high", "low", "close", "volume"])
        result = predictor.predict(_make_feature_signal(), empty_df)
        assert result is not None or result is None  # Should not raise


class TestFeatureBridging:
    def test_bridge_computes_all_features(self, tmp_path):
        from tyche.ml.inference import CSPSafetyPredictor
        from tyche.ml.model_store import save_model
        from tyche.ml.features import FEATURE_COLS

        X = np.random.rand(200, len(FEATURE_COLS))
        y = (X[:, 0] > 0.5).astype(int)
        model = xgb.XGBClassifier(
            n_estimators=5, max_depth=2, verbosity=0, random_state=42,
        )
        model.fit(X, y, verbose=False)
        save_model(model, target="csp_win_5d", feature_cols=FEATURE_COLS, data_dir=str(tmp_path))

        predictor = CSPSafetyPredictor(data_dir=str(tmp_path))

        features = predictor._bridge_features(
            _make_feature_signal(),
            _make_ohlcv(),
            market_cap=100e9,
            institutional_pct=0.60,
            sector_encoded=5,
        )

        for col in FEATURE_COLS:
            assert col in features, f"Missing feature: {col}"

        assert features["ema_8"] == 182.0
        assert features["rsi_14"] == 45.0
        assert features["sector_encoded"] == 5
        assert features["log_market_cap"] > 0
