"""Tests for backtest API endpoints and position routes in stocks routes."""

from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from tyche.app import create_app
from tyche.models.backtest import (
    ExitSignal,
    PullbackEvent,
    StockPosition,
    TickerPullbackProfile,
)
from tyche.schemas.alerts import (
    ExitCheckResponse,
    ExitSignalResponse,
    HistoricalBounceStats,
    StockPositionRequest,
    StockPositionResponse,
)


def _make_profile(
    ticker: str = "AAPL",
    pullback_type: str = "8ema",
    event_count: int = 20,
    median_peak: float = 5.5,
    p75_peak: float = 9.2,
) -> TickerPullbackProfile:
    return TickerPullbackProfile(
        id="test-profile",
        ticker=ticker,
        pullback_type=pullback_type,
        event_count=event_count,
        median_peak_gain_pct=median_peak,
        mean_peak_gain_pct=6.0,
        p25_peak_gain_pct=3.0,
        p75_peak_gain_pct=p75_peak,
        median_exit_gain_pct=3.5,
        win_rate_5pct=0.65,
        win_rate_10pct=0.30,
        median_days_to_peak=7,
        median_days_to_exit=14,
        avg_max_drawdown_pct=-1.8,
        last_computed=datetime(2024, 6, 1, tzinfo=timezone.utc),
    )


def _make_event(ticker: str = "AAPL") -> PullbackEvent:
    from datetime import date

    return PullbackEvent(
        id="test-event",
        ticker=ticker,
        pullback_type="8ema",
        entry_date=date(2024, 3, 1),
        entry_price=175.50,
        peak_date=date(2024, 3, 8),
        peak_price=184.30,
        peak_gain_pct=5.01,
        exit_date=date(2024, 3, 12),
        exit_price=180.20,
        exit_gain_pct=2.68,
        days_to_peak=5,
        days_to_exit=8,
        max_drawdown_pct=-0.9,
        volume_declining_at_entry=1,
    )


class TestHistoricalBounceStats:
    def test_schema(self):
        stats = HistoricalBounceStats(
            pullback_type="8ema",
            event_count=20,
            median_peak_gain_pct=5.5,
            mean_peak_gain_pct=6.0,
            p25_peak_gain_pct=3.0,
            p75_peak_gain_pct=9.2,
            median_exit_gain_pct=3.5,
            win_rate_5pct=0.65,
            win_rate_10pct=0.30,
            median_days_to_peak=7,
            median_days_to_exit=14,
            avg_max_drawdown_pct=-1.8,
            suggested_exit_pct=9.2,
        )
        assert stats.pullback_type == "8ema"
        assert stats.suggested_exit_pct == 9.2
        assert stats.win_rate_5pct == 0.65


class TestProfileToDict:
    def test_profile_to_dict(self):
        profile = _make_profile()
        d = profile.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["event_count"] == 20
        assert d["median_peak_gain_pct"] == 5.5
        assert d["p75_peak_gain_pct"] == 9.2

    def test_event_to_dict(self):
        event = _make_event()
        d = event.to_dict()
        assert d["ticker"] == "AAPL"
        assert d["pullback_type"] == "8ema"
        assert d["entry_price"] == 175.5
        assert d["peak_gain_pct"] == 5.01


class TestProfileToBounceSatsConversion:
    def test_conversion(self):
        from tyche.api.routes.stocks import _profile_to_bounce_stats

        profile = _make_profile(p75_peak=11.33)
        stats = _profile_to_bounce_stats(profile)
        assert stats.pullback_type == "8ema"
        assert stats.event_count == 20
        assert stats.suggested_exit_pct == 11.33
        assert stats.win_rate_5pct == 0.65


def _make_position(
    ticker: str = "AAPL",
    purchase_price: float = 180.0,
    target_exit_price: float | None = 194.0,
    status: str = "active",
) -> StockPosition:
    now = datetime.now(timezone.utc)
    return StockPosition(
        id="pos-test-1",
        ticker=ticker,
        quantity=10,
        purchase_date=date(2026, 3, 20),
        purchase_price=purchase_price,
        pullback_type="8ema",
        target_exit_pct=7.78 if target_exit_price else None,
        target_exit_price=target_exit_price,
        stop_loss_price=178.0,
        current_price=purchase_price,
        current_gain_pct=0.0,
        status=status,
        created_at=now,
        updated_at=now,
    )


class TestStockPositionSchemas:
    def test_request_schema(self):
        req = StockPositionRequest(
            ticker="AAPL",
            purchase_price=185.50,
            quantity=10,
            purchase_date="2026-03-20",
            pullback_type="8ema",
        )
        assert req.ticker == "AAPL"
        assert req.purchase_price == 185.50

    def test_response_schema(self):
        resp = StockPositionResponse(
            id="pos-1",
            ticker="AAPL",
            quantity=10,
            purchase_date="2026-03-20",
            purchase_price=185.50,
            pullback_type="8ema",
            target_exit_pct=7.5,
            target_exit_price=199.41,
            stop_loss_price=183.20,
            current_price=188.00,
            current_gain_pct=1.35,
            status="active",
        )
        assert resp.status == "active"
        assert resp.target_exit_price == 199.41

    def test_response_schema_optional_none(self):
        resp = StockPositionResponse(
            id="pos-2",
            ticker="PL",
            quantity=100,
            purchase_date="2026-03-15",
            purchase_price=4.50,
            pullback_type="manual",
            status="active",
        )
        assert resp.target_exit_pct is None
        assert resp.exit_date is None

    def test_exit_signal_response(self):
        resp = ExitSignalResponse(
            id="sig-1",
            position_id="pos-1",
            ticker="AAPL",
            signal_type="profit_target",
            trigger_price=194.0,
            current_price=195.0,
            gain_pct=8.33,
            triggered_at="2026-03-25T16:05:00+00:00",
        )
        assert resp.signal_type == "profit_target"
        assert resp.gain_pct == 8.33

    def test_exit_check_response(self):
        resp = ExitCheckResponse(
            positions_checked=5,
            prices_updated=5,
            profit_targets_hit=1,
            stop_losses_hit=2,
            errors=0,
            signals=[
                ExitSignalResponse(
                    id="s1",
                    position_id="p1",
                    ticker="AAPL",
                    signal_type="profit_target",
                    trigger_price=194.0,
                    current_price=195.0,
                    gain_pct=8.33,
                ),
            ],
        )
        assert resp.positions_checked == 5
        assert len(resp.signals) == 1


class TestPositionToDict:
    def test_position_roundtrip(self):
        pos = _make_position()
        d = pos.to_dict()
        resp = StockPositionResponse(**d)
        assert resp.ticker == "AAPL"
        assert resp.purchase_price == 180.0
        assert resp.status == "active"
