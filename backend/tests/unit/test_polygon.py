"""Tests for the Polygon.io client — unit tests with mocked HTTP."""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock, patch

import pytest

from tyche.market_data.polygon import (
    DailyBar,
    OptionsContract,
    PolygonClient,
    TickerInfo,
    TickerSnapshot,
)


@pytest.fixture
def client():
    return PolygonClient(
        api_key="test_key",
        base_url="https://api.polygon.io",
        rate_limit_rpm=1000,
        max_retries=1,
    )


class TestDailyBar:
    def test_frozen(self):
        bar = DailyBar(
            ticker="AAPL", date=date(2026, 1, 5),
            open=150.0, high=155.0, low=149.0, close=153.0,
            volume=50_000_000,
        )
        assert bar.ticker == "AAPL"
        assert bar.close == 153.0
        with pytest.raises(AttributeError):
            bar.ticker = "GOOG"


class TestTickerInfo:
    def test_fields(self):
        info = TickerInfo(
            ticker="NVDA", name="NVIDIA", market="stocks",
            locale="us", type="CS", active=True,
            primary_exchange="XNAS", market_cap=2_500_000_000_000,
        )
        assert info.ticker == "NVDA"
        assert info.market_cap == 2_500_000_000_000


class TestTickerSnapshot:
    def test_fields(self):
        snap = TickerSnapshot(
            ticker="PL", last_price=24.5,
            today_open=24.0, today_high=25.0, today_low=23.5,
            today_close=24.5, today_volume=5_000_000,
            prev_close=24.0, change=0.5, change_pct=2.08,
        )
        assert snap.ticker == "PL"
        assert snap.change_pct == 2.08


class TestOptionsContract:
    def test_fields(self):
        c = OptionsContract(
            ticker="O:PL260320P00023000",
            underlying_ticker="PL",
            contract_type="put",
            strike_price=23.0,
            expiration_date=date(2026, 3, 20),
            bid=1.75, ask=1.85, mid=1.80,
            last_trade_price=1.80,
            volume=500, open_interest=2000,
            implied_volatility=0.45,
            delta=-0.35,
        )
        assert c.contract_type == "put"
        assert c.strike_price == 23.0
        assert c.delta == -0.35


class TestPolygonClientInit:
    def test_default_values(self):
        c = PolygonClient(api_key="key")
        assert c._api_key == "key"
        assert c._base_url == "https://api.polygon.io"
        assert c._max_retries == 3

    def test_custom_base_url(self):
        c = PolygonClient(api_key="key", base_url="https://custom.api.com/")
        assert c._base_url == "https://custom.api.com"

    def test_rate_limit_interval(self):
        c = PolygonClient(api_key="key", rate_limit_rpm=60)
        assert c._min_interval == pytest.approx(1.0, abs=0.01)

        c2 = PolygonClient(api_key="key", rate_limit_rpm=5)
        assert c2._min_interval == pytest.approx(12.0, abs=0.01)
