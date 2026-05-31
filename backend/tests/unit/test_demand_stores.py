"""Tests for the Phase 0 demand-data stores.

FundamentalsStore, EstimatesStore, ShortInterestStore — point-in-time
Parquet stores backing the Demand Conviction engine.
"""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tyche.market_data.estimates_store import EstimatesStore
from tyche.market_data.fundamentals_store import FundamentalsStore
from tyche.market_data.short_interest_store import ShortInterestStore


# ── FundamentalsStore ──────────────────────────────────────────────────


class TestFundamentalsStore:
    @pytest.fixture
    def store(self, tmp_path):
        return FundamentalsStore(data_dir=str(tmp_path))

    def _rows(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "period_end": "2025-12-31",
                    "filing_date": "2026-01-28",
                    "fiscal_year": 2025,
                    "fiscal_period": "Q4",
                    "timeframe": "quarterly",
                    "revenue": 1000.0,
                    "gross_profit": 400.0,
                    "operating_income": 200.0,
                    "net_income": 150.0,
                    "eps_diluted": 1.50,
                    "operating_cash_flow": 250.0,
                    "capex": -50.0,
                    "shares_diluted": 100.0,
                },
                {
                    "period_end": "2025-09-30",
                    "filing_date": "2025-10-28",
                    "fiscal_year": 2025,
                    "fiscal_period": "Q3",
                    "timeframe": "quarterly",
                    "revenue": 800.0,
                    "gross_profit": 300.0,
                    "operating_income": 140.0,
                    "net_income": 100.0,
                    "eps_diluted": 1.00,
                    "operating_cash_flow": 180.0,
                    "capex": -40.0,
                    "shares_diluted": 100.0,
                },
            ]
        )

    def test_empty_store(self, store):
        assert store.read_ticker("MU").empty
        assert store.get_all_tickers() == []
        assert store.get_latest_period_end("MU") is None

    def test_write_and_read(self, store):
        n = store.write_financials("MU", self._rows())
        assert n == 2
        df = store.read_ticker("MU")
        assert len(df) == 2
        assert df["ticker"].iloc[0] == "MU"
        assert store.get_all_tickers() == ["MU"]

    def test_derived_margins_and_fcf(self, store):
        store.write_financials("MU", self._rows())
        df = store.read_ticker("MU").sort_values("period_end")
        latest = df.iloc[-1]
        # 400 / 1000 * 100
        assert latest["gross_margin"] == pytest.approx(40.0)
        assert latest["operating_margin"] == pytest.approx(20.0)
        assert latest["net_margin"] == pytest.approx(15.0)
        # FCF = OCF + capex = 250 + (-50)
        assert latest["free_cash_flow"] == pytest.approx(200.0)

    def test_point_in_time_filter(self, store):
        store.write_financials("MU", self._rows())
        # As of just after Q3 filing, the Q4 row (filed 2026-01-28) is hidden.
        df = store.read_ticker("MU", as_of=date(2025, 11, 1))
        assert len(df) == 1
        assert df["period_end"].iloc[0] == date(2025, 9, 30)

    def test_dedup_on_restatement(self, store):
        store.write_financials("MU", self._rows())
        restated = self._rows().iloc[[0]].copy()
        restated["revenue"] = 1100.0
        store.write_financials("MU", restated)
        df = store.read_ticker("MU")
        assert len(df) == 2  # still 2 periods
        q4 = df[df["period_end"] == date(2025, 12, 31)].iloc[0]
        assert q4["revenue"] == pytest.approx(1100.0)  # latest write wins

    def test_latest_period_end(self, store):
        store.write_financials("MU", self._rows())
        assert store.get_latest_period_end("MU") == date(2025, 12, 31)

    def test_empty_write_noop(self, store):
        assert store.write_financials("MU", pd.DataFrame()) == 0


# ── EstimatesStore ─────────────────────────────────────────────────────


