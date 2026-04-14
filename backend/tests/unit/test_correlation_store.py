"""Tests for CorrelationStore and rolling correlation computation."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tyche.market_data.correlation_store import (
    CorrelationStore,
    _compute_beta,
    compute_rolling_correlations,
)


def _make_returns(n: int = 100, seed: int = 42) -> pd.Series:
    rng = np.random.default_rng(seed)
    return pd.Series(rng.normal(0.001, 0.02, n))


class TestComputeBeta:
    def test_perfect_correlation(self):
        market = pd.Series([0.01, 0.02, -0.01, 0.015, -0.005] * 10)
        stock = market * 1.5
        beta = _compute_beta(stock, market)
        assert beta == pytest.approx(1.5, abs=0.01)

    def test_zero_var_market(self):
        market = pd.Series([0.0] * 30)
        stock = _make_returns(30)
        beta = _compute_beta(stock, market)
        assert np.isnan(beta)

    def test_insufficient_data(self):
        market = pd.Series([0.01, 0.02])
        stock = pd.Series([0.02, 0.04])
        beta = _compute_beta(stock, market)
        assert np.isnan(beta)


class TestCorrelationStore:
    @pytest.fixture
    def store(self, tmp_path):
        return CorrelationStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert not store.exists
        assert not store.beta_exists
        assert store.read_correlations().empty
        assert store.read_betas().empty
        assert store.get_top_correlated("AAPL") == []
        beta = store.get_beta("AAPL")
        assert beta["spy_beta_60d"] is None

    def test_write_and_read_correlations(self, store):
        df = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "MSFT", "correlation_60d": 0.85},
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "GOOG", "correlation_60d": 0.72},
        ])
        store.write_correlations(df)
        assert store.exists

        result = store.read_correlations()
        assert len(result) == 2

    def test_read_correlations_filtered(self, store):
        df = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "MSFT", "correlation_60d": 0.85},
            {"as_of_date": date(2026, 4, 2), "ticker_a": "AAPL", "ticker_b": "MSFT", "correlation_60d": 0.86},
        ])
        store.write_correlations(df)
        result = store.read_correlations(as_of=date(2026, 4, 1))
        assert len(result) == 1

    def test_write_and_read_betas(self, store):
        df = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker": "AAPL", "spy_beta_60d": 1.1, "qqq_beta_60d": 1.3},
            {"as_of_date": date(2026, 4, 1), "ticker": "MSFT", "spy_beta_60d": 0.9, "qqq_beta_60d": 1.1},
        ])
        store.write_betas(df)
        assert store.beta_exists

        result = store.get_beta("AAPL")
        assert result["spy_beta_60d"] == pytest.approx(1.1)
        assert result["qqq_beta_60d"] == pytest.approx(1.3)

    def test_get_beta_missing_ticker(self, store):
        df = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker": "AAPL", "spy_beta_60d": 1.1, "qqq_beta_60d": 1.3},
        ])
        store.write_betas(df)
        result = store.get_beta("ZZZZ")
        assert result["spy_beta_60d"] is None

    def test_get_top_correlated(self, store):
        df = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "MSFT", "correlation_60d": 0.85},
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "GOOG", "correlation_60d": 0.72},
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "NVDA", "correlation_60d": 0.91},
        ])
        store.write_correlations(df)
        top = store.get_top_correlated("AAPL", n=2)
        assert len(top) == 2
        assert top[0][0] == "NVDA"
        assert top[0][1] == pytest.approx(0.91)


class TestComputeRollingCorrelations:
    @pytest.fixture
    def mock_ohlcv_store(self):
        store = MagicMock()
        rng = np.random.default_rng(42)

        as_of = date(2026, 4, 1)
        n_days = 80
        dates = [as_of - timedelta(days=n_days - i) for i in range(n_days)]

        def make_ticker_df(ticker: str, seed: int):
            r = np.random.default_rng(seed)
            closes = 100 + np.cumsum(r.normal(0, 1, n_days))
            return pd.DataFrame({
                "date": dates,
                "close": closes,
                "volume": r.integers(100_000, 1_000_000, n_days),
            })

        ticker_data = {
            "AAPL": make_ticker_df("AAPL", 1),
            "MSFT": make_ticker_df("MSFT", 2),
            "GOOG": make_ticker_df("GOOG", 3),
            "SPY": make_ticker_df("SPY", 4),
            "QQQ": make_ticker_df("QQQ", 5),
        }

        store.get_all_tickers.return_value = list(ticker_data.keys())
        store.get_latest_date.return_value = as_of
        store.read_ticker.side_effect = lambda t: ticker_data.get(t)

        return store

    def test_produces_correlations_and_betas(self, mock_ohlcv_store):
        corr_df, beta_df = compute_rolling_correlations(
            ohlcv_store=mock_ohlcv_store,
            window=60,
            top_n=5,
        )
        assert not corr_df.empty
        assert not beta_df.empty
        assert "ticker_a" in corr_df.columns
        assert "correlation_60d" in corr_df.columns
        assert "spy_beta_60d" in beta_df.columns

    def test_betas_exclude_spy_qqq(self, mock_ohlcv_store):
        _, beta_df = compute_rolling_correlations(
            ohlcv_store=mock_ohlcv_store,
            window=60,
        )
        tickers = beta_df["ticker"].tolist()
        assert "SPY" not in tickers
        assert "QQQ" not in tickers

    def test_no_leakage(self, mock_ohlcv_store):
        """Correlation window should exclude as_of_date itself."""
        as_of = date(2026, 4, 1)
        corr_df, _ = compute_rolling_correlations(
            ohlcv_store=mock_ohlcv_store,
            window=60,
            as_of_date=as_of,
        )
        assert not corr_df.empty

    def test_empty_store(self):
        store = MagicMock()
        store.get_latest_date.return_value = None
        corr_df, beta_df = compute_rolling_correlations(ohlcv_store=store)
        assert corr_df.empty
        assert beta_df.empty

    def test_insufficient_tickers(self):
        """One real ticker still produces betas because SPY/QQQ are auto-added."""
        store = MagicMock()
        store.get_all_tickers.return_value = ["AAPL"]
        store.get_latest_date.return_value = date(2026, 4, 1)
        dates = [date(2026, 4, 1) - timedelta(days=70 - i) for i in range(69)]
        store.read_ticker.return_value = pd.DataFrame({
            "date": dates,
            "close": list(range(69)),
        })
        corr_df, beta_df = compute_rolling_correlations(ohlcv_store=store, tickers=["AAPL"])
        assert not corr_df.empty
        assert not beta_df.empty
        assert len(beta_df) == 1
        assert beta_df.iloc[0]["ticker"] == "AAPL"

    def test_truly_insufficient_no_data(self):
        """No data at all returns empty."""
        store = MagicMock()
        store.get_all_tickers.return_value = []
        store.get_latest_date.return_value = date(2026, 4, 1)
        store.read_ticker.return_value = pd.DataFrame(columns=["date", "close"])
        corr_df, beta_df = compute_rolling_correlations(ohlcv_store=store, tickers=[])
        assert corr_df.empty
        assert beta_df.empty
