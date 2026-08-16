"""Tests for tyche.ml.xgb_baseline — walk-forward XGBoost evaluation."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

pytest.importorskip("xgboost")
pytest.importorskip("sklearn")

from tyche.ml.xgb_baseline import (
    BaselineReport,
    ModelResult,
    _average_importance,
    _prepare_feature_matrix,
    get_feature_columns,
    slim_dataset_for_training,
    walk_forward_evaluate,
)


def _make_dataset(
    n_tickers: int = 5,
    n_dates: int = 300,
    base_date: date | None = None,
) -> pd.DataFrame:
    """Generate a synthetic tabular dataset for testing walk-forward eval."""
    base = base_date or date(2023, 1, 3)
    rng = np.random.default_rng(42)
    rows = []

    for t in range(n_tickers):
        ticker = f"TEST{t}"
        for d in range(n_dates):
            dt = base + timedelta(days=d)
            row = {
                "date": dt,
                "ticker": ticker,
                "ema_8": 100 + rng.normal(0, 2),
                "ema_21": 100 + rng.normal(0, 2),
                "ema_50": 100 + rng.normal(0, 2),
                "price_to_8ema_pct": rng.normal(0, 2),
                "price_to_21ema_pct": rng.normal(0, 2),
                "price_to_50ema_pct": rng.normal(0, 3),
                "ema_8_slope": rng.normal(0, 0.5),
                "ema_21_slope": rng.normal(0, 0.3),
                "ema_50_slope": rng.normal(0, 0.2),
                "rsi_14": rng.uniform(20, 80),
                "days_above_both_emas": rng.integers(0, 20),
                "prior_streak": rng.integers(0, 15),
                "trend_state_ord": rng.integers(0, 6),
                "volume_ratio": rng.uniform(0.5, 2.0),
                "volume_declining": rng.integers(0, 2),
                "return_1d": rng.normal(0, 0.02),
                "return_5d": rng.normal(0, 0.04),
                "return_10d": rng.normal(0, 0.06),
                "return_20d": rng.normal(0, 0.08),
                "volatility_20d": rng.uniform(0.1, 0.5),
                "iv_rank": rng.uniform(10, 90),
                "iv_percentile": rng.uniform(10, 95),
                "atm_iv": rng.uniform(0.15, 0.6),
                "vrp": rng.normal(5, 10),
                "rv_20d": rng.uniform(0.1, 0.4),
                "log_market_cap": np.log1p(rng.uniform(4e9, 100e9)),
                "institutional_pct": rng.uniform(0.5, 0.95),
                "sector_encoded": rng.integers(1, 6),
                "sector_avg_rsi": rng.uniform(30, 70),
                "sector_avg_ema8_slope": rng.normal(0, 0.3),
                "sector_avg_ema21_slope": rng.normal(0, 0.2),
                "sector_breadth_8ema": rng.uniform(0.3, 0.8),
                "sector_breadth_21ema": rng.uniform(0.3, 0.8),
                "sector_avg_iv_rank": rng.uniform(20, 70),
                "sector_avg_vrp": rng.normal(5, 8),
                "sector_avg_return_5d": rng.normal(0, 0.03),
                "sector_count": rng.integers(5, 30),
                "csp_win_14d": float(rng.random() > 0.3),
                "csp_win_5d": float(rng.random() > 0.2),
                "direction_5d": rng.choice([-1, 0, 1]),
                "pullback_recovery_5d": float(rng.random() > 0.4),
            }
            rows.append(row)

    return pd.DataFrame(rows)


class TestGetFeatureColumns:
    def test_single_features(self):
        cols = get_feature_columns(include_neighbors=False)
        assert "ema_8" in cols
        assert "sector_avg_rsi" not in cols

    def test_neighbor_features(self):
        cols = get_feature_columns(include_neighbors=True)
        assert "ema_8" in cols
        assert "sector_avg_rsi" in cols


class TestPrepareFeatureMatrix:
    def test_float32_numpy_output(self):
        frame = pd.DataFrame(
            {
                "ema_8": [1.0, np.nan, 3.0],
                "rsi_14": [50.0, 60.0, 70.0],
                "e_eps_revision_90d": [1.0, np.nan, 2.0],
            }
        )
        X, cols = _prepare_feature_matrix(
            frame,
            ["ema_8", "rsi_14", "e_eps_revision_90d"],
            use_missingness_indicators=True,
        )
        assert X.dtype == np.float32
        assert np.isnan(X).sum() == 0
        assert X[1, 0] == -999.0
        assert "e_eps_revision_90d__isna" in cols


class TestSlimDatasetForTraining:
    def test_drops_unused_columns(self):
        wide = _make_dataset(n_tickers=1, n_dates=5)
        wide["extra_label"] = 1.0
        slim = slim_dataset_for_training(wide, ["csp_win_14d"])
        assert "csp_win_14d" in slim.columns
        assert "date" in slim.columns
        assert "extra_label" not in slim.columns
        assert len(slim.columns) < len(wide.columns)


class TestWalkForwardEvaluate:
    @pytest.fixture
    def dataset(self):
        return _make_dataset(n_tickers=3, n_dates=250)

    def test_binary_target(self, dataset):
        report = walk_forward_evaluate(
            dataset=dataset,
            target="csp_win_14d",
            include_neighbors=False,
            train_days=100,
            test_days=50,
            model_name="test_csp",
        )
        assert isinstance(report, BaselineReport)
        assert len(report.windows) >= 1
        assert report.mean_accuracy > 0
        assert report.target == "csp_win_14d"

    def test_multiclass_target(self, dataset):
        report = walk_forward_evaluate(
            dataset=dataset,
            target="direction_5d",
            include_neighbors=False,
            train_days=100,
            test_days=50,
            model_name="test_dir",
        )
        assert len(report.windows) >= 1

    def test_neighbor_features_used(self, dataset):
        report = walk_forward_evaluate(
            dataset=dataset,
            target="csp_win_14d",
            include_neighbors=True,
            train_days=100,
            test_days=50,
            model_name="test_neighbor",
        )
        assert report.feature_set == "neighbor"
        assert len(report.windows) >= 1

    def test_insufficient_dates(self, dataset):
        small = dataset[dataset["date"] < dataset["date"].min() + timedelta(days=30)]
        report = walk_forward_evaluate(
            dataset=small,
            target="csp_win_14d",
            train_days=100,
            test_days=50,
        )
        assert len(report.windows) == 0

    def test_feature_importance_populated(self, dataset):
        report = walk_forward_evaluate(
            dataset=dataset,
            target="csp_win_14d",
            train_days=100,
            test_days=50,
        )
        if report.windows:
            assert len(report.windows[0].feature_importance) > 0

    def test_window_stepping(self, dataset):
        report_no_overlap = walk_forward_evaluate(
            dataset=dataset,
            target="csp_win_14d",
            train_days=80,
            test_days=40,
            step_days=40,
        )
        report_overlap = walk_forward_evaluate(
            dataset=dataset,
            target="csp_win_14d",
            train_days=80,
            test_days=40,
            step_days=20,
        )
        assert len(report_overlap.windows) >= len(report_no_overlap.windows)


class TestBaselineReport:
    def test_aggregation(self):
        w1 = ModelResult(window_id=0, train_start="2024-01-01", train_end="2024-06-01",
                         test_start="2024-06-02", test_end="2024-09-01",
                         train_rows=1000, test_rows=500,
                         accuracy=75.0, precision=70.0, recall=80.0, f1=0.75, auc=0.80)
        w2 = ModelResult(window_id=1, train_start="2024-03-01", train_end="2024-09-01",
                         test_start="2024-09-02", test_end="2024-12-01",
                         train_rows=1000, test_rows=500,
                         accuracy=80.0, precision=75.0, recall=85.0, f1=0.80, auc=0.85)
        report = BaselineReport(model_name="test", target="csp_win_14d", feature_set="single", windows=[w1, w2])

        assert report.mean_accuracy == 77.5
        assert report.mean_auc == pytest.approx(0.825)
        assert report.std_accuracy > 0

    def test_to_dict(self):
        report = BaselineReport(model_name="test", target="csp_win_14d", feature_set="single")
        d = report.to_dict()
        assert d["model_name"] == "test"
        assert d["n_windows"] == 0


class TestAverageImportance:
    def test_averages_across_windows(self):
        w1 = ModelResult(window_id=0, train_start="", train_end="",
                         test_start="", test_end="", train_rows=0, test_rows=0,
                         feature_importance={"rsi_14": 0.3, "ema_8": 0.2})
        w2 = ModelResult(window_id=1, train_start="", train_end="",
                         test_start="", test_end="", train_rows=0, test_rows=0,
                         feature_importance={"rsi_14": 0.1, "ema_8": 0.4})
        result = _average_importance([w1, w2])
        result_dict = dict(result)
        assert result_dict["ema_8"] == pytest.approx(0.3)
        assert result_dict["rsi_14"] == pytest.approx(0.2)
        assert result[0][0] == "ema_8"
