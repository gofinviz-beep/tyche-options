"""Tests for the universe builder / stock screening funnel."""

from __future__ import annotations

from datetime import date

import pytest

from tyche.market_data.polygon import TickerSnapshot
from tyche.market_data.universe import UniverseBuilder


def _snap(ticker, price=50.0, volume=1_000_000, mcap=0.0):
    return TickerSnapshot(
        ticker=ticker, last_price=price,
        today_open=price, today_high=price * 1.01,
        today_low=price * 0.99, today_close=price,
        today_volume=volume, prev_close=price,
        change=0.0, change_pct=0.0, market_cap=mcap,
    )


class TestUniverseBuilder:

    @pytest.fixture
    def builder(self):
        return UniverseBuilder(
            min_market_cap_millions=500.0,
            min_avg_volume=500_000,
            min_price=5.0,
        )

    def test_passes_all_gates(self, builder):
        snaps = [_snap("AAPL", price=180.0, volume=60_000_000, mcap=3e12)]
        result = builder.screen_from_snapshots(snaps)
        assert len(result) == 1
        assert result[0].symbol == "AAPL"
        assert result[0].passes_all is True

    def test_fails_market_cap(self, builder):
        snaps = [_snap("TINY", price=10.0, volume=1_000_000, mcap=100e6)]
        result = builder.screen_from_snapshots(snaps)
        assert len(result) == 0

    def test_fails_volume(self, builder):
        snaps = [_snap("ILLIQUID", price=50.0, volume=100_000, mcap=1e9)]
        result = builder.screen_from_snapshots(snaps)
        assert len(result) == 0

    def test_fails_price(self, builder):
        snaps = [_snap("PENNY", price=2.0, volume=5_000_000, mcap=1e9)]
        result = builder.screen_from_snapshots(snaps)
        assert len(result) == 0

    def test_multiple_stocks(self, builder):
        snaps = [
            _snap("GOOD", price=100.0, volume=2_000_000, mcap=5e9),
            _snap("BAD_VOL", price=100.0, volume=100, mcap=5e9),
            _snap("BAD_CAP", price=100.0, volume=2_000_000, mcap=10e6),
            _snap("BAD_PRICE", price=1.0, volume=2_000_000, mcap=5e9),
        ]
        result = builder.screen_from_snapshots(snaps)
        assert len(result) == 1
        assert result[0].symbol == "GOOD"

    def test_watchlist_trusts_market_cap(self, builder):
        result = builder.screen_watchlist(["PL", "AAPL"])
        assert len(result) == 2
        assert all(p.passes_market_cap for p in result)

    def test_watchlist_with_snapshots(self, builder):
        snaps = {
            "PL": _snap("PL", price=24.0, volume=5_000_000),
            "PENNY": _snap("PENNY", price=1.0, volume=5_000_000),
        }
        result = builder.screen_watchlist(["PL", "PENNY"], snapshots=snaps)
        symbols = [r.symbol for r in result]
        assert "PL" in symbols
        assert "PENNY" not in symbols

    def test_earnings_dates_passed(self, builder):
        snaps = [_snap("AAPL", price=180.0, volume=60_000_000, mcap=3e12)]
        earnings = {"AAPL": date(2026, 4, 25)}
        result = builder.screen_from_snapshots(snaps, earnings_dates=earnings)
        assert result[0].next_earnings == date(2026, 4, 25)
