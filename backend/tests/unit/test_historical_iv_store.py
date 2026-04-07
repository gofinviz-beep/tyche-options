"""Tests for HistoricalIVStore and DerivedMetricsStore."""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.market_data.historical_iv_store import HistoricalIVStore


@pytest.fixture
def iv_store(tmp_path):
    return HistoricalIVStore(data_dir=str(tmp_path))


@pytest.fixture
def derived_store(tmp_path):
    return DerivedMetricsStore(data_dir=str(tmp_path))


def _make_iv_records(start: date, count: int, base_iv: float = 0.30) -> list[dict]:
    """Generate synthetic IV records for testing."""
    records = []
    for i in range(count):
        d = date(start.year, start.month, start.day + i) if start.day + i <= 28 else start
        records.append(
            {
                "date": d,
                "strike": 100.0,
                "expiration": date(2025, 6, 20),
                "contract_ticker": f"O:TEST250620P00100000",
                "option_close": 3.50 + i * 0.1,
                "underlying_close": 100.0 + i * 0.5,
                "dte": 30 - i,
                "implied_volatility": base_iv + i * 0.005,
            }
        )
    return records


class TestHistoricalIVStore:
    def test_write_and_read(self, iv_store: HistoricalIVStore) -> None:
        records = _make_iv_records(date(2025, 1, 6), 5)
        rows = iv_store.write_iv_data("AAPL", records)
        assert rows == 5

        df = iv_store.read_ticker("AAPL")
        assert len(df) == 5
        assert "implied_volatility" in df.columns

    def test_write_deduplicates_on_date(self, iv_store: HistoricalIVStore) -> None:
        records1 = _make_iv_records(date(2025, 1, 6), 3, base_iv=0.25)
        records2 = _make_iv_records(date(2025, 1, 6), 3, base_iv=0.35)

        iv_store.write_iv_data("AAPL", records1)
        rows = iv_store.write_iv_data("AAPL", records2)
        assert rows == 3

        df = iv_store.read_ticker("AAPL")
        assert df.iloc[0]["implied_volatility"] == pytest.approx(0.35, abs=0.001)

    def test_read_nonexistent_ticker(self, iv_store: HistoricalIVStore) -> None:
        df = iv_store.read_ticker("NOPE")
        assert df.empty

    def test_get_latest_date(self, iv_store: HistoricalIVStore) -> None:
        records = _make_iv_records(date(2025, 1, 6), 5)
        iv_store.write_iv_data("MSFT", records)

        latest = iv_store.get_latest_date("MSFT")
        assert latest == date(2025, 1, 10)

    def test_get_latest_date_nonexistent(self, iv_store: HistoricalIVStore) -> None:
        assert iv_store.get_latest_date("NOPE") is None

    def test_get_all_tickers(self, iv_store: HistoricalIVStore) -> None:
        iv_store.write_iv_data("AAPL", _make_iv_records(date(2025, 1, 6), 3))
        iv_store.write_iv_data("MSFT", _make_iv_records(date(2025, 1, 6), 3))

        tickers = iv_store.get_all_tickers()
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_get_stats(self, iv_store: HistoricalIVStore) -> None:
        iv_store.write_iv_data("AAPL", _make_iv_records(date(2025, 1, 6), 5))
        stats = iv_store.get_stats()
        assert stats["ticker_count"] == 1
        assert stats["total_rows"] == 5

    def test_empty_write(self, iv_store: HistoricalIVStore) -> None:
        assert iv_store.write_iv_data("AAPL", []) == 0

    def test_read_with_date_range(self, iv_store: HistoricalIVStore) -> None:
        records = _make_iv_records(date(2025, 1, 6), 10)
        iv_store.write_iv_data("AAPL", records)

        df = iv_store.read_ticker("AAPL", start_date=date(2025, 1, 8))
        assert all(d >= date(2025, 1, 8) for d in df["date"])


class TestDerivedMetricsStore:
    def test_write_and_read(self, derived_store: DerivedMetricsStore) -> None:
        df = pd.DataFrame(
            {
                "date": [date(2025, 1, 6), date(2025, 1, 7)],
                "atm_iv": [0.30, 0.32],
                "iv_rank": [45.0, 50.0],
                "iv_percentile": [40.0, 55.0],
                "rv_20d": [0.25, 0.26],
                "vrp": [0.05, 0.06],
            }
        )
        rows = derived_store.write_metrics("AAPL", df)
        assert rows == 2

        result = derived_store.read_ticker("AAPL")
        assert len(result) == 2

    def test_read_nonexistent_ticker(self, derived_store: DerivedMetricsStore) -> None:
        df = derived_store.read_ticker("NOPE")
        assert df.empty

    def test_get_all_tickers(self, derived_store: DerivedMetricsStore) -> None:
        df = pd.DataFrame(
            {
                "date": [date(2025, 1, 6)],
                "atm_iv": [0.30],
                "iv_rank": [50.0],
                "iv_percentile": [50.0],
                "rv_20d": [0.25],
                "vrp": [0.05],
            }
        )
        derived_store.write_metrics("AAPL", df)
        derived_store.write_metrics("MSFT", df)

        tickers = derived_store.get_all_tickers()
        assert len(tickers) == 2

    def test_compute_metrics_basic(self) -> None:
        """Verify derived metrics computation with synthetic data."""
        n_days = 300
        dates = pd.date_range("2024-01-01", periods=n_days, freq="B")

        np.random.seed(42)
        iv_values = 0.30 + np.cumsum(np.random.randn(n_days) * 0.005)
        iv_df = pd.DataFrame({"date": dates, "implied_volatility": iv_values})

        prices = 100 * np.exp(np.cumsum(np.random.randn(n_days) * 0.01))
        ohlcv_df = pd.DataFrame({"date": dates, "close": prices})

        result = DerivedMetricsStore.compute_metrics(iv_df, ohlcv_df)

        assert not result.empty
        assert "iv_rank" in result.columns
        assert "iv_percentile" in result.columns
        assert "rv_20d" in result.columns
        assert "vrp" in result.columns

        valid_rank = result[result["iv_rank"].notna()]
        assert len(valid_rank) > 0
        assert all(0 <= r <= 100 for r in valid_rank["iv_rank"])

    def test_compute_metrics_empty_iv(self) -> None:
        iv_df = pd.DataFrame(columns=["date", "implied_volatility"])
        ohlcv_df = pd.DataFrame({"date": [date(2025, 1, 6)], "close": [100.0]})
        result = DerivedMetricsStore.compute_metrics(iv_df, ohlcv_df)
        assert result.empty

    def test_deduplicates_on_date(self, derived_store: DerivedMetricsStore) -> None:
        df1 = pd.DataFrame(
            {
                "date": [date(2025, 1, 6)],
                "atm_iv": [0.30],
                "iv_rank": [45.0],
                "iv_percentile": [40.0],
                "rv_20d": [0.25],
                "vrp": [0.05],
            }
        )
        df2 = pd.DataFrame(
            {
                "date": [date(2025, 1, 6)],
                "atm_iv": [0.35],
                "iv_rank": [55.0],
                "iv_percentile": [50.0],
                "rv_20d": [0.28],
                "vrp": [0.07],
            }
        )
        derived_store.write_metrics("AAPL", df1)
        derived_store.write_metrics("AAPL", df2)

        result = derived_store.read_ticker("AAPL")
        assert len(result) == 1
        assert result.iloc[0]["atm_iv"] == pytest.approx(0.35, abs=0.001)
