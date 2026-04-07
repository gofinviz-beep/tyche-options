"""Tests for OptionsHistoryStore and the flat-file ingestion pipeline."""

from __future__ import annotations

import gzip
import io
import math
from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from tyche.market_data.options_history_store import OptionsHistoryStore


@pytest.fixture
def history_store(tmp_path):
    return OptionsHistoryStore(data_dir=str(tmp_path))


def _make_options_rows(
    underlying: str = "AAPL",
    trade_date: date = date(2025, 1, 6),
    count: int = 3,
) -> pd.DataFrame:
    """Generate synthetic options history rows."""
    rows = []
    for i in range(count):
        strike = 190.0 + i * 5
        strike_raw = f"{int(strike * 1000):08d}"
        exp = trade_date + timedelta(days=30)
        exp_str = exp.strftime("%y%m%d")
        ticker = f"O:{underlying}{exp_str}P{strike_raw}"
        rows.append(
            {
                "date": trade_date,
                "option_ticker": ticker,
                "underlying": underlying,
                "expiration": exp,
                "strike": strike,
                "option_type": "P",
                "open": 3.50 + i * 0.2,
                "close": 3.40 + i * 0.2,
                "high": 3.60 + i * 0.2,
                "low": 3.30 + i * 0.2,
                "volume": 1000 + i * 100,
                "transactions": 50 + i * 10,
                "dte": 30,
            }
        )
    return pd.DataFrame(rows)


class TestOptionsHistoryStore:
    def test_write_and_read(self, history_store: OptionsHistoryStore) -> None:
        df = _make_options_rows()
        rows = history_store.write_ticker_data("AAPL", df)
        assert rows == 3

        result = history_store.read_ticker("AAPL")
        assert len(result) == 3
        assert "option_ticker" in result.columns
        assert "underlying" in result.columns

    def test_dedup_on_date_and_ticker(
        self, history_store: OptionsHistoryStore
    ) -> None:
        df1 = _make_options_rows(trade_date=date(2025, 1, 6))
        df2 = _make_options_rows(trade_date=date(2025, 1, 6))
        df2["close"] = 99.0

        history_store.write_ticker_data("AAPL", df1)
        rows = history_store.write_ticker_data("AAPL", df2)
        assert rows == 3

        result = history_store.read_ticker("AAPL")
        assert all(result["close"] == 99.0)

    def test_merge_different_dates(
        self, history_store: OptionsHistoryStore
    ) -> None:
        df1 = _make_options_rows(trade_date=date(2025, 1, 6))
        df2 = _make_options_rows(trade_date=date(2025, 1, 7))

        history_store.write_ticker_data("AAPL", df1)
        rows = history_store.write_ticker_data("AAPL", df2)
        assert rows == 6

    def test_read_nonexistent(self, history_store: OptionsHistoryStore) -> None:
        df = history_store.read_ticker("NOPE")
        assert df.empty

    def test_read_date_range(self, history_store: OptionsHistoryStore) -> None:
        frames = []
        for i in range(5):
            frames.append(_make_options_rows(trade_date=date(2025, 1, 6 + i)))
        combined = pd.concat(frames, ignore_index=True)
        history_store.write_ticker_data("AAPL", combined)

        result = history_store.read_ticker(
            "AAPL", start_date=date(2025, 1, 8), end_date=date(2025, 1, 9)
        )
        dates = set(result["date"])
        assert dates == {date(2025, 1, 8), date(2025, 1, 9)}

    def test_write_batch(self, history_store: OptionsHistoryStore) -> None:
        batch = {
            "AAPL": _make_options_rows("AAPL"),
            "MSFT": _make_options_rows("MSFT"),
        }
        results = history_store.write_batch(batch)
        assert results["AAPL"] == 3
        assert results["MSFT"] == 3

        assert set(history_store.get_all_tickers()) == {"AAPL", "MSFT"}

    def test_empty_write(self, history_store: OptionsHistoryStore) -> None:
        assert history_store.write_ticker_data("AAPL", pd.DataFrame()) == 0

    def test_get_all_tickers(self, history_store: OptionsHistoryStore) -> None:
        history_store.write_ticker_data("AAPL", _make_options_rows("AAPL"))
        history_store.write_ticker_data("MSFT", _make_options_rows("MSFT"))
        history_store.write_ticker_data("GOOGL", _make_options_rows("GOOGL"))

        tickers = history_store.get_all_tickers()
        assert tickers == ["AAPL", "GOOGL", "MSFT"]

    def test_get_stats(self, history_store: OptionsHistoryStore) -> None:
        history_store.write_ticker_data("AAPL", _make_options_rows("AAPL"))
        stats = history_store.get_stats()
        assert stats["ticker_count"] == 1
        assert stats["total_rows"] == 3

    def test_get_stats_empty(self, history_store: OptionsHistoryStore) -> None:
        stats = history_store.get_stats()
        assert stats["ticker_count"] == 0
        assert stats["total_rows"] == 0


