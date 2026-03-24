"""Tests for workflow orchestration — morning scan and order monitor."""

from __future__ import annotations

import pytest

from tyche.broker.mock import MockBroker
from tyche.market_data.universe import UniverseBuilder
from tyche.strategy.engine import StrategyEngine
from tyche.workflow.morning_scan import run_morning_scan
from tyche.workflow.order_monitor import run_order_monitor


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker()


class TestMorningScan:
    @pytest.mark.asyncio
    async def test_scan_without_llm(self, broker: MockBroker) -> None:
        """Morning scan should work without LLM (deterministic only)."""
        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL", "AAPL"],
            top_n=5,
        )
        assert result.scan_id
        assert result.symbols_scanned == 2
        assert len(result.csp_candidates) > 0
        assert len(result.cc_candidates) > 0
        assert result.csp_analyses == []  # No LLM
        assert result.errors == []

    @pytest.mark.asyncio
    async def test_scan_empty_watchlist(self, broker: MockBroker) -> None:
        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=[],
        )
        assert result.symbols_scanned == 0
        assert len(result.errors) > 0  # "No symbols passed"


class TestOrderMonitor:
    @pytest.mark.asyncio
    async def test_monitor_without_llm(self, broker: MockBroker) -> None:
        """Order monitor should work without LLM (data-only)."""
        result = await run_order_monitor(broker=broker, analysis_agent=None)
        assert result.orders_checked >= 1
        assert len(result.alerts) >= 1
        assert result.analyses == []  # No LLM

    @pytest.mark.asyncio
    async def test_monitor_alert_fields(self, broker: MockBroker) -> None:
        result = await run_order_monitor(broker=broker)
        for alert in result.alerts:
            assert "order_id" in alert
            assert "symbol" in alert
            assert "limit_price" in alert
            assert "underlying_price" in alert
