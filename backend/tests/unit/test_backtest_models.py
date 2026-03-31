"""Tests for backtest SQLAlchemy models."""

from datetime import date, datetime, timezone

import pytest

from tyche.models.backtest import (
    ExitSignal,
    PullbackEvent,
    StockPosition,
    TickerPullbackProfile,
)


class TestPullbackEvent:
    def test_instantiation_with_all_fields(self):
        event = PullbackEvent(
            id="test-id",
            ticker="AAPL",
            pullback_type="8ema",
            entry_date=date(2024, 1, 15),
            entry_price=185.50,
            peak_date=date(2024, 1, 22),
            peak_price=192.30,
            peak_gain_pct=3.6656,
            exit_date=date(2024, 1, 25),
            exit_price=188.40,
            exit_gain_pct=1.5634,
            days_to_peak=5,
            days_to_exit=8,
            max_drawdown_pct=-1.24,
            volume_declining_at_entry=1,
        )
        assert event.ticker == "AAPL"
        assert event.pullback_type == "8ema"
        assert event.peak_gain_pct == 3.6656
        assert event.volume_declining_at_entry == 1

    def test_to_dict(self):
        event = PullbackEvent(
            id="test-id",
            ticker="PL",
            pullback_type="21ema",
            entry_date=date(2024, 3, 1),
            entry_price=4.50,
            peak_date=date(2024, 3, 10),
            peak_price=5.20,
            peak_gain_pct=15.5556,
            exit_date=date(2024, 3, 15),
            exit_price=4.80,
            exit_gain_pct=6.6667,
            days_to_peak=7,
            days_to_exit=10,
            max_drawdown_pct=-2.22,
            volume_declining_at_entry=0,
        )
        d = event.to_dict()
        assert d["ticker"] == "PL"
        assert d["pullback_type"] == "21ema"
        assert d["entry_date"] == "2024-03-01"
        assert d["peak_date"] == "2024-03-10"
        assert d["exit_date"] == "2024-03-15"
        assert d["entry_price"] == 4.5
        assert d["peak_gain_pct"] == 15.5556
        assert d["days_to_peak"] == 7
        assert d["volume_declining_at_entry"] == 0

    def test_to_dict_rounds_prices(self):
        event = PullbackEvent(
            id="x",
            ticker="X",
            pullback_type="8ema",
            entry_date=date(2024, 1, 1),
            entry_price=100.12345,
            peak_date=date(2024, 1, 2),
            peak_price=105.98765,
            peak_gain_pct=5.8543211,
            exit_date=date(2024, 1, 3),
            exit_price=102.11111,
            exit_gain_pct=1.9876543,
            days_to_peak=1,
            days_to_exit=2,
            max_drawdown_pct=-0.5,
            volume_declining_at_entry=0,
        )
        d = event.to_dict()
        assert d["entry_price"] == 100.12
        assert d["peak_price"] == 105.99
        assert d["peak_gain_pct"] == 5.8543
        assert d["exit_gain_pct"] == 1.9877


class TestTickerPullbackProfile:
    def test_instantiation(self):
        now = datetime.now(timezone.utc)
        profile = TickerPullbackProfile(
            id="prof-1",
            ticker="AAPL",
            pullback_type="8ema",
            event_count=25,
            median_peak_gain_pct=5.23,
            mean_peak_gain_pct=6.10,
            p25_peak_gain_pct=2.80,
            p75_peak_gain_pct=8.45,
            median_exit_gain_pct=3.12,
            win_rate_5pct=0.64,
            win_rate_10pct=0.28,
            median_days_to_peak=6,
            median_days_to_exit=12,
            avg_max_drawdown_pct=-1.5,
            last_computed=now,
        )
        assert profile.ticker == "AAPL"
        assert profile.event_count == 25
        assert profile.win_rate_5pct == 0.64

    def test_to_dict(self):
        now = datetime(2024, 6, 15, 12, 0, 0, tzinfo=timezone.utc)
        profile = TickerPullbackProfile(
            id="prof-2",
            ticker="MSFT",
            pullback_type="21ema",
            event_count=10,
            median_peak_gain_pct=8.1234,
            mean_peak_gain_pct=9.5678,
            p25_peak_gain_pct=4.1111,
            p75_peak_gain_pct=12.9999,
            median_exit_gain_pct=5.5555,
            win_rate_5pct=0.7000,
            win_rate_10pct=0.4000,
            median_days_to_peak=8,
            median_days_to_exit=15,
            avg_max_drawdown_pct=-2.3456,
            last_computed=now,
        )
        d = profile.to_dict()
        assert d["ticker"] == "MSFT"
        assert d["pullback_type"] == "21ema"
        assert d["event_count"] == 10
        assert d["median_peak_gain_pct"] == 8.1234
        assert d["p75_peak_gain_pct"] == 12.9999
        assert d["win_rate_5pct"] == 0.7
        assert d["median_days_to_peak"] == 8
        assert d["last_computed"] == "2024-06-15T12:00:00+00:00"

    def test_default_values_on_direct_instantiation(self):
        """SQLAlchemy `default=` only applies on DB INSERT, not direct __init__.

        On direct instantiation without explicit values, fields are None.
        """
        now = datetime.now(timezone.utc)
        profile = TickerPullbackProfile(
            id="p",
            ticker="X",
            pullback_type="8ema",
            event_count=1,
            last_computed=now,
        )
        assert profile.median_peak_gain_pct is None
        assert profile.win_rate_5pct is None


