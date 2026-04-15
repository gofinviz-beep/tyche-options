"""Tests for ETF and correlation feature integration into the ML pipeline."""

from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock

import numpy as np
import pandas as pd
import pytest

from tyche.ml.features import (
    CORRELATION_FEATURE_COLS,
    ETF_FEATURE_COLS,
    MARKET_CONTEXT_COLS,
    add_correlation_features,
    add_etf_features,
    add_market_context_features,
)


def _make_feature_df(tickers: list[str] | None = None, n_per_ticker: int = 5) -> pd.DataFrame:
    """Create a minimal feature DataFrame for testing."""
    tickers = tickers or ["AAPL", "MSFT", "NVDA"]
    rows = []
    for ticker in tickers:
        for i in range(n_per_ticker):
            rows.append({
                "ticker": ticker,
                "date": date(2026, 1, 1 + i),
                "close": 100.0 + i,
                "rsi_14": 50.0,
            })
    return pd.DataFrame(rows)


class TestAddETFFeatures:
    def test_no_store_fills_zeros(self):
        df = _make_feature_df()
        result = add_etf_features(df, etf_store=None)
        for col in ETF_FEATURE_COLS:
            assert col in result.columns
            assert (result[col] == 0.0).all()

    def test_with_store(self):
        store = MagicMock()
        store.get_membership_counts.return_value = {"AAPL": 3, "MSFT": 2, "NVDA": 5}
        store.get_membership_matrix.return_value = {
            "AAPL": ["QQQ", "SPY", "XLK"],
            "MSFT": ["QQQ", "SPY"],
            "NVDA": ["DIA", "QQQ", "SMH", "SOXX", "SPY"],
        }
        store.get_etf_weights.side_effect = lambda etf: {
            "SPY": {"AAPL": 0.065, "MSFT": 0.050, "NVDA": 0.075},
            "QQQ": {"AAPL": 0.076, "MSFT": 0.056, "NVDA": 0.087},
            "DIA": {"NVDA": 0.04},
            "XLK": {"AAPL": 0.13},
            "XLF": {},
            "XLE": {},
            "XLV": {},
            "SMH": {"NVDA": 0.15},
            "SOXX": {"NVDA": 0.14},
            "XLI": {},
        }.get(etf, {})

        df = _make_feature_df()
        result = add_etf_features(df, etf_store=store)

        aapl_rows = result[result["ticker"] == "AAPL"]
        assert aapl_rows["etf_membership_count"].iloc[0] == 3
        assert aapl_rows["in_spy"].iloc[0] == 1
        assert aapl_rows["in_qqq"].iloc[0] == 1
        assert aapl_rows["in_dia"].iloc[0] == 0
        assert aapl_rows["spy_weight"].iloc[0] == pytest.approx(0.065)

        nvda_rows = result[result["ticker"] == "NVDA"]
        assert nvda_rows["in_dia"].iloc[0] == 1
        assert nvda_rows["max_etf_weight"].iloc[0] == pytest.approx(0.15)

    def test_empty_df(self):
        result = add_etf_features(pd.DataFrame(), etf_store=None)
        assert result.empty

    def test_missing_ticker_fills_defaults(self):
        store = MagicMock()
        store.get_membership_counts.return_value = {}
        store.get_membership_matrix.return_value = {}
        store.get_etf_weights.return_value = {}

        df = _make_feature_df(["UNKNOWN"])
        result = add_etf_features(df, etf_store=store)
        assert result["etf_membership_count"].iloc[0] == 0
        assert result["in_spy"].iloc[0] == 0


class TestAddCorrelationFeatures:
    def test_no_store_fills_nan(self):
        df = _make_feature_df()
        result = add_correlation_features(df, correlation_store=None)
        for col in CORRELATION_FEATURE_COLS:
            assert col in result.columns
            assert result[col].isna().all()

    def test_with_store(self):
        store = MagicMock()
        store.read_betas.return_value = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker": "AAPL", "spy_beta_60d": 1.1, "qqq_beta_60d": 1.3},
            {"as_of_date": date(2026, 4, 1), "ticker": "MSFT", "spy_beta_60d": 0.9, "qqq_beta_60d": 1.1},
        ])
        store.read_correlations.return_value = pd.DataFrame([
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "MSFT", "correlation_60d": 0.85},
            {"as_of_date": date(2026, 4, 1), "ticker_a": "AAPL", "ticker_b": "NVDA", "correlation_60d": 0.90},
        ])

        df = _make_feature_df()
        result = add_correlation_features(df, correlation_store=store)

        aapl = result[result["ticker"] == "AAPL"]
        assert aapl["spy_beta_60d"].iloc[0] == pytest.approx(1.1)
        assert aapl["qqq_beta_60d"].iloc[0] == pytest.approx(1.3)
        assert aapl["top_peer_corr_mean"].iloc[0] == pytest.approx(0.875)
        assert aapl["top_peer_corr_max"].iloc[0] == pytest.approx(0.90)

    def test_empty_betas(self):
        store = MagicMock()
        store.read_betas.return_value = pd.DataFrame()
        store.read_correlations.return_value = pd.DataFrame()

        df = _make_feature_df()
        result = add_correlation_features(df, correlation_store=store)
        assert result["spy_beta_60d"].isna().all()
        assert result["top_peer_corr_mean"].isna().all()

    def test_empty_df(self):
        result = add_correlation_features(pd.DataFrame(), correlation_store=None)
        assert result.empty


