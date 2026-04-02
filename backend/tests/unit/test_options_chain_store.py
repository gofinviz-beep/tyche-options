"""Tests for OptionsChainStore and MarketPremiumModel."""

from __future__ import annotations

import shutil
from datetime import date, timedelta
from pathlib import Path

import pandas as pd
import pytest

from tyche.backtest.premium import (
    FixedPctPremiumModel,
    MarketPremiumModel,
    get_market_premium_model,
    get_premium_model,
)
from tyche.market_data.data_store import OptionsChainStore


@pytest.fixture
def tmp_store(tmp_path: Path) -> OptionsChainStore:
    return OptionsChainStore(data_dir=str(tmp_path))


def _make_contracts(
    expiration: date,
    strikes: list[float],
    option_type: str = "put",
    bid_base: float = 2.0,
    spread: float = 0.20,
) -> list[dict]:
    """Generate synthetic option contracts for testing."""
    contracts = []
    for strike in strikes:
        bid = round(bid_base + (200 - strike) * 0.05, 2)
        ask = round(bid + spread, 2)
        contracts.append({
            "expiration": expiration,
            "strike": strike,
            "option_type": option_type,
            "bid": bid,
            "ask": ask,
            "mid": round((bid + ask) / 2, 2),
            "last": round((bid + ask) / 2, 2),
            "volume": 100,
            "open_interest": 500,
            "implied_volatility": 0.30,
            "delta": -0.25,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.10,
            "rho": -0.01,
        })
    return contracts