class TestProgressTracking:
    def test_no_progress_initially(self, history_store: OptionsHistoryStore) -> None:
        assert history_store.get_completed_dates() == set()

    def test_mark_and_read(self, history_store: OptionsHistoryStore) -> None:
        history_store.mark_dates_completed(["2025-01-06", "2025-01-07"])
        completed = history_store.get_completed_dates()
        assert completed == {"2025-01-06", "2025-01-07"}

    def test_incremental_marking(self, history_store: OptionsHistoryStore) -> None:
        history_store.mark_dates_completed(["2025-01-06"])
        history_store.mark_dates_completed(["2025-01-07", "2025-01-08"])

        completed = history_store.get_completed_dates()
        assert completed == {"2025-01-06", "2025-01-07", "2025-01-08"}

    def test_idempotent_marking(self, history_store: OptionsHistoryStore) -> None:
        history_store.mark_dates_completed(["2025-01-06"])
        history_store.mark_dates_completed(["2025-01-06"])

        completed = history_store.get_completed_dates()
        assert completed == {"2025-01-06"}

    def test_corrupted_progress_file(
        self, history_store: OptionsHistoryStore
    ) -> None:
        history_store._progress_path.write_text("not json")
        assert history_store.get_completed_dates() == set()


class TestFlatFilePipeline:
    """Integration tests for the CSV processing pipeline."""

    def _make_csv_gz(self, rows: list[dict]) -> bytes:
        """Create a gzipped CSV from row dicts."""
        df = pd.DataFrame(rows)
        csv_bytes = df.to_csv(index=False).encode("utf-8")
        buf = io.BytesIO()
        with gzip.GzipFile(fileobj=buf, mode="wb") as gz:
            gz.write(csv_bytes)
        buf.seek(0)
        return buf.read()

    def _make_flat_file_row(
        self,
        underlying: str = "AAPL",
        exp_date: date = date(2025, 2, 5),
        option_type: str = "P",
        strike: float = 190.0,
        close: float = 3.50,
        volume: int = 1000,
        transactions: int = 50,
    ) -> dict:
        """Create a single row matching the Polygon flat file format."""
        strike_raw = f"{int(strike * 1000):08d}"
        exp_str = exp_date.strftime("%y%m%d")
        ticker = f"O:{underlying}{exp_str}{option_type}{strike_raw}"
        return {
            "ticker": ticker,
            "open": close + 0.10,
            "close": close,
            "high": close + 0.20,
            "low": close - 0.10,
            "volume": volume,
            "transactions": transactions,
            "window_start": 1706745600000000000,
        }

    def test_process_date_file_filters_universe(self) -> None:
        from scripts.ingest_options_flatfiles import _process_date_file

        rows = [
            self._make_flat_file_row("AAPL", strike=190.0),
            self._make_flat_file_row("AAPL", strike=195.0),
            self._make_flat_file_row("MSFT", strike=400.0),
            self._make_flat_file_row("NOPE", strike=50.0),
        ]
        csv_gz = self._make_csv_gz(rows)

        universe = {"AAPL", "MSFT"}
        ohlcv_closes: dict = {}

        result = _process_date_file(
            csv_gz, date(2025, 1, 6), universe, ohlcv_closes
        )

        assert len(result) == 3
        assert set(result["underlying"]) == {"AAPL", "MSFT"}

    def test_process_date_file_parses_occ(self) -> None:
        from scripts.ingest_options_flatfiles import _process_date_file

        rows = [
            self._make_flat_file_row(
                "SPY",
                exp_date=date(2025, 3, 27),
                option_type="P",
                strike=390.0,
            ),
        ]
        csv_gz = self._make_csv_gz(rows)

        result = _process_date_file(
            csv_gz, date(2025, 2, 25), {"SPY"}, {}
        )

        assert len(result) == 1
        row = result.iloc[0]
        assert row["underlying"] == "SPY"
        assert row["option_type"] == "P"
        assert row["strike"] == 390.0
        assert row["dte"] == 30

    def test_process_date_file_empty_universe(self) -> None:
        from scripts.ingest_options_flatfiles import _process_date_file

        rows = [self._make_flat_file_row("AAPL")]
        csv_gz = self._make_csv_gz(rows)

        result = _process_date_file(csv_gz, date(2025, 1, 6), set(), {})
        assert result.empty

    def test_process_date_file_multiple_option_types(self) -> None:
        from scripts.ingest_options_flatfiles import _process_date_file

        rows = [
            self._make_flat_file_row("AAPL", option_type="P", strike=190.0),
            self._make_flat_file_row("AAPL", option_type="C", strike=195.0),
        ]
        csv_gz = self._make_csv_gz(rows)

        result = _process_date_file(csv_gz, date(2025, 1, 6), {"AAPL"}, {})

        assert len(result) == 2
        assert set(result["option_type"]) == {"P", "C"}


