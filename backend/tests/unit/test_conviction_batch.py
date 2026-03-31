"""Tests for the conviction batch job."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.workflow.conviction_batch import (
    ConvictionBatchResult,
    _filter_by_market_cap,
    _filter_by_price_volume,
    run_conviction_batch,
)


def _make_mock_data_store(
    exists: bool = True,
    tickers: list[str] | None = None,
    ticker_data: dict | None = None,
) -> MagicMock:
    store = MagicMock()
    store.exists = exists
    store.get_all_tickers.return_value = tickers or []
    store.read_tickers.return_value = ticker_data or {}
    return store


def _make_mock_meta_store(
    exists: bool = True,
    caps: dict[str, float] | None = None,
) -> MagicMock:
    store = MagicMock()
    store.exists = exists
    store.get_market_caps.return_value = caps or {}
    return store


def _make_mock_engine(signals: list | None = None) -> MagicMock:
    engine = MagicMock()
    engine.analyze_batch.return_value = signals or []
    return engine


def _make_signal_mock(ticker: str, as_of_date: date | None = None) -> MagicMock:
    sig = MagicMock()
    sig.ticker = ticker
    sig.as_of_date = as_of_date or date(2026, 3, 28)
    return sig


def _make_dataframe(last_close: float = 100.0, avg_volume: float = 1_000_000.0):
    """Create a mock DataFrame with 25 rows."""
    import pandas as pd
    import numpy as np

    dates = pd.date_range("2026-02-15", periods=25, freq="B")
    df = pd.DataFrame({
        "date": dates,
        "open": [last_close] * 25,
        "high": [last_close * 1.02] * 25,
        "low": [last_close * 0.98] * 25,
        "close": [last_close] * 25,
        "volume": [avg_volume] * 25,
    })
    return df


class TestFilterByMarketCap:
    def test_no_meta_store(self):
        result = _filter_by_market_cap(["AAPL", "MSFT"], None, 500_000_000)
        assert result == ["AAPL", "MSFT"]

    def test_meta_store_not_exists(self):
        meta = _make_mock_meta_store(exists=False)
        result = _filter_by_market_cap(["AAPL"], meta, 500_000_000)
        assert result == ["AAPL"]

    def test_min_market_cap_zero(self):
        meta = _make_mock_meta_store(caps={"AAPL": 100})
        result = _filter_by_market_cap(["AAPL"], meta, 0)
        assert result == ["AAPL"]

    def test_passes_above_threshold(self):
        meta = _make_mock_meta_store(caps={"AAPL": 2_000_000_000, "MSFT": 3_000_000_000})
        result = _filter_by_market_cap(["AAPL", "MSFT"], meta, 1_000_000_000)
        assert result == ["AAPL", "MSFT"]

    def test_drops_below_threshold(self):
        meta = _make_mock_meta_store(caps={"AAPL": 2_000_000_000, "SMALL": 100_000_000})
        result = _filter_by_market_cap(["AAPL", "SMALL"], meta, 500_000_000)
        assert result == ["AAPL"]

    def test_no_data_passes(self):
        meta = _make_mock_meta_store(caps={"AAPL": 2_000_000_000})
        result = _filter_by_market_cap(["AAPL", "NEWT"], meta, 500_000_000)
        assert "NEWT" in result

    def test_zero_cap_passes(self):
        meta = _make_mock_meta_store(caps={"AAPL": 2_000_000_000, "ZERO": 0})
        result = _filter_by_market_cap(["AAPL", "ZERO"], meta, 500_000_000)
        assert "ZERO" in result


class TestFilterByPriceVolume:
    def test_empty_data(self):
        result = _filter_by_price_volume({}, 5.0, 500_000)
        assert result == {}

    def test_drops_low_price(self):
        data = {
            "CHEAP": _make_dataframe(last_close=2.0),
            "NORMAL": _make_dataframe(last_close=50.0),
        }
        result = _filter_by_price_volume(data, 5.0, 500_000)
        assert "CHEAP" not in result
        assert "NORMAL" in result

    def test_drops_low_volume(self):
        data = {
            "THIN": _make_dataframe(avg_volume=100_000),
            "LIQUID": _make_dataframe(avg_volume=2_000_000),
        }
        result = _filter_by_price_volume(data, 5.0, 500_000)
        assert "THIN" not in result
        assert "LIQUID" in result

    def test_drops_insufficient_rows(self):
        import pandas as pd
        short_df = pd.DataFrame({
            "close": [100.0] * 10,
            "volume": [1_000_000] * 10,
        })
        data = {"SHORT": short_df}
        result = _filter_by_price_volume(data, 5.0, 500_000)
        assert result == {}


class TestRunConvictionBatch:
    @pytest.mark.asyncio
    async def test_store_not_exists(self):
        store = _make_mock_data_store(exists=False)
        engine = _make_mock_engine()
        result = await run_conviction_batch(store, engine)
        assert "OHLCV data store does not exist" in result.errors

    @pytest.mark.asyncio
    async def test_no_tickers(self):
        store = _make_mock_data_store(exists=True, tickers=[])
        engine = _make_mock_engine()
        result = await run_conviction_batch(store, engine)
        assert "No tickers in OHLCV store" in result.errors

    @pytest.mark.asyncio
    async def test_no_tickers_pass_filter(self):
        store = _make_mock_data_store(
            exists=True,
            tickers=["SMALL"],
            ticker_data={"SMALL": _make_dataframe(last_close=2.0)},
        )
        engine = _make_mock_engine()
        meta = _make_mock_meta_store(caps={"SMALL": 100_000_000})

        result = await run_conviction_batch(
            store, engine, ticker_meta_store=meta, min_market_cap=500_000_000
        )
        assert result.tickers_after_market_cap_filter == 0

    @pytest.mark.asyncio
    @patch("tyche.workflow.conviction_batch.cleanup_old_snapshots", new_callable=AsyncMock)
    @patch("tyche.workflow.conviction_batch.detect_and_record_transitions", new_callable=AsyncMock)
    @patch("tyche.workflow.conviction_batch.upsert_snapshots", new_callable=AsyncMock)
    async def test_full_pipeline(self, mock_upsert, mock_transitions, mock_cleanup):
        mock_upsert.return_value = 2
        t1 = MagicMock()
        t1.to_state = "pullback_to_8ema"
        mock_transitions.return_value = [t1]
        mock_cleanup.return_value = 0

        signals = [
            _make_signal_mock("AAPL"),
            _make_signal_mock("MSFT"),
        ]

        store = _make_mock_data_store(
            exists=True,
            tickers=["AAPL", "MSFT"],
            ticker_data={
                "AAPL": _make_dataframe(last_close=185.0, avg_volume=60_000_000),
                "MSFT": _make_dataframe(last_close=420.0, avg_volume=25_000_000),
            },
        )
        engine = _make_mock_engine(signals)

        result = await run_conviction_batch(store, engine)
        assert result.signals_computed == 2
        assert result.snapshots_upserted == 2
        assert result.transitions_detected == 1
        assert result.new_pullback_transitions == 1
        assert result.duration_ms > 0

    @pytest.mark.asyncio
    @patch("tyche.workflow.conviction_batch.cleanup_old_snapshots", new_callable=AsyncMock)
    @patch("tyche.workflow.conviction_batch.detect_and_record_transitions", new_callable=AsyncMock)
    @patch("tyche.workflow.conviction_batch.upsert_snapshots", new_callable=AsyncMock)
    async def test_upsert_failure_logged(self, mock_upsert, mock_transitions, mock_cleanup):
        mock_upsert.side_effect = RuntimeError("DB error")
        mock_transitions.return_value = []
        mock_cleanup.return_value = 0

        signals = [_make_signal_mock("AAPL")]
        store = _make_mock_data_store(
            exists=True,
            tickers=["AAPL"],
            ticker_data={"AAPL": _make_dataframe()},
        )
        engine = _make_mock_engine(signals)

        result = await run_conviction_batch(store, engine)
        assert "Snapshot upsert failed" in result.errors
        assert result.snapshots_upserted == 0

    @pytest.mark.asyncio
    @patch("tyche.workflow.conviction_batch.cleanup_old_snapshots", new_callable=AsyncMock)
    @patch("tyche.workflow.conviction_batch.detect_and_record_transitions", new_callable=AsyncMock)
    @patch("tyche.workflow.conviction_batch.upsert_snapshots", new_callable=AsyncMock)
    async def test_transition_failure_logged(self, mock_upsert, mock_transitions, mock_cleanup):
        mock_upsert.return_value = 1
        mock_transitions.side_effect = RuntimeError("DB error")
        mock_cleanup.return_value = 0

        signals = [_make_signal_mock("AAPL")]
        store = _make_mock_data_store(
            exists=True,
            tickers=["AAPL"],
            ticker_data={"AAPL": _make_dataframe()},
        )
        engine = _make_mock_engine(signals)

        result = await run_conviction_batch(store, engine)
        assert "Transition detection failed" in result.errors


class TestConvictionBatchResult:
    def test_to_dict(self):
        result = ConvictionBatchResult(
            as_of_date=date(2026, 3, 28),
            total_tickers_in_store=13000,
            tickers_after_market_cap_filter=4000,
            tickers_after_price_volume_filter=3000,
            signals_computed=2800,
            snapshots_upserted=2800,
            transitions_detected=15,
            new_pullback_transitions=5,
            duration_ms=45123.456,
            errors=["some warning"],
        )
        d = result.to_dict()
        assert d["as_of_date"] == "2026-03-28"
        assert d["total_tickers_in_store"] == 13000
        assert d["duration_ms"] == 45123.46
        assert d["errors"] == ["some warning"]
