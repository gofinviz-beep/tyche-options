"""Extended tests for morning_scan.py — covers error branches, timing,
capital override, dynamic universe, market cap filter, conviction failures,
institutional failures, earnings failures, CSP/CC failures, portfolio
allocator, and LLM analysis paths.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from tyche.broker.base import AccountBalance, BrokerPosition
from tyche.broker.mock import MockBroker
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal, TrendState
from tyche.market_data.universe import UniverseBuilder
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.allocator import AllocationResult, PortfolioAllocator
from tyche.strategy.engine import StrategyEngine
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.workflow.morning_scan import (
    MorningScanResult,
    PipelineStage,
    run_morning_scan,
)


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker()


# ---------------------------------------------------------------------------
# PipelineStage
# ---------------------------------------------------------------------------

class TestPipelineStage:
    def test_to_dict_includes_duration(self):
        stage = PipelineStage("Test", 10, 8, detail="Some detail", duration_ms=42.567)
        d = stage.to_dict()
        assert d["name"] == "Test"
        assert d["input"] == 10
        assert d["output"] == 8
        assert d["dropped"] == 2
        assert d["detail"] == "Some detail"
        assert d["duration_ms"] == 42.57

    def test_dropped_computed(self):
        stage = PipelineStage("X", 100, 30)
        assert stage.dropped == 70


# ---------------------------------------------------------------------------
# MorningScanResult
# ---------------------------------------------------------------------------

class TestMorningScanResult:
    def test_defaults(self):
        r = MorningScanResult()
        assert r.scan_id
        assert r.scanned_at is not None
        assert r.csp_candidates == []
        assert r.cc_candidates == []
        assert r.csp_analyses == []
        assert r.errors == []
        assert r.pipeline_stages == []
        assert r.total_duration_ms == 0.0


# ---------------------------------------------------------------------------
# Account load failure (lines 133-140)
# ---------------------------------------------------------------------------

class TestAccountLoadFailure:
    @pytest.mark.asyncio
    async def test_broker_exception_returns_early_with_error(self):
        broker = MockBroker()
        broker.get_account_balances = AsyncMock(side_effect=ConnectionError("down"))

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["AAPL"],
        )

        assert len(result.errors) == 1
        assert "Failed to load account state" in result.errors[0]
        assert result.csp_candidates == []
        assert result.total_duration_ms > 0


# ---------------------------------------------------------------------------
# Capital override (lines 146-152)
# ---------------------------------------------------------------------------

class TestCapitalOverride:
    @pytest.mark.asyncio
    async def test_override_applied_when_buying_power_zero(self):
        broker = MockBroker()
        broker.get_account_balances = AsyncMock(return_value=AccountBalance(
            cash=0, buying_power=0, net_liquidation_value=0,
            market_value=0, total_equity=0, open_pl=0, close_pl=0, pending_cash=0,
        ))

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            available_capital_override=100_000.0,
        )

        assert result.errors == [] or all("Failed" not in e for e in result.errors)
        assert result.symbols_scanned == 1


# ---------------------------------------------------------------------------
# Dynamic universe (lines 170-176) — no watchlist, data store exists
# ---------------------------------------------------------------------------

class TestDynamicUniverse:
    @pytest.mark.asyncio
    async def test_uses_data_store_when_no_watchlist(self, broker):
        mock_store = MagicMock()
        mock_store.exists = True
        mock_store.screen_universe.return_value = ["MSFT", "GOOG"]

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=[],
            data_store=mock_store,
        )

        mock_store.screen_universe.assert_called_once()
        assert result.symbols_scanned == 2

    @pytest.mark.asyncio
    async def test_no_watchlist_no_data_store_returns_error(self, broker):
        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=[],
            data_store=None,
        )

        assert len(result.errors) >= 1
        assert "No watchlist" in result.errors[0]


# ---------------------------------------------------------------------------
# Empty fundamental screen (lines 199-202)
# ---------------------------------------------------------------------------

class TestEmptyFundamentalScreen:
    @pytest.mark.asyncio
    async def test_all_symbols_filtered_returns_error(self, broker):
        ub = UniverseBuilder(min_avg_volume=0)
        ub.screen_watchlist = MagicMock(return_value=[])

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=ub,
            watchlist=["PL"],
        )

        assert any("No symbols passed fundamental screening" in e for e in result.errors)


# ---------------------------------------------------------------------------
# Market cap filter (lines 204-240)
# ---------------------------------------------------------------------------

class TestMarketCapFilter:
    @pytest.mark.asyncio
    async def test_market_cap_drops_small_caps(self, broker):
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda tickers: tickers
        meta_store.get_market_caps.return_value = {"PL": 2_000_000_000, "AAPL": 3_000_000_000_000}

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL", "AAPL"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000,
        )

        cap_stages = [s for s in result.pipeline_stages if s.name == "Market Cap"]
        assert len(cap_stages) == 1
        assert cap_stages[0].dropped >= 1

    @pytest.mark.asyncio
    async def test_market_cap_passes_no_data_tickers(self, broker):
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda tickers: tickers
        meta_store.get_market_caps.return_value = {}

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000,
        )

        cap_stages = [s for s in result.pipeline_stages if s.name == "Market Cap"]
        assert len(cap_stages) == 1
        assert "no data" in cap_stages[0].detail

    @pytest.mark.asyncio
    async def test_all_filtered_by_market_cap(self, broker):
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda tickers: tickers
        meta_store.get_market_caps.return_value = {"PL": 100_000}

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000,
        )

        assert any("market cap" in e.lower() for e in result.errors)


# ---------------------------------------------------------------------------
# Conviction engine failure (lines 279-283) + no eligible (line 265-266)
# ---------------------------------------------------------------------------

class TestConvictionEngine:
    @pytest.mark.asyncio
    async def test_conviction_exception_is_swallowed(self, broker):
        mock_store = MagicMock()
        mock_store.exists = True
        mock_store.read_tickers.side_effect = RuntimeError("parquet corrupted")

        engine = ConvictionEngine()

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            conviction_engine=engine,
            data_store=mock_store,
        )

        assert result.conviction_signals == {}
        assert result.csp_candidates is not None

    @pytest.mark.asyncio
    async def test_conviction_no_eligible_does_not_crash(self, broker):
        """When conviction engine returns signals but none are CSP eligible."""
        mock_store = MagicMock()
        mock_store.exists = True
        mock_store.read_tickers.return_value = {"PL": pd.DataFrame({
            "date": pd.date_range("2025-01-01", periods=80),
            "open": [100.0] * 80,
            "high": [101.0] * 80,
            "low": [99.0] * 80,
            "close": [100 - i * 0.5 for i in range(80)],
            "volume": [1_000_000] * 80,
            "vwap": [100.0] * 80,
        })}

        engine = ConvictionEngine()

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            conviction_engine=engine,
            data_store=mock_store,
        )

        assert len(result.conviction_signals) >= 0

    @pytest.mark.asyncio
    async def test_conviction_eligible_filters_symbols(self, broker):
        """When conviction engine finds eligible symbols, the pipeline narrows."""
        eligible_signal = ConvictionSignal(
            ticker="PL",
            trend_state=TrendState.STRONG_UPTREND,
            conviction_level="high",
            csp_eligible=True,
            ema_8=24.0,
            ema_21=23.5,
            last_close=24.5,
            days_above_both_emas=5,
        )
        ineligible_signal = ConvictionSignal(
            ticker="AAPL",
            trend_state=TrendState.DOWNTREND,
            conviction_level="low",
            csp_eligible=False,
            ema_8=190.0,
            ema_21=192.0,
            last_close=188.0,
            days_above_both_emas=0,
        )

        mock_store = MagicMock()
        mock_store.exists = True
        mock_store.read_tickers.return_value = {"PL": MagicMock(), "AAPL": MagicMock()}

        mock_conviction = MagicMock(spec=ConvictionEngine)
        mock_conviction.analyze_batch.return_value = [eligible_signal, ineligible_signal]

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL", "AAPL"],
            conviction_engine=mock_conviction,
            data_store=mock_store,
        )

        assert "PL" in result.conviction_signals
        assert "AAPL" in result.conviction_signals
        assert result.conviction_signals["PL"].csp_eligible is True
        conviction_stages = [s for s in result.pipeline_stages if s.name == "EMA Conviction"]
        assert len(conviction_stages) == 1
        assert conviction_stages[0].input_count == 2
        assert conviction_stages[0].output_count == 1


# ---------------------------------------------------------------------------
# Institutional ownership failure (lines 311-315)
# ---------------------------------------------------------------------------

class TestInstitutionalFailure:
    @pytest.mark.asyncio
    async def test_institutional_exception_is_swallowed(self, broker):
        with patch(
            "tyche.workflow.morning_scan.filter_by_institutional_ownership",
            new_callable=AsyncMock,
            side_effect=RuntimeError("SEC API down"),
        ):
            result = await run_morning_scan(
                broker=broker,
                strategy_engine=StrategyEngine(),
                analysis_agent=None,
                earnings_client=None,
                universe_builder=UniverseBuilder(min_avg_volume=0),
                watchlist=["PL"],
                min_institutional_pct=0.40,
            )

        assert result.institutional_ownership == {}
        assert result.csp_candidates is not None


# ---------------------------------------------------------------------------
# Earnings failure (lines 328-330)
# ---------------------------------------------------------------------------

class TestEarningsFetch:
    @pytest.mark.asyncio
    async def test_earnings_exception_is_swallowed(self, broker):
        mock_earnings = AsyncMock()
        mock_earnings.get_upcoming_earnings = AsyncMock(
            side_effect=RuntimeError("API timeout"),
        )

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=mock_earnings,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        assert result.earnings_context == {}
        assert result.csp_candidates is not None

    @pytest.mark.asyncio
    async def test_earnings_data_populated_on_success(self, broker):
        mock_earnings = AsyncMock()
        mock_earnings.get_upcoming_earnings = AsyncMock(
            return_value={
                "PL": {"earnings_date": "2026-05-01", "source": "estimates"},
            },
        )

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=mock_earnings,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        assert "PL" in result.earnings_context
        assert result.earnings_context["PL"]["earnings_date"] == "2026-05-01"


# ---------------------------------------------------------------------------
# CSP scan failure (lines 349-352)
# ---------------------------------------------------------------------------

class TestCSPScanFailure:
    @pytest.mark.asyncio
    async def test_csp_exception_recorded_in_errors(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine.scan_csp_candidates = AsyncMock(
            side_effect=ValueError("options chain empty"),
        )  # side_effect raises, so no tuple needed
        mock_engine.scan_cc_candidates = AsyncMock(return_value=[])

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        assert any("CSP scan failed" in e for e in result.errors)
        assert result.csp_candidates == []


# ---------------------------------------------------------------------------
# CC scan failure (lines 365-368)
# ---------------------------------------------------------------------------

class TestCCScanFailure:
    @pytest.mark.asyncio
    async def test_cc_exception_recorded_in_errors(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine.scan_csp_candidates = AsyncMock(return_value=([], {}))
        mock_engine.scan_cc_candidates = AsyncMock(
            side_effect=ValueError("no held shares"),
        )

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        assert any("CC scan failed" in e for e in result.errors)
        assert result.cc_candidates == []


# ---------------------------------------------------------------------------
# Portfolio allocator (lines 372-407)
# ---------------------------------------------------------------------------

def _make_csp_candidate(symbol: str = "PL", strike: float = 23.0) -> ScoredCandidate:
    return ScoredCandidate(
        symbol=symbol,
        option_symbol=f"{symbol}260403P{int(strike*1000):08d}",
        option_type="put",
        strike=strike,
        expiration=date(2026, 4, 3),
        dte=5,
        bid=1.50,
        ask=1.60,
        mid=1.55,
        underlying_price=strike + 1.5,
        strategy="csp",
        premium_per_contract=150.0,
        collateral_required=strike * 100,
        annualized_return_pct=20.0,
        score=80.0,
        delta=-0.25,
        theta=0.05,
        implied_volatility=0.45,
        volume=500,
        open_interest=2000,
        earnings_within_dte=False,
    )


class TestPortfolioAllocator:
    @pytest.mark.asyncio
    async def test_allocator_runs_when_candidates_exist(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine.scan_csp_candidates = AsyncMock(
            return_value=([_make_csp_candidate()], {"symbols_with_candidates": 1}),
        )
        mock_engine.scan_cc_candidates = AsyncMock(return_value=[])

        allocator = PortfolioAllocator()

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            portfolio_allocator=allocator,
        )

        assert result.allocation is not None
        assert result.allocation.solver_status in ("optimal", "greedy_fallback")

    @pytest.mark.asyncio
    async def test_allocator_exception_is_swallowed(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine.scan_csp_candidates = AsyncMock(
            return_value=([_make_csp_candidate()], {"symbols_with_candidates": 1}),
        )
        mock_engine.scan_cc_candidates = AsyncMock(return_value=[])

        bad_allocator = MagicMock(spec=PortfolioAllocator)
        bad_allocator.optimize.side_effect = RuntimeError("solver crash")

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            portfolio_allocator=bad_allocator,
        )

        assert result.allocation is None
        assert result.csp_candidates == [_make_csp_candidate()]


# ---------------------------------------------------------------------------
# LLM analysis (lines 409-472)
# ---------------------------------------------------------------------------

def _make_analysis(ticker: str = "PL") -> CSPAnalysis:
    return CSPAnalysis(
        ticker=ticker,
        assignment_comfort="high",
        assignment_comfort_reasoning="Strong trend",
        thesis=f"Bullish on {ticker}",
        recommended_strike=23.0,
        recommended_expiration="2026-04-03",
        target_premium=1.50,
        annualized_return_pct=20.0,
        invalidation="Support breakdown",
        confidence="high",
        risks=["Market crash"],
        would_you_hold_if_assigned="Yes",
        suggested_contracts=5,
        collateral_required=11500.0,
        allocation_mode="concentrated",
    )


class TestLLMAnalysis:
    @pytest.mark.asyncio
    async def test_llm_analysis_called_per_ticker(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        candidates = [_make_csp_candidate("PL"), _make_csp_candidate("AAPL", 180.0)]
        mock_engine.scan_csp_candidates = AsyncMock(return_value=(candidates, {"symbols_with_candidates": 2}))
        mock_engine.scan_cc_candidates = AsyncMock(return_value=[])

        mock_agent = MagicMock()
        mock_agent.analyze_csp_candidates = AsyncMock(
            side_effect=lambda candidates, **kw: [_make_analysis(candidates[0].symbol)],
        )

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=mock_agent,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL", "AAPL"],
            llm_concurrency=2,
        )

        assert len(result.csp_analyses) == 2
        assert mock_agent.analyze_csp_candidates.call_count == 2
        tickers_analyzed = {a.ticker for a in result.csp_analyses}
        assert tickers_analyzed == {"PL", "AAPL"}

    @pytest.mark.asyncio
    async def test_llm_failure_per_ticker_returns_empty(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine.scan_csp_candidates = AsyncMock(
            return_value=([_make_csp_candidate("PL")], {"symbols_with_candidates": 1}),
        )
        mock_engine.scan_cc_candidates = AsyncMock(return_value=[])

        mock_agent = MagicMock()
        mock_agent.analyze_csp_candidates = AsyncMock(
            side_effect=RuntimeError("Gemini 503"),
        )

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=mock_agent,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        assert result.csp_analyses == []
        assert result.csp_candidates == [_make_csp_candidate("PL")]

    @pytest.mark.asyncio
    async def test_llm_not_called_when_no_candidates(self, broker):
        mock_engine = MagicMock(spec=StrategyEngine)
        mock_engine.scan_csp_candidates = AsyncMock(return_value=([], {}))
        mock_engine.scan_cc_candidates = AsyncMock(return_value=[])

        mock_agent = MagicMock()
        mock_agent.analyze_csp_candidates = AsyncMock()

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=mock_engine,
            analysis_agent=mock_agent,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        mock_agent.analyze_csp_candidates.assert_not_called()
        assert result.csp_analyses == []


# ---------------------------------------------------------------------------
# Timing / duration fields
# ---------------------------------------------------------------------------

class TestTimingFields:
    @pytest.mark.asyncio
    async def test_total_duration_is_set(self, broker):
        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
        )

        assert result.total_duration_ms > 0

    @pytest.mark.asyncio
    async def test_pipeline_stages_have_duration(self, broker):
        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL", "AAPL"],
        )

        for stage in result.pipeline_stages:
            d = stage.to_dict()
            assert "duration_ms" in d
            assert d["duration_ms"] >= 0


# ---------------------------------------------------------------------------
# Full pipeline integration
# ---------------------------------------------------------------------------

class TestFullPipeline:
    @pytest.mark.asyncio
    async def test_full_pipeline_with_all_stages(self, broker):
        """End-to-end: watchlist → screen → CSP/CC → allocator → LLM."""
        mock_agent = MagicMock()
        mock_agent.analyze_csp_candidates = AsyncMock(
            side_effect=lambda candidates, **kw: [_make_analysis(candidates[0].symbol)],
        )

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=mock_agent,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL", "AAPL"],
            portfolio_allocator=PortfolioAllocator(),
            top_n=5,
        )

        assert result.symbols_scanned == 2
        assert len(result.csp_candidates) > 0
        assert len(result.csp_analyses) > 0
        assert result.allocation is not None
        assert result.total_duration_ms > 0
        assert len(result.errors) == 0