class TestOptionsChainStoreBasics:
    def test_empty_store(self, tmp_store: OptionsChainStore) -> None:
        assert not tmp_store.exists
        assert tmp_store.get_ticker_count() == 0
        assert tmp_store.list_tickers() == []
        assert tmp_store.list_snapshot_dates() == []

    def test_write_and_read(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [190.0, 195.0, 200.0])

        rows = tmp_store.write_chains("AAPL", snap, contracts, 200.0)
        assert rows == 3

        assert tmp_store.exists
        assert tmp_store.get_ticker_count() == 1
        assert tmp_store.list_tickers() == ["AAPL"]
        assert tmp_store.list_snapshot_dates("AAPL") == [snap]

        df = tmp_store.read_ticker("AAPL")
        assert len(df) == 3
        assert set(df["option_type"]) == {"put"}
        assert df["underlying_price"].iloc[0] == 200.0

    def test_write_empty_contracts(self, tmp_store: OptionsChainStore) -> None:
        rows = tmp_store.write_chains("AAPL", date.today(), [], 200.0)
        assert rows == 0
        assert not tmp_store.exists

    def test_deduplication(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [190.0, 195.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)
        rows = tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        assert rows == 0  # no new rows (all duplicates)
        df = tmp_store.read_ticker("AAPL")
        assert len(df) == 2

    def test_multiple_snapshots(self, tmp_store: OptionsChainStore) -> None:
        exp = date(2026, 4, 10)
        day1 = date(2026, 4, 1)
        day2 = date(2026, 4, 2)

        c1 = _make_contracts(exp, [190.0, 195.0])
        c2 = _make_contracts(exp, [190.0, 195.0], bid_base=2.5)

        tmp_store.write_chains("AAPL", day1, c1, 200.0)
        tmp_store.write_chains("AAPL", day2, c2, 201.0)

        dates = tmp_store.list_snapshot_dates("AAPL")
        assert dates == [day1, day2]

        df = tmp_store.read_ticker("AAPL")
        assert len(df) == 4  # 2 strikes x 2 snapshot dates

    def test_multiple_tickers(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [190.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)
        tmp_store.write_chains("MSFT", snap, contracts, 400.0)

        assert tmp_store.get_ticker_count() == 2
        assert tmp_store.list_tickers() == ["AAPL", "MSFT"]

    def test_read_nonexistent_ticker(self, tmp_store: OptionsChainStore) -> None:
        df = tmp_store.read_ticker("NOPE")
        assert df.empty


class TestOptionsChainStoreFilters:
    def test_filter_by_snapshot_date(self, tmp_store: OptionsChainStore) -> None:
        exp = date(2026, 4, 10)
        day1 = date(2026, 4, 1)
        day2 = date(2026, 4, 2)

        tmp_store.write_chains("AAPL", day1, _make_contracts(exp, [190.0]), 200.0)
        tmp_store.write_chains("AAPL", day2, _make_contracts(exp, [190.0]), 201.0)

        df = tmp_store.read_ticker("AAPL", snapshot_date=day1)
        assert len(df) == 1
        assert df["snapshot_date"].iloc[0] == day1

    def test_filter_by_option_type(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        puts = _make_contracts(exp, [190.0], option_type="put")
        calls = _make_contracts(exp, [190.0], option_type="call")

        tmp_store.write_chains("AAPL", snap, puts + calls, 200.0)

        puts_df = tmp_store.read_ticker("AAPL", option_type="put")
        assert len(puts_df) == 1
        assert puts_df["option_type"].iloc[0] == "put"

        calls_df = tmp_store.read_ticker("AAPL", option_type="call")
        assert len(calls_df) == 1


class TestNearestSnapshotDate:
    def test_exact_match(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        tmp_store.write_chains("AAPL", snap, _make_contracts(date(2026, 4, 10), [190.0]), 200.0)

        result = tmp_store.get_nearest_snapshot_date("AAPL", snap)
        assert result == snap

    def test_nearest_before(self, tmp_store: OptionsChainStore) -> None:
        d1 = date(2026, 3, 28)
        d2 = date(2026, 4, 5)
        exp = date(2026, 4, 10)

        tmp_store.write_chains("AAPL", d1, _make_contracts(exp, [190.0]), 200.0)
        tmp_store.write_chains("AAPL", d2, _make_contracts(exp, [190.0]), 200.0)

        result = tmp_store.get_nearest_snapshot_date("AAPL", date(2026, 3, 30))
        assert result == d1  # 2 days away vs 6 days

    def test_nonexistent_ticker(self, tmp_store: OptionsChainStore) -> None:
        result = tmp_store.get_nearest_snapshot_date("NOPE", date.today())
        assert result is None


class TestGetPutPremium:
    def test_exact_strike_match(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [185.0, 190.0, 195.0, 200.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        result = tmp_store.get_put_premium("AAPL", snap, target_strike=190.0)
        assert result is not None
        assert result["strike"] == 190.0
        assert result["bid"] > 0
        assert result["ask"] > result["bid"]

    def test_nearest_strike_within_tolerance(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [185.0, 190.0, 195.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        # Request 191.0 — should match 190.0 (0.5% away, within 2% tol)
        result = tmp_store.get_put_premium("AAPL", snap, target_strike=191.0)
        assert result is not None
        assert result["strike"] == 190.0

    def test_no_match_outside_tolerance(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [170.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        result = tmp_store.get_put_premium("AAPL", snap, target_strike=190.0, tolerance_pct=2.0)
        assert result is None

    def test_nearest_expiration(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp1 = date(2026, 4, 10)
        exp2 = date(2026, 4, 17)

        c1 = _make_contracts(exp1, [190.0], bid_base=1.5)
        c2 = _make_contracts(exp2, [190.0], bid_base=2.5)

        tmp_store.write_chains("AAPL", snap, c1 + c2, 200.0)

        result = tmp_store.get_put_premium(
            "AAPL", snap, target_strike=190.0, target_expiration=exp1
        )
        assert result is not None
        assert result["expiration"] == exp1

    def test_empty_store(self, tmp_store: OptionsChainStore) -> None:
        result = tmp_store.get_put_premium("AAPL", date.today(), target_strike=190.0)
        assert result is None


class TestMarketPremiumModel:
    def test_hit_returns_real_premium(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [185.0, 190.0, 195.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        model = MarketPremiumModel(options_store=tmp_store)

        pct = model.premium_pct(
            strike=190.0,
            underlying_price=200.0,
            dte=9,
            ticker="AAPL",
            snapshot_date=snap,
        )

        assert pct > 0
        assert model._hits == 1
        assert model._misses == 0

    def test_miss_falls_back(self, tmp_store: OptionsChainStore) -> None:
        fallback = FixedPctPremiumModel(pct=0.015)
        model = MarketPremiumModel(options_store=tmp_store, fallback=fallback)

        pct = model.premium_pct(
            strike=190.0,
            underlying_price=200.0,
            dte=9,
            ticker="NOPE",
            snapshot_date=date.today(),
        )

        assert pct == 0.015
        assert model._misses == 1
        assert model._hits == 0

    def test_no_ticker_kwarg_falls_back(self, tmp_store: OptionsChainStore) -> None:
        fallback = FixedPctPremiumModel(pct=0.020)
        model = MarketPremiumModel(options_store=tmp_store, fallback=fallback)

        pct = model.premium_pct(
            strike=190.0, underlying_price=200.0, dte=9
        )
        assert pct == 0.020

    def test_date_too_far_falls_back(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 1, 1)
        contracts = _make_contracts(date(2026, 1, 10), [190.0])
        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        fallback = FixedPctPremiumModel(pct=0.015)
        model = MarketPremiumModel(
            options_store=tmp_store, fallback=fallback, max_date_gap_days=3
        )

        pct = model.premium_pct(
            strike=190.0, underlying_price=200.0, dte=9,
            ticker="AAPL", snapshot_date=date(2026, 4, 1),
        )
        assert pct == 0.015

    def test_hit_rate(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        contracts = _make_contracts(date(2026, 4, 10), [190.0])
        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        model = MarketPremiumModel(options_store=tmp_store)

        model.premium_pct(190.0, 200.0, 9, ticker="AAPL", snapshot_date=snap)
        model.premium_pct(190.0, 200.0, 9, ticker="NOPE", snapshot_date=snap)
        model.premium_pct(190.0, 200.0, 9, ticker="AAPL", snapshot_date=snap)

        assert model.hit_rate == pytest.approx(66.67, abs=0.1)

    def test_describe(self, tmp_store: OptionsChainStore) -> None:
        model = MarketPremiumModel(options_store=tmp_store)
        desc = model.describe()
        assert desc["model"] == "market"
        assert desc["fallback"] == "iv_proxy"
        assert "hits" in desc
        assert "misses" in desc

    def test_use_mid_price(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [190.0], bid_base=2.0, spread=1.0)
        tmp_store.write_chains("AAPL", snap, contracts, 200.0)

        bid_model = MarketPremiumModel(options_store=tmp_store)
        bid_pct = bid_model.premium_pct(
            190.0, 200.0, 9, ticker="AAPL", snapshot_date=snap, use_bid=True
        )

        mid_model = MarketPremiumModel(options_store=tmp_store)
        mid_pct = mid_model.premium_pct(
            190.0, 200.0, 9, ticker="AAPL", snapshot_date=snap, use_bid=False
        )

        assert mid_pct > bid_pct


class TestGetPremiumModelFactory:
    def test_market_model_via_factory(self, tmp_store: OptionsChainStore) -> None:
        model = get_premium_model("market", options_store=tmp_store)
        assert isinstance(model, MarketPremiumModel)

    def test_market_convenience_factory(self, tmp_store: OptionsChainStore) -> None:
        model = get_market_premium_model(tmp_store, fallback_name="fixed_pct")
        assert isinstance(model, MarketPremiumModel)
        assert model._fallback.name == "fixed_pct"

    def test_unknown_model_includes_market(self) -> None:
        with pytest.raises(ValueError, match="market"):
            get_premium_model("nonexistent")


class TestOptionsChainStoreStats:
    def test_stats_empty(self, tmp_store: OptionsChainStore) -> None:
        stats = tmp_store.get_stats()
        assert stats["ticker_count"] == 0
        assert stats["total_rows"] == 0

    def test_stats_with_data(self, tmp_store: OptionsChainStore) -> None:
        snap = date(2026, 4, 1)
        exp = date(2026, 4, 10)
        contracts = _make_contracts(exp, [190.0, 195.0])

        tmp_store.write_chains("AAPL", snap, contracts, 200.0)
        tmp_store.write_chains("MSFT", snap, contracts, 400.0)

        # Force rebuild (no cached meta)
        stats = tmp_store.get_stats()
        assert stats["ticker_count"] == 2
        assert stats["total_rows"] == 4
        assert stats["snapshot_dates"] == 1

    def test_list_snapshot_dates_all_tickers(self, tmp_store: OptionsChainStore) -> None:
        d1 = date(2026, 4, 1)
        d2 = date(2026, 4, 2)
        exp = date(2026, 4, 10)

        tmp_store.write_chains("AAPL", d1, _make_contracts(exp, [190.0]), 200.0)
        tmp_store.write_chains("MSFT", d2, _make_contracts(exp, [190.0]), 400.0)

        all_dates = tmp_store.list_snapshot_dates()
        assert all_dates == [d1, d2]


class TestStringExpirationHandling:
    def test_string_expiration_in_contracts(self, tmp_store: OptionsChainStore) -> None:
        """Contracts from API may have string dates."""
        snap = date(2026, 4, 1)
        contracts = [{
            "expiration": "2026-04-10",
            "strike": 190.0,
            "option_type": "put",
            "bid": 2.0,
            "ask": 2.20,
            "mid": 2.10,
            "last": 2.10,
            "volume": 100,
            "open_interest": 500,
            "implied_volatility": 0.30,
            "delta": -0.25,
            "gamma": 0.02,
            "theta": -0.05,
            "vega": 0.10,
            "rho": -0.01,
        }]

        rows = tmp_store.write_chains("AAPL", snap, contracts, 200.0)
        assert rows == 1

        df = tmp_store.read_ticker("AAPL")
        assert df["expiration"].iloc[0] == date(2026, 4, 10)
