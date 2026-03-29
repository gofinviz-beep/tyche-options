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
    LLMAnalysisRecord,
    OpenOrder,
    OptionCandidate,
    OrderMonitorSnapshot,
    Position,
    ScanCandidate,
    ScanRun,
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
    """Verify all 15 tables can be queried without errors."""
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
        ScanRun,
        ScanCandidate,
        LLMAnalysisRecord,
    ]:
        result = await db_session.execute(select(model))
        assert result.all() == []


@pytest.mark.asyncio
async def test_scan_run_roundtrip(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    run = ScanRun(
        scanned_at=now,
        trigger="manual",
        symbols_scanned=50,
        csp_candidate_count=10,
        pipeline_stages="[]",
        errors="[]",
    )
    db_session.add(run)
    await db_session.commit()

    result = await db_session.execute(select(ScanRun))
    loaded = result.scalar_one()
    assert loaded.symbols_scanned == 50
    assert loaded.trigger == "manual"
    assert loaded.csp_candidate_count == 10


@pytest.mark.asyncio
async def test_scan_candidate_roundtrip(db_session: AsyncSession) -> None:
    cand = ScanCandidate(
        scan_id="test-scan-001",
        strategy="csp",
        symbol="AAPL",
        option_symbol="AAPL260403P00200000",
        strike=200.0,
        expiration="2026-04-03",
        dte=5,
        bid=3.50,
        ask=3.80,
        premium_per_contract=350.0,
        score=8.5,
    )
    db_session.add(cand)
    await db_session.commit()

    result = await db_session.execute(select(ScanCandidate))
    loaded = result.scalar_one()
    assert loaded.symbol == "AAPL"
    assert loaded.strike == 200.0
    assert loaded.strategy == "csp"


@pytest.mark.asyncio
async def test_llm_analysis_roundtrip(db_session: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    analysis = LLMAnalysisRecord(
        scan_id="test-scan-001",
        ticker="AAPL",
        thesis="Strong uptrend with EMA support",
        confidence="high",
        assignment_comfort="high",
        recommended_strike=195.0,
        status="success",
        created_at=now,
    )
    db_session.add(analysis)
    await db_session.commit()

    result = await db_session.execute(select(LLMAnalysisRecord))
    loaded = result.scalar_one()
    assert loaded.ticker == "AAPL"
    assert loaded.confidence == "high"
    assert loaded.status == "success"
