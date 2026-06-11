"""Tests for nightly alpha batch scoring and Cloud Run wiring.

Regression: _score_variant must receive ``settings`` — a bare NameError there
broke production alpha-batch (June 2026).
"""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.strategy.alpha_engine import AlphaScoreEngine
from tyche.workflow.alpha_batch import (
    _resolve_as_of,
    _score_variant,
    run_alpha_batch,
)


def _minimal_features(*, with_date: bool = True) -> pd.DataFrame:
    row = {
        "ticker": "TEST",
        "close": 100.0,
        "return_63d": 0.1,
        "return_126d": 0.25,
        "return_252d": 0.4,
        "rs_63d": 0.05,
        "rs_126d": 0.15,
        "ema_stack_score": 3,
        "slope_accel": 0.1,
        "price_to_200ema_pct": 12.0,
        "pct_off_52w_high": -3.0,
        "breakout_20d": 1,
        "breakout_63d": 1,
        "volume_thrust_ratio": 1.5,
    }
    if with_date:
        row["date"] = date(2026, 6, 10)
    return pd.DataFrame([row])


class TestResolveAsOf:
    def test_uses_max_feature_date(self) -> None:
        features = _minimal_features()
        assert _resolve_as_of(features) == date(2026, 6, 10)

    @patch("tyche.market_data.ingest_dates.resolve_ingest_end_date")
    def test_falls_back_to_ingest_end_when_no_date_column(
        self, mock_resolve: MagicMock,
    ) -> None:
        mock_resolve.return_value = date(2026, 6, 9)
        settings = MagicMock()
        settings.ingest_window = "morning"
        features = _minimal_features(with_date=False)

        assert _resolve_as_of(features, settings=settings) == date(2026, 6, 9)
        mock_resolve.assert_called_once_with("morning", job_name="alpha-batch")


class TestScoreVariant:
    def test_scores_without_settings_nameerror_regression(
        self, tmp_path,
    ) -> None:
        """Calling _score_variant must not reference undefined ``settings``."""
        result = _score_variant(
            _minimal_features(),
            variant="peak",
            engine=AlphaScoreEngine(),
            data_dir=str(tmp_path),
            predictor=None,
            persist=False,
            settings=None,
        )
        assert result["status"] == "ok"
        assert result["as_of_date"] == "2026-06-10"
        assert result["signals"] == 1

    @patch("tyche.market_data.ingest_dates.resolve_ingest_end_date")
    def test_score_variant_passes_settings_to_as_of_resolver(
        self, mock_resolve: MagicMock, tmp_path,
    ) -> None:
        mock_resolve.return_value = date(2026, 6, 9)
        settings = MagicMock()
        settings.ingest_window = "morning"

        result = _score_variant(
            _minimal_features(with_date=False),
            variant="peak",
            engine=AlphaScoreEngine(),
            data_dir=str(tmp_path),
            predictor=None,
            persist=False,
            settings=settings,
        )
        assert result["as_of_date"] == "2026-06-09"
        mock_resolve.assert_called_once_with("morning", job_name="alpha-batch")


class TestRunAlphaBatch:
    @patch("tyche.workflow.alpha_batch._build_predictor", return_value=None)
    @patch("tyche.workflow.alpha_batch.build_latest_features")
    def test_passes_settings_into_score_variant(
        self,
        mock_build: MagicMock,
        _mock_predictor: MagicMock,
        tmp_path,
    ) -> None:
        mock_build.return_value = _minimal_features()
        settings = TycheSettings(ingest_window="morning")

        summary = run_alpha_batch(
            data_dir=str(tmp_path),
            settings=settings,
            persist=False,
            variants=["peak"],
            max_tickers=1,
        )

        assert summary["status"] == "ok"
        assert summary["as_of_date"] == "2026-06-10"


class TestGcpAlphaBatchJob:
    @patch("tyche.ops.gcp_jobs.run_alpha_batch")
    def test_run_alpha_batch_job_forwards_settings(
        self, mock_batch: MagicMock,
    ) -> None:
        from tyche.ops.gcp_jobs import run_alpha_batch_job

        mock_batch.return_value = {
            "status": "ok",
            "signals": 2,
            "buy_signals": 0,
            "ml_available": False,
        }
        settings = TycheSettings(
            ingest_window="morning",
            alpha_sustained_enabled=False,
            alpha_min_market_cap_millions=250,
        )

        result = run_alpha_batch_job(settings=settings)

        assert result.status == "success"
        mock_batch.assert_called_once()
        assert mock_batch.call_args.kwargs["settings"] is settings
