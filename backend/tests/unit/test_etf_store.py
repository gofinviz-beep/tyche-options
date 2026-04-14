"""Tests for ETF constituent store and static constituent lists."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tyche.market_data.etf_constituents import (
    CURATED_ETFS,
    get_all_static_constituents,
    get_static_constituents,
    get_stock_etf_memberships,
)
from tyche.market_data.etf_store import (
    ETFConstituentStore,
    build_etf_data,
)


class TestStaticConstituents:
    def test_curated_etfs_not_empty(self):
        assert len(CURATED_ETFS) >= 8

    def test_spy_has_expected_tickers(self):
        members = get_static_constituents("SPY")
        assert len(members) >= 50
        for ticker in ["NVDA", "AAPL", "MSFT", "AMZN", "GOOGL"]:
            assert ticker in members

    def test_qqq_has_expected_tickers(self):
        members = get_static_constituents("QQQ")
        assert len(members) >= 50
        assert "NVDA" in members
        assert "AAPL" in members

    def test_dia_has_30_tickers(self):
        members = get_static_constituents("DIA")
        assert len(members) == 30

    def test_sector_etfs_have_members(self):
        for etf in ["XLK", "XLF", "XLE", "XLV", "SMH", "SOXX", "XLI"]:
            members = get_static_constituents(etf)
            assert len(members) >= 10, f"{etf} should have >= 10 members"

    def test_unknown_etf_returns_empty(self):
        assert get_static_constituents("NOTREAL") == []

    def test_get_all_returns_all_etfs(self):
        all_data = get_all_static_constituents()
        for etf in CURATED_ETFS:
            assert etf in all_data

    def test_returns_copies(self):
        a = get_static_constituents("SPY")
        b = get_static_constituents("SPY")
        a.append("ZZZZZ")
        assert "ZZZZZ" not in b

    def test_membership_lookup(self):
        etfs = get_stock_etf_memberships("NVDA")
        assert "SPY" in etfs
        assert "QQQ" in etfs
        assert "XLK" in etfs
        assert "SMH" in etfs

    def test_membership_unknown_ticker(self):
        assert get_stock_etf_memberships("ZZZZZZ") == []


class TestETFConstituentStore:
    @pytest.fixture
    def store(self, tmp_path):
        return ETFConstituentStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert not store.exists
        assert store.read_all().empty
        assert store.get_constituents("SPY") == []
        assert store.get_etf_memberships("AAPL") == []
        assert store.get_etf_weights("SPY") == {}
        assert store.get_membership_counts() == {}
        assert store.get_membership_matrix() == {}

    def test_write_and_read_single(self, store):
        constituents = [
            {"ticker": "AAPL", "weight": 0.065},
            {"ticker": "MSFT", "weight": 0.050},
            {"ticker": "NVDA", "weight": 0.075},
        ]
        count = store.write_constituents("SPY", constituents, as_of=date(2026, 4, 1))
        assert count == 3
        assert store.exists

        members = store.get_constituents("SPY")
        assert sorted(members) == ["AAPL", "MSFT", "NVDA"]

    def test_write_and_read_weights(self, store):
        constituents = [
            {"ticker": "AAPL", "weight": 0.065},
            {"ticker": "MSFT", "weight": 0.050},
        ]
        store.write_constituents("SPY", constituents)
        weights = store.get_etf_weights("SPY")
        assert weights["AAPL"] == pytest.approx(0.065)
        assert weights["MSFT"] == pytest.approx(0.050)

    def test_upsert_replaces_etf(self, store):
        store.write_constituents("SPY", [{"ticker": "AAPL"}])
        store.write_constituents("SPY", [{"ticker": "MSFT"}])
        members = store.get_constituents("SPY")
        assert members == ["MSFT"]

    def test_upsert_preserves_other_etfs(self, store):
        store.write_constituents("SPY", [{"ticker": "AAPL"}])
        store.write_constituents("QQQ", [{"ticker": "NVDA"}])
        assert store.get_constituents("SPY") == ["AAPL"]
        assert store.get_constituents("QQQ") == ["NVDA"]

    def test_write_all(self, store):
        data = {
            "SPY": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "QQQ": [{"ticker": "NVDA"}, {"ticker": "AAPL"}],
        }
        total = store.write_all(data)
        assert total == 4
        assert sorted(store.get_constituents("SPY")) == ["AAPL", "MSFT"]
        assert sorted(store.get_constituents("QQQ")) == ["AAPL", "NVDA"]

    def test_membership_counts(self, store):
        data = {
            "SPY": [{"ticker": "AAPL"}, {"ticker": "MSFT"}],
            "QQQ": [{"ticker": "NVDA"}, {"ticker": "AAPL"}],
        }
        store.write_all(data)
        counts = store.get_membership_counts()
        assert counts["AAPL"] == 2
        assert counts["MSFT"] == 1
        assert counts["NVDA"] == 1

    def test_membership_matrix(self, store):
        data = {
            "SPY": [{"ticker": "AAPL"}],
            "QQQ": [{"ticker": "AAPL"}],
            "XLK": [{"ticker": "AAPL"}, {"ticker": "NVDA"}],
        }
        store.write_all(data)
        matrix = store.get_membership_matrix()
        assert sorted(matrix["AAPL"]) == ["QQQ", "SPY", "XLK"]
        assert matrix["NVDA"] == ["XLK"]

    def test_get_etf_memberships(self, store):
        data = {
            "SPY": [{"ticker": "AAPL"}],
            "QQQ": [{"ticker": "AAPL"}],
        }
        store.write_all(data)
        etfs = store.get_etf_memberships("AAPL")
        assert sorted(etfs) == ["QQQ", "SPY"]

    def test_write_all_empty(self, store):
        assert store.write_all({}) == 0


class TestBuildETFData:
    def test_builds_from_static_no_yfinance(self):
        data = build_etf_data(etf_tickers=["DIA"], use_yfinance=False)
        assert "DIA" in data
        assert len(data["DIA"]) == 30
        for item in data["DIA"]:
            assert "ticker" in item
            assert item["weight"] is None

    def test_builds_multiple_etfs(self):
        data = build_etf_data(etf_tickers=["DIA", "XLE"], use_yfinance=False)
        assert "DIA" in data
        assert "XLE" in data