class TestFeatureColumnLists:
    def test_etf_cols_defined(self):
        assert len(ETF_FEATURE_COLS) == 7
        assert "etf_membership_count" in ETF_FEATURE_COLS
        assert "in_spy" in ETF_FEATURE_COLS

    def test_corr_cols_defined(self):
        assert len(CORRELATION_FEATURE_COLS) == 5
        assert "spy_beta_60d" in CORRELATION_FEATURE_COLS

    def test_market_context_cols_defined(self):
        assert len(MARKET_CONTEXT_COLS) == 6
        assert "concurrent_dips" in MARKET_CONTEXT_COLS
        assert "spy_drawdown_from_high" in MARKET_CONTEXT_COLS

    def test_no_overlap_with_feature_cols(self):
        from tyche.ml.features import FEATURE_COLS, NEIGHBOR_FEATURE_COLS

        all_existing = set(FEATURE_COLS + NEIGHBOR_FEATURE_COLS)
        new_cols = set(ETF_FEATURE_COLS + CORRELATION_FEATURE_COLS + MARKET_CONTEXT_COLS)
        overlap = all_existing & new_cols
        assert overlap == set(), f"Column name collision: {overlap}"


class TestAddMarketContextFeatures:
    def _make_df_with_ema(self):
        rows = []
        for ticker in ["AAPL", "MSFT", "NVDA"]:
            for i in range(5):
                rows.append({
                    "ticker": ticker,
                    "date": date(2026, 1, 1 + i),
                    "price_to_21ema_pct": -6.0 if ticker != "NVDA" else 2.0,
                })
        return pd.DataFrame(rows)

    def test_no_spy_fills_nan(self):
        df = self._make_df_with_ema()
        result = add_market_context_features(df, spy_ohlcv=None)
        assert "spy_return_5d" in result.columns
        assert result["spy_return_5d"].isna().all()
        assert "concurrent_dips" in result.columns
        assert not result["concurrent_dips"].isna().any()

    def test_concurrent_dips_counted(self):
        df = self._make_df_with_ema()
        result = add_market_context_features(df, spy_ohlcv=None)
        row = result[result["date"] == date(2026, 1, 1)].iloc[0]
        assert row["concurrent_dips"] == 2

    def test_market_dip_breadth(self):
        df = self._make_df_with_ema()
        result = add_market_context_features(df, spy_ohlcv=None)
        row = result[result["date"] == date(2026, 1, 1)].iloc[0]
        assert row["market_dip_breadth"] == pytest.approx(2 / 3)

    def test_with_spy_ohlcv(self):
        df = self._make_df_with_ema()
        spy = pd.DataFrame({
            "date": [date(2026, 1, 1 + i) for i in range(20)],
            "close": [500 - i * 2 for i in range(20)],
            "high": [505 - i * 2 for i in range(20)],
            "low": [495 - i * 2 for i in range(20)],
        })
        result = add_market_context_features(df, spy_ohlcv=spy)
        assert "spy_rsi_14" in result.columns
        assert "spy_drawdown_from_high" in result.columns
        valid = result["spy_rsi_14"].dropna()
        assert len(valid) > 0

    def test_empty_df(self):
        result = add_market_context_features(pd.DataFrame(), spy_ohlcv=None)
        assert result.empty


class TestGetFeatureColumns:
    def test_includes_etf_and_corr_by_default(self):
        from tyche.ml.xgb_baseline import get_feature_columns

        cols = get_feature_columns(include_neighbors=False)
        for c in ETF_FEATURE_COLS:
            assert c in cols
        for c in CORRELATION_FEATURE_COLS:
            assert c in cols
        for c in MARKET_CONTEXT_COLS:
            assert c in cols

    def test_excludes_when_flags_false(self):
        from tyche.ml.xgb_baseline import get_feature_columns

        cols = get_feature_columns(
            include_neighbors=False,
            include_etf=False,
            include_correlation=False,
            include_market_context=False,
        )
        for c in ETF_FEATURE_COLS:
            assert c not in cols
        for c in CORRELATION_FEATURE_COLS:
            assert c not in cols
        for c in MARKET_CONTEXT_COLS:
            assert c not in cols

    def test_includes_all(self):
        from tyche.ml.xgb_baseline import get_feature_columns
        from tyche.ml.features import FEATURE_COLS, NEIGHBOR_FEATURE_COLS

        cols = get_feature_columns(
            include_neighbors=True,
            include_etf=True,
            include_correlation=True,
            include_market_context=True,
        )
        total = (
            len(FEATURE_COLS) + len(NEIGHBOR_FEATURE_COLS)
            + len(ETF_FEATURE_COLS) + len(CORRELATION_FEATURE_COLS)
            + len(MARKET_CONTEXT_COLS)
        )
        assert len(cols) == total
