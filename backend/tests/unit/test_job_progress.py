"""Structured job progress logging for Cloud Run observability."""

from __future__ import annotations

import time
from unittest.mock import MagicMock

import pytest

from tyche.ops.job_progress import log_job_phase, log_job_progress


def test_log_job_phase_emits_structured_event() -> None:
    mock = MagicMock()
    log_job_phase("ingest-data", "bootstrap_ohlcv", tickers=3500, logger=mock)
    mock.info.assert_called_once()
    args, kwargs = mock.info.call_args
    assert args[0] == "job_phase"
    assert kwargs["job"] == "ingest-data"
    assert kwargs["phase"] == "bootstrap_ohlcv"
    assert kwargs["status"] == "start"
    assert kwargs["tickers"] == 3500


def test_log_job_phase_complete_status() -> None:
    mock = MagicMock()
    log_job_phase(
        "alpha-batch",
        "build_features",
        status="complete",
        feature_rows=3200,
        logger=mock,
    )
    kwargs = mock.info.call_args.kwargs
    assert kwargs["status"] == "complete"
    assert kwargs["feature_rows"] == 3200


def test_log_job_progress_pct_and_eta() -> None:
    mock = MagicMock()
    start = time.monotonic() - 60.0
    log_job_progress(
        "ingest-options-flatfiles",
        "preload_ohlcv",
        done=500,
        total=3500,
        start_time=start,
        loaded=480,
        logger=mock,
    )
    kwargs = mock.info.call_args.kwargs
    assert kwargs["job"] == "ingest-options-flatfiles"
    assert kwargs["phase"] == "preload_ohlcv"
    assert kwargs["done"] == 500
    assert kwargs["total"] == 3500
    assert kwargs["pct"] == pytest.approx(14.3, abs=0.1)
    assert kwargs["loaded"] == 480
    assert "elapsed_min" in kwargs
    assert "eta_min" in kwargs


def test_log_job_progress_zero_total() -> None:
    mock = MagicMock()
    log_job_progress("test", "phase", done=0, total=0, logger=mock)
    kwargs = mock.info.call_args.kwargs
    assert kwargs["pct"] == 0.0
