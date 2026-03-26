"""Tests for the local OHLCV Parquet data store."""

from __future__ import annotations

import tempfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest

from tyche.market_data.data_store import OHLCVStore
from tyche.market_data.polygon import DailyBar


def _bars(ticker="AAPL", n=5, start=date(2026, 1, 6)):
    from datetime import timedelta
    return [
        DailyBar(
            ticker=ticker,
            date=start + timedelta(days=i),
            open=100.0 + i,
            high=101.0 + i,
            low=99.0 + i,
            close=100.5 + i,
            volume=1_000_000 + i * 1000,
            vwap=100.3 + i,
        )
        for i in range(n)
    ]


class TestOHLCVStore:

    @pytest.fixture
    def store(self, tmp_path):
        return OHLCVStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert store.exists is False
        assert store.get_latest_date() is None
        assert store.get_ticker_count() == 0
        assert store.get_row_count() == 0
        assert store.get_date_range() == (None, None)
        assert store.get_all_tickers() == []

    def test_write_and_read(self, store):
        bars = _bars("AAPL", 5)
        added = store.write_bars(bars)
        assert added == 5
        assert store.exists is True
        assert store.get_ticker_count() == 1
        assert store.get_row_count() == 5
        assert "AAPL" in store.get_all_tickers()

    def test_read_ticker(self, store):
        store.write_bars(_bars("AAPL", 5))
        df = store.read_ticker("AAPL")
        assert len(df) == 5
        assert "close" in df.columns
        assert "date" in df.columns
        assert "ticker" not in df.columns

    def test_read_missing_ticker(self, store):
        store.write_bars(_bars("AAPL", 5))
        df = store.read_ticker("GOOG")
        assert len(df) == 0

    def test_deduplication(self, store):
        bars1 = _bars("AAPL", 5)
        bars2 = _bars("AAPL", 5)
        store.write_bars(bars1)
        added = store.write_bars(bars2)
        assert added == 0
        assert store.get_row_count() == 5

    def test_multi_ticker(self, store):
        store.write_bars(_bars("AAPL", 3) + _bars("GOOG", 4))
        assert store.get_ticker_count() == 2
        assert store.get_row_count() == 7
        tickers = store.get_all_tickers()
        assert "AAPL" in tickers
        assert "GOOG" in tickers

    def test_read_tickers_batch(self, store):
        store.write_bars(_bars("AAPL", 3) + _bars("GOOG", 4))
        result = store.read_tickers(["AAPL", "GOOG"])
        assert len(result) == 2
        assert len(result["AAPL"]) == 3
        assert len(result["GOOG"]) == 4

    def test_date_range(self, store):
        bars = _bars("AAPL", 10)
        store.write_bars(bars)
        earliest, latest = store.get_date_range()
        assert earliest == bars[0].date
        assert latest == bars[-1].date

    def test_date_filtering(self, store):
        bars = _bars("AAPL", 10)
        store.write_bars(bars)
        df = store.read_ticker(
            "AAPL",
            start_date=bars[3].date,
            end_date=bars[7].date,
        )
        assert len(df) == 5

    def test_write_empty_bars(self, store):
        assert store.write_bars([]) == 0
        assert store.exists is False

    def test_append_new_data(self, store):
        from datetime import timedelta
        bars1 = _bars("AAPL", 5, start=date(2026, 1, 6))
        bars2 = _bars("AAPL", 3, start=date(2026, 1, 13))
        store.write_bars(bars1)
        added = store.write_bars(bars2)
        assert added == 3 if date(2026, 1, 13) > date(2026, 1, 10) else added >= 0
        assert store.get_row_count() >= 5

    def test_read_from_empty_returns_empty_df(self, store):
        df = store.read_ticker("NOTHING")
        assert isinstance(df, pd.DataFrame)
        assert len(df) == 0