class TestStockPosition:
    def test_instantiation(self):
        now = datetime.now(timezone.utc)
        pos = StockPosition(
            id="pos-1",
            ticker="AAPL",
            quantity=10,
            purchase_date=date(2026, 3, 20),
            purchase_price=185.50,
            pullback_type="8ema",
            target_exit_pct=7.5,
            target_exit_price=199.41,
            stop_loss_price=183.20,
            current_price=188.00,
            current_gain_pct=1.35,
            status="active",
            created_at=now,
            updated_at=now,
        )
        assert pos.ticker == "AAPL"
        assert pos.quantity == 10
        assert pos.status == "active"
        assert pos.target_exit_pct == 7.5

    def test_to_dict(self):
        now = datetime(2026, 3, 25, 10, 0, 0, tzinfo=timezone.utc)
        pos = StockPosition(
            id="pos-2",
            ticker="PL",
            quantity=100,
            purchase_date=date(2026, 3, 15),
            purchase_price=4.50,
            pullback_type="21ema",
            target_exit_pct=12.5,
            target_exit_price=5.06,
            stop_loss_price=4.35,
            current_price=4.60,
            current_gain_pct=2.22,
            status="active",
            created_at=now,
            updated_at=now,
        )
        d = pos.to_dict()
        assert d["ticker"] == "PL"
        assert d["purchase_date"] == "2026-03-15"
        assert d["purchase_price"] == 4.50
        assert d["target_exit_price"] == 5.06
        assert d["stop_loss_price"] == 4.35
        assert d["current_gain_pct"] == 2.22
        assert d["status"] == "active"

    def test_to_dict_with_none_optionals(self):
        now = datetime.now(timezone.utc)
        pos = StockPosition(
            id="pos-3",
            ticker="MSFT",
            quantity=5,
            purchase_date=date(2026, 3, 1),
            purchase_price=400.00,
            pullback_type="manual",
            status="active",
            created_at=now,
            updated_at=now,
        )
        d = pos.to_dict()
        assert d["target_exit_pct"] is None
        assert d["target_exit_price"] is None
        assert d["stop_loss_price"] is None
        assert d["exit_date"] is None

    def test_exited_position(self):
        now = datetime.now(timezone.utc)
        pos = StockPosition(
            id="pos-4",
            ticker="GOOGL",
            quantity=20,
            purchase_date=date(2026, 2, 1),
            purchase_price=150.00,
            pullback_type="8ema",
            target_exit_pct=8.0,
            target_exit_price=162.00,
            current_price=163.50,
            current_gain_pct=9.0,
            status="profit_target_hit",
            exit_date=date(2026, 3, 10),
            exit_price=163.50,
            exit_reason="profit_target_hit",
            created_at=now,
            updated_at=now,
        )
        d = pos.to_dict()
        assert d["status"] == "profit_target_hit"
        assert d["exit_date"] == "2026-03-10"
        assert d["exit_price"] == 163.50
        assert d["exit_reason"] == "profit_target_hit"


class TestExitSignal:
    def test_instantiation(self):
        now = datetime.now(timezone.utc)
        signal = ExitSignal(
            id="sig-1",
            position_id="pos-1",
            ticker="AAPL",
            signal_type="profit_target",
            trigger_price=199.41,
            current_price=200.50,
            gain_pct=8.09,
            triggered_at=now,
        )
        assert signal.ticker == "AAPL"
        assert signal.signal_type == "profit_target"
        assert signal.gain_pct == 8.09

    def test_to_dict(self):
        now = datetime(2026, 3, 25, 16, 5, 0, tzinfo=timezone.utc)
        signal = ExitSignal(
            id="sig-2",
            position_id="pos-2",
            ticker="PL",
            signal_type="stop_loss",
            trigger_price=4.35,
            current_price=4.20,
            gain_pct=-6.67,
            triggered_at=now,
        )
        d = signal.to_dict()
        assert d["ticker"] == "PL"
        assert d["signal_type"] == "stop_loss"
        assert d["trigger_price"] == 4.35
        assert d["current_price"] == 4.20
        assert d["gain_pct"] == -6.67
        assert d["triggered_at"] == "2026-03-25T16:05:00+00:00"