class TestTradingDates:
    def test_excludes_weekends(self) -> None:
        from scripts.ingest_options_flatfiles import _trading_dates

        start = date(2025, 1, 6)  # Monday
        end = date(2025, 1, 12)  # Sunday
        dates = _trading_dates(start, end)

        assert len(dates) == 5
        for d in dates:
            assert d.weekday() < 5

    def test_single_day(self) -> None:
        from scripts.ingest_options_flatfiles import _trading_dates

        dates = _trading_dates(date(2025, 1, 6), date(2025, 1, 6))
        assert dates == [date(2025, 1, 6)]

    def test_weekend_only(self) -> None:
        from scripts.ingest_options_flatfiles import _trading_dates

        dates = _trading_dates(date(2025, 1, 11), date(2025, 1, 12))
        assert dates == []


class TestATMIVExtraction:
    """Test IV extraction from stored options history."""

    def test_extract_atm_iv(self, tmp_path) -> None:
        from tyche.market_data.historical_iv_store import HistoricalIVStore

        history_store = OptionsHistoryStore(data_dir=str(tmp_path))
        iv_store = HistoricalIVStore(data_dir=str(tmp_path))

        ohlcv_df = pd.DataFrame(
            {
                "date": [date(2025, 1, 6)],
                "open": [190.0],
                "high": [195.0],
                "low": [188.0],
                "close": [192.0],
                "volume": [1000000],
            }
        )

        exp = date(2025, 2, 5)
        options_df = pd.DataFrame(
            [
                {
                    "date": date(2025, 1, 6),
                    "option_ticker": "O:AAPL250205P00190000",
                    "underlying": "AAPL",
                    "expiration": exp,
                    "strike": 190.0,
                    "option_type": "P",
                    "open": 4.00,
                    "close": 3.80,
                    "high": 4.20,
                    "low": 3.60,
                    "volume": 500,
                    "transactions": 20,
                    "dte": 30,
                },
                {
                    "date": date(2025, 1, 6),
                    "option_ticker": "O:AAPL250205P00195000",
                    "underlying": "AAPL",
                    "expiration": exp,
                    "strike": 195.0,
                    "option_type": "P",
                    "open": 6.00,
                    "close": 5.80,
                    "high": 6.20,
                    "low": 5.60,
                    "volume": 300,
                    "transactions": 15,
                    "dte": 30,
                },
                {
                    "date": date(2025, 1, 6),
                    "option_ticker": "O:AAPL250205C00195000",
                    "underlying": "AAPL",
                    "expiration": exp,
                    "strike": 195.0,
                    "option_type": "C",
                    "open": 2.00,
                    "close": 1.80,
                    "high": 2.20,
                    "low": 1.60,
                    "volume": 400,
                    "transactions": 18,
                    "dte": 30,
                },
            ]
        )

        history_store.write_ticker_data("AAPL", options_df)

        mock_ohlcv_store = MagicMock()
        mock_ohlcv_store.read_ticker.return_value = ohlcv_df

        from scripts.ingest_options_flatfiles import _extract_atm_iv_from_history

        iv_count = _extract_atm_iv_from_history(
            history_store, mock_ohlcv_store, iv_store, "AAPL"
        )

        assert iv_count == 1

        iv_df = iv_store.read_ticker("AAPL")
        assert len(iv_df) == 1
        row = iv_df.iloc[0]
        assert row["strike"] == 190.0
        assert not math.isnan(row["implied_volatility"])
        assert 0 < row["implied_volatility"] < 5.0

    def test_extract_skips_calls(self, tmp_path) -> None:
        from tyche.market_data.historical_iv_store import HistoricalIVStore

        history_store = OptionsHistoryStore(data_dir=str(tmp_path))
        iv_store = HistoricalIVStore(data_dir=str(tmp_path))

        exp = date(2025, 2, 5)
        options_df = pd.DataFrame(
            [
                {
                    "date": date(2025, 1, 6),
                    "option_ticker": "O:AAPL250205C00195000",
                    "underlying": "AAPL",
                    "expiration": exp,
                    "strike": 195.0,
                    "option_type": "C",
                    "open": 2.00,
                    "close": 1.80,
                    "high": 2.20,
                    "low": 1.60,
                    "volume": 400,
                    "transactions": 18,
                    "dte": 30,
                },
            ]
        )
        history_store.write_ticker_data("AAPL", options_df)

        mock_ohlcv_store = MagicMock()
        mock_ohlcv_store.read_ticker.return_value = pd.DataFrame(
            {
                "date": [date(2025, 1, 6)],
                "close": [192.0],
            }
        )

        from scripts.ingest_options_flatfiles import _extract_atm_iv_from_history

        iv_count = _extract_atm_iv_from_history(
            history_store, mock_ohlcv_store, iv_store, "AAPL"
        )
        assert iv_count == 0

    def test_extract_empty_history(self, tmp_path) -> None:
        from tyche.market_data.historical_iv_store import HistoricalIVStore

        history_store = OptionsHistoryStore(data_dir=str(tmp_path))
        iv_store = HistoricalIVStore(data_dir=str(tmp_path))
        mock_ohlcv_store = MagicMock()

        from scripts.ingest_options_flatfiles import _extract_atm_iv_from_history

        iv_count = _extract_atm_iv_from_history(
            history_store, mock_ohlcv_store, iv_store, "NOPE"
        )
        assert iv_count == 0
