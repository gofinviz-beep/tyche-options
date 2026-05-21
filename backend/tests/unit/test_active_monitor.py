"""Tests for ActiveMonitor — short_put and short_call alert logic."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.workflow.active_monitor import (
    ActiveMonitor,
    AlertSeverity,
    TrackedPosition,
    TrendDirection,
)


def _make_quote(last: float, bid: float = 0, ask: float = 0, volume: int = 1000):
    q = MagicMock()
    q.last = last
    q.bid = bid
    q.ask = ask
    q.volume = volume
    return q


def _make_contract(
    option_symbol: str,
    strike: float,
    option_type: str,
    bid: float,
    ask: float,
    delta: float = -0.25,
    theta: float = 0.05,
):
    c = MagicMock()
    c.option_symbol = option_symbol
    c.strike = strike
    c.option_type = option_type
    c.bid = bid
    c.ask = ask
    c.mid = (bid + ask) / 2
    c.delta = delta
    c.theta = theta
    return c


def _make_chain(*contracts):
    chain = MagicMock()
    chain.contracts = list(contracts)
    chain.puts = [c for c in contracts if c.option_type == "put"]
    chain.calls = [c for c in contracts if c.option_type == "call"]
    return chain


def _tracked_position(
    position_type: str = "short_put",
    strike: float = 100.0,
    entry_price: float = 2.0,
) -> TrackedPosition:
    return TrackedPosition(
        symbol="TEST",
        option_symbol=f"TEST260501{'P' if 'put' in position_type else 'C'}{int(strike * 100):08d}",
        position_type=position_type,
        strike=strike,
        expiration=date.today() + timedelta(days=10),
        entry_price=entry_price,
        contracts=5,
        entry_date=date.today(),
        underlying_at_entry=105.0,
    )


class TestDistanceToStrike:
    """Distance % is directionally correct for puts and calls."""

    @pytest.mark.asyncio
    async def test_short_put_distance_positive_when_above_strike(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(110.0))
        contract = _make_contract("TEST260501P01000000", 100.0, "put", 0.50, 0.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_put", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        assert status is not None
        assert status.distance_to_strike_pct > 0

    @pytest.mark.asyncio
    async def test_short_call_distance_positive_when_below_strike(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(95.0))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 0.50, 0.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        assert status is not None
        assert status.distance_to_strike_pct > 0

    @pytest.mark.asyncio
    async def test_short_call_distance_negative_when_itm(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(105.0))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 5.50, 5.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        assert status is not None
        assert status.distance_to_strike_pct < 0


class TestContractNotFoundFallback:
    """When contract is missing, distance calculation is still direction-aware."""

    @pytest.mark.asyncio
    async def test_put_fallback_distance(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(110.0))
        broker.get_options_chain = AsyncMock(return_value=_make_chain())

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_put", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        assert status is not None
        assert status.distance_to_strike_pct == pytest.approx(10.0)

    @pytest.mark.asyncio
    async def test_call_fallback_distance(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(95.0))
        broker.get_options_chain = AsyncMock(return_value=_make_chain())

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        assert status is not None
        assert status.distance_to_strike_pct == pytest.approx(5.0)


class TestApproachingStrikeAlert:
    """approaching_strike fires for both puts (dropping) and calls (rising)."""

    @pytest.mark.asyncio
    async def test_short_put_approaching_strike(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(100.5))
        contract = _make_contract("TEST260501P01000000", 100.0, "put", 2.50, 2.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_put", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "approaching_strike"]
        assert len(alerts) == 1
        assert any("roll_down" in a.action for a in alerts[0].suggested_actions)

    @pytest.mark.asyncio
    async def test_short_call_approaching_strike(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(99.5))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 2.50, 2.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "approaching_strike"]
        assert len(alerts) == 1
        assert any("roll_up" in a.action for a in alerts[0].suggested_actions)

    @pytest.mark.asyncio
    async def test_short_call_no_alert_when_far_otm(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(90.0))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 0.10, 0.20)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "approaching_strike"]
        assert len(alerts) == 0


class TestAdverseTrendAlert:
    """adverse_trend fires for puts (down) and calls (up)."""

    @pytest.mark.asyncio
    async def test_short_put_adverse_trend_down(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(110.0))
        contract = _make_contract("TEST260501P01000000", 100.0, "put", 0.50, 0.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_put", strike=100.0)
        monitor.track_position(pos)

        # Simulate downtrend by injecting price history
        from datetime import datetime, timezone
        from tyche.workflow.active_monitor import PriceSnapshot
        from collections import deque

        now = datetime.now(timezone.utc)
        monitor._price_history["TEST"] = deque([
            PriceSnapshot(now.replace(second=0), 112.0, 112.0, 112.1, 100),
            PriceSnapshot(now.replace(second=30), 110.0, 110.0, 110.1, 100),
        ], maxlen=20)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "adverse_trend"]
        assert len(alerts) == 1
        assert "dropping" in alerts[0].message.lower()

    @pytest.mark.asyncio
    async def test_short_call_adverse_trend_up(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(99.0))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 2.50, 2.60)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0)
        monitor.track_position(pos)

        from datetime import datetime, timezone
        from tyche.workflow.active_monitor import PriceSnapshot
        from collections import deque

        now = datetime.now(timezone.utc)
        monitor._price_history["TEST"] = deque([
            PriceSnapshot(now.replace(second=0), 97.0, 97.0, 97.1, 100),
            PriceSnapshot(now.replace(second=30), 99.0, 99.0, 99.1, 100),
        ], maxlen=20)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "adverse_trend"]
        assert len(alerts) == 1
        assert "rising" in alerts[0].message.lower()


class TestSignificantLossAlert:
    """significant_loss has direction-appropriate roll actions."""

    @pytest.mark.asyncio
    async def test_short_put_loss_suggests_roll_down(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(95.0))
        contract = _make_contract("TEST260501P01000000", 100.0, "put", 6.0, 6.20)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_put", strike=100.0, entry_price=2.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "significant_loss"]
        assert len(alerts) == 1
        assert any("roll_down" in a.action for a in alerts[0].suggested_actions)

    @pytest.mark.asyncio
    async def test_short_call_loss_suggests_roll_up(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(105.0))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 6.0, 6.20)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0, entry_price=2.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "significant_loss"]
        assert len(alerts) == 1
        assert any("roll_up" in a.action for a in alerts[0].suggested_actions)


class TestProfitTarget:
    """Profit alerts work for both puts and calls (same formula)."""

    @pytest.mark.asyncio
    async def test_short_call_80_pct_profit(self):
        broker = MagicMock()
        broker.get_quote = AsyncMock(return_value=_make_quote(90.0))
        contract = _make_contract("TEST260501C01000000", 100.0, "call", 0.05, 0.10)
        broker.get_options_chain = AsyncMock(return_value=_make_chain(contract))

        monitor = ActiveMonitor(broker)
        pos = _tracked_position("short_call", strike=100.0, entry_price=2.0)
        monitor.track_position(pos)

        status = await monitor.check_position(pos.option_symbol)
        alerts = [a for a in status.alerts if a.alert_type == "profit_target_80"]
        assert len(alerts) == 1