class TestEstimatesStore:
    @pytest.fixture
    def store(self, tmp_path):
        return EstimatesStore(data_dir=str(tmp_path))

    def _rows(self, snap: str, eps_avg: float) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {"snapshot_date": snap, "metric": "eps_est_avg", "period": "2026-12-31", "value": eps_avg},
                {"snapshot_date": snap, "metric": "rec_strong_buy", "period": "", "value": 12.0},
                {"snapshot_date": snap, "metric": "eps_surprise_pct", "period": "2025-12-31", "value": 8.5},
            ]
        )

    def test_empty_store(self, store):
        assert store.read_ticker("MU").empty
        assert store.latest_values("MU") == {}
        assert store.get_all_tickers() == []

    def test_write_and_read(self, store):
        store.write_records("MU", self._rows("2026-05-01", 5.0))
        df = store.read_ticker("MU")
        assert len(df) == 3
        assert set(df["metric"]) == {"eps_est_avg", "rec_strong_buy", "eps_surprise_pct"}

    def test_revision_time_series(self, store):
        store.write_records("MU", self._rows("2026-05-01", 5.0))
        store.write_records("MU", self._rows("2026-05-15", 5.6))
        eps = store.read_ticker("MU", metric="eps_est_avg").sort_values("snapshot_date")
        assert len(eps) == 2
        # upward revision captured across snapshots
        assert eps["value"].iloc[-1] > eps["value"].iloc[0]

    def test_dedup_same_snapshot_metric_period(self, store):
        store.write_records("MU", self._rows("2026-05-01", 5.0))
        store.write_records("MU", self._rows("2026-05-01", 5.9))  # same snapshot, new value
        eps = store.read_ticker("MU", metric="eps_est_avg")
        assert len(eps) == 1
        assert eps["value"].iloc[0] == pytest.approx(5.9)

    def test_latest_values(self, store):
        store.write_records("MU", self._rows("2026-05-01", 5.0))
        store.write_records("MU", self._rows("2026-05-15", 5.6))
        latest = store.latest_values("MU")
        assert latest["eps_est_avg"] == pytest.approx(5.6)
        assert latest["rec_strong_buy"] == pytest.approx(12.0)

    def test_point_in_time_filter(self, store):
        store.write_records("MU", self._rows("2026-05-01", 5.0))
        store.write_records("MU", self._rows("2026-05-15", 5.6))
        latest = store.latest_values("MU", as_of=date(2026, 5, 10))
        assert latest["eps_est_avg"] == pytest.approx(5.0)

    def test_drops_nan_values(self, store):
        df = pd.DataFrame(
            [{"snapshot_date": "2026-05-01", "metric": "eps_est_avg", "period": "x", "value": None}]
        )
        assert store.write_records("MU", df) == 0


# ── ShortInterestStore ─────────────────────────────────────────────────


class TestShortInterestStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ShortInterestStore(data_dir=str(tmp_path))

    def _rows(self) -> pd.DataFrame:
        return pd.DataFrame(
            [
                {
                    "settlement_date": "2026-05-15",
                    "short_interest": 10_000_000.0,
                    "avg_daily_volume": 2_000_000.0,
                },
                {
                    "settlement_date": "2026-04-30",
                    "short_interest": 8_000_000.0,
                    "avg_daily_volume": 2_000_000.0,
                },
            ]
        )

    def test_empty_store(self, store):
        assert store.read_ticker("MU").empty
        assert store.latest("MU") is None
        assert store.get_all_tickers() == []

    def test_write_and_read(self, store):
        n = store.write_records("MU", self._rows())
        assert n == 2
        df = store.read_ticker("MU")
        assert len(df) == 2

    def test_derived_days_to_cover(self, store):
        store.write_records("MU", self._rows())
        latest = store.latest("MU")
        # 10M short / 2M ADV = 5 days to cover
        assert latest["days_to_cover"] == pytest.approx(5.0)
        assert latest["short_interest_ratio"] == pytest.approx(5.0)

    def test_point_in_time_filter(self, store):
        store.write_records("MU", self._rows())
        latest = store.latest("MU", as_of=date(2026, 5, 1))
        assert latest["settlement_date"] == date(2026, 4, 30)

    def test_dedup_on_settlement_date(self, store):
        store.write_records("MU", self._rows())
        updated = self._rows().iloc[[0]].copy()
        updated["short_interest"] = 11_000_000.0
        store.write_records("MU", updated)
        df = store.read_ticker("MU")
        assert len(df) == 2
        latest = store.latest("MU")
        assert latest["short_interest"] == pytest.approx(11_000_000.0)
