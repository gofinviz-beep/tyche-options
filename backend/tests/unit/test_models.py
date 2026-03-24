"""Tests for database models — verify schema creation and basic operations."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tyche.models import (
    AccountSnapshot,
    BotMemory,
    EarningsEntry,
    ExecutionDecision,
    OpenOrder,
    OptionCandidate,
    OrderMonitorSnapshot,
    Position,
    TradeJournal,
    TradeRecommendation,
    WatchlistSymbol,
    WheelCycle,
)


@pytest.mark.asyncio
async def test_account_snapshot_roundtrip(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    snapshot = AccountSnapshot(
        captured_at=now,
        cash=50000.0,
        buying_power=50000.0,
        net_liquidation_value=112000.0,
        market_value=62000.0,
        total_equity=112000.0,
    )
    db_session.add(snapshot)
    await db_session.commit()

    result = await db_session.execute(select(AccountSnapshot))
    loaded = result.scalar_one()
    assert loaded.cash == 50000.0
    assert loaded.net_liquidation_value == 112000.0


@pytest.mark.asyncio
async def test_wheel_cycle_creation(db_session: AsyncSession) -> None:
    cycle = WheelCycle(
        started_at=datetime.now(timezone.utc),
        symbol="PL",
        state="csp_pending",
        csp_strike=23.0,
        csp_contracts=40,
    )
    db_session.add(cycle)
    await db_session.commit()

    result = await db_session.execute(select(WheelCycle))
    loaded = result.scalar_one()
    assert loaded.symbol == "PL"
    assert loaded.state == "csp_pending"
    assert loaded.csp_strike == 23.0
    assert loaded.csp_contracts == 40
    assert loaded.total_premium_collected == 0.0


@pytest.mark.asyncio
async def test_all_tables_created(db_session: AsyncSession) -> None:
    """Verify all 12 tables can be queried without errors."""
    for model in [
        AccountSnapshot,
        Position,
        OpenOrder,
        OrderMonitorSnapshot,
        ExecutionDecision,
        WatchlistSymbol,
        OptionCandidate,
        TradeRecommendation,
        TradeJournal,
        BotMemory,
        WheelCycle,
        EarningsEntry,
    ]:
        result = await db_session.execute(select(model))
        assert result.all() == []
