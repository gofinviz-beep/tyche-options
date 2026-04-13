"""Tests for ML model persistence (save/load round-trip)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

try:
    import xgboost as xgb
    from tyche.ml.model_store import ModelMeta, load_model, save_model

    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False

pytestmark = pytest.mark.skipif(not _ML_AVAILABLE, reason="ML deps not installed")


@pytest.fixture
def tmp_data_dir(tmp_path: Path) -> str:
    return str(tmp_path)


@pytest.fixture
def trained_model() -> "xgb.XGBClassifier":
    """A tiny trained model for round-trip testing."""
    import numpy as np

    X = np.random.rand(100, 5)
    y = (X[:, 0] > 0.5).astype(int)
    model = xgb.XGBClassifier(
        n_estimators=5, max_depth=2, verbosity=0, random_state=42,
    )
    model.fit(X, y, verbose=False)
    return model


class TestSaveModel:
    def test_creates_model_and_meta_files(self, tmp_data_dir, trained_model):
        feature_cols = ["f1", "f2", "f3", "f4", "f5"]
        path = save_model(
            trained_model, target="csp_win_5d", feature_cols=feature_cols,
            data_dir=tmp_data_dir, train_rows=100, mean_auc=0.91,
        )

        assert path.exists()
        assert path.suffix == ".json"
        meta_path = path.parent / "csp_win_5d_meta.json"
        assert meta_path.exists()

    def test_meta_content(self, tmp_data_dir, trained_model):
        feature_cols = ["a", "b", "c"]
        save_model(
            trained_model, target="test_target", feature_cols=feature_cols,
            data_dir=tmp_data_dir, train_rows=200, mean_auc=0.85,
            mean_accuracy=88.0,
        )

        meta_path = Path(tmp_data_dir) / "ml" / "models" / "test_target_meta.json"
        meta = json.loads(meta_path.read_text())

        assert meta["target"] == "test_target"
        assert meta["feature_cols"] == ["a", "b", "c"]
        assert meta["train_rows"] == 200
        assert meta["mean_auc"] == 0.85
        assert meta["mean_accuracy"] == 88.0
        assert meta["schema_version"] == 1
        assert "trained_at" in meta


class TestLoadModel:
    def test_round_trip(self, tmp_data_dir, trained_model):
        feature_cols = ["f1", "f2", "f3", "f4", "f5"]
        save_model(
            trained_model, target="csp_win_5d", feature_cols=feature_cols,
            data_dir=tmp_data_dir,
        )

        result = load_model("csp_win_5d", data_dir=tmp_data_dir)
        assert result is not None

        loaded_model, meta = result
        assert isinstance(loaded_model, xgb.XGBClassifier)
        assert meta.target == "csp_win_5d"
        assert meta.feature_cols == feature_cols

    def test_missing_model_returns_none(self, tmp_data_dir):
        result = load_model("nonexistent_target", data_dir=tmp_data_dir)
        assert result is None

    def test_predictions_match_after_reload(self, tmp_data_dir, trained_model):
        import numpy as np

        feature_cols = ["f1", "f2", "f3", "f4", "f5"]
        save_model(
            trained_model, target="csp_win_5d", feature_cols=feature_cols,
            data_dir=tmp_data_dir,
        )

        X_test = np.random.rand(10, 5)
        original_pred = trained_model.predict_proba(X_test)[:, 1]

        loaded_model, _ = load_model("csp_win_5d", data_dir=tmp_data_dir)
        loaded_pred = loaded_model.predict_proba(X_test)[:, 1]

        np.testing.assert_array_almost_equal(original_pred, loaded_pred)
