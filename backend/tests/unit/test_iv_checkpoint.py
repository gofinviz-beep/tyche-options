"""Tests for IV checkpoint tracking and incremental IV extraction."""

from __future__ import annotations

import json
import math
from datetime import date
from unittest.mock import MagicMock

import pandas as pd
import pytest

from tyche.market_data.historical_iv_store import HistoricalIVStore
from tyche.market_data.options_history_store import OptionsHistoryStore


@pytest.fixture
def iv_store(tmp_path):
    return HistoricalIVStore(data_dir=str(tmp_path))


@pytest.fixture
def history_store(tmp_path):
    return OptionsHistoryStore(data_dir=str(tmp_path))


# ── Checkpoint round-trip ────────────────────────────────────────────


class TestIVCheckpoint:
    def test_no_checkpoint_initially(self, iv_store: HistoricalIVStore) -> None:
        assert iv_store.get_checkpoint() is None

    def test_write_and_read(self, iv_store: HistoricalIVStore) -> None:
        iv_store.write_checkpoint(
            last_options_date="2026-04-06",
            tickers_processed=3558,
            iv_points=1_137_634,
        )
        cp = iv_store.get_checkpoint()
        assert cp is not None
        assert cp["last_options_date"] == "2026-04-06"
        assert cp["tickers_processed"] == 3558
        assert cp["iv_points"] == 1_137_634
        assert "last_run_iso" in cp

    def test_overwrite(self, iv_store: HistoricalIVStore) -> None:
        iv_store.write_checkpoint(
            last_options_date="2026-04-05",
            tickers_processed=100,
            iv_points=5000,
        )
        iv_store.write_checkpoint(
            last_options_date="2026-04-06",
            tickers_processed=200,
            iv_points=10000,
        )
        cp = iv_store.get_checkpoint()
        assert cp["last_options_date"] == "2026-04-06"
        assert cp["tickers_processed"] == 200

    def test_corrupted_checkpoint_returns_none(
        self, iv_store: HistoricalIVStore
    ) -> None:
        iv_store._checkpoint_path.write_text("not valid json {{{")
        assert iv_store.get_checkpoint() is None

    def test_checkpoint_file_location(self, iv_store: HistoricalIVStore) -> None:
        iv_store.write_checkpoint(
            last_options_date="2026-04-06",
            tickers_processed=1,
            iv_points=1,
        )
        assert iv_store._checkpoint_path.name == "_iv_checkpoint.json"
        assert iv_store._checkpoint_path.exists()

        raw = json.loads(iv_store._checkpoint_path.read_text())
        assert set(raw.keys()) == {
            "last_run_iso",
            "last_options_date",
            "tickers_processed",
            "iv_points",
        }


# ── Incremental IV extraction ───────────────────────────────────────


def _seed_history(history_store: OptionsHistoryStore, ticker: str) -> None:
    """Write minimal options data so IV extraction has something to process."""
    exp = date(2025, 2, 5)
    strike = 190.0
    strike_raw = f"{int(strike * 1000):08d}"
    exp_str = exp.strftime("%y%m%d")
    options_df = pd.DataFrame(
        [
            {
                "date": date(2025, 1, 6),
                "option_ticker": f"O:{ticker}{exp_str}P{strike_raw}",
                "underlying": ticker,
                "expiration": exp,
                "strike": strike,
                "option_type": "P",
                "open": 4.00,
                "close": 3.80,
                "high": 4.20,
                "low": 3.60,
                "volume": 500,
                "transactions": 20,
                "dte": 30,
            },
        ]
    )
    history_store.write_ticker_data(ticker, options_df)


class TestIncrementalIVExtraction:
    def test_tickers_subset_limits_processing(self, tmp_path) -> None:
        from scripts.ingest_options_flatfiles import _run_iv_extraction

        history_store = OptionsHistoryStore(data_dir=str(tmp_path))
        iv_store = HistoricalIVStore(data_dir=str(tmp_path))
        derived_store = MagicMock()
        ohlcv_store = MagicMock()
        ohlcv_store.read_ticker.return_value = pd.DataFrame(
            {
                "date": [date(2025, 1, 6)],
                "open": [190.0],
                "high": [195.0],
                "low": [188.0],
                "close": [192.0],
                "volume": [1_000_000],
            }
        )

        for ticker in ("AAPL", "MSFT", "GOOGL"):
            _seed_history(history_store, ticker)

        stats_full = _run_iv_extraction(
            history_store, ohlcv_store, iv_store, derived_store,
            skip_derived=True, tickers_subset=None,
        )
        assert stats_full["tickers_processed"] == 3

        stats_partial = _run_iv_extraction(
            history_store, ohlcv_store, iv_store, derived_store,
            skip_derived=True, tickers_subset={"AAPL"},
        )
        assert stats_partial["tickers_processed"] == 1

    def test_empty_subset_processes_nothing(self, tmp_path) -> None:
        from scripts.ingest_options_flatfiles import _run_iv_extraction

        history_store = OptionsHistoryStore(data_dir=str(tmp_path))
        iv_store = HistoricalIVStore(data_dir=str(tmp_path))
        derived_store = MagicMock()
        ohlcv_store = MagicMock()

        _seed_history(history_store, "AAPL")

        stats = _run_iv_extraction(
            history_store, ohlcv_store, iv_store, derived_store,
            skip_derived=True, tickers_subset=set(),
        )
        assert stats["tickers_processed"] == 0
        assert stats["iv_points"] == 0


# ── Download phase tickers_touched_set ───────────────────────────────


class TestDownloadPhaseTickersSet:
    def test_returns_tickers_touched_set(self) -> None:
        """Verify _run_download_phase returns both set and count."""
        from scripts.ingest_options_flatfiles import _run_download_phase

        mock_s3 = MagicMock()
        mock_s3.download_fileobj.side_effect = Exception("skip")

        history_store = MagicMock()

        stats = _run_download_phase(
            s3_client=mock_s3,
            bucket="test",
            dates=[date(2025, 1, 6)],
            universe={"AAPL"},
            ohlcv_closes={},
            history_store=history_store,
            concurrency=1,
            flush_interval=10,
        )

        assert "tickers_touched_set" in stats
        assert isinstance(stats["tickers_touched_set"], set)
        assert isinstance(stats["tickers_touched"], int)
