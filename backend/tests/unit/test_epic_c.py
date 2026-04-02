"""Tests for Epic C — market-cap policy, institutional batching, two-level shortlist.

C1: Strict market-cap metadata policy
C2: Always-on institutional filter with async batching
C3: Pre-allocator pool vs final display shortlist
"""

from __future__ import annotations

import asyncio
from datetime import date
from dataclasses import dataclass
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.broker.base import AccountBalance
from tyche.broker.mock import MockBroker
from tyche.market_data.institutional import (
    InstitutionalFilterStats,
    filter_by_institutional_ownership_batched,
)
from tyche.market_data.universe import UniverseBuilder
from tyche.strategy.engine import StrategyEngine
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.workflow.morning_scan import (
    MorningScanResult,
    PipelineStage,
    run_morning_scan,
)


# ═══════════════════════════════════════════════════════════════════════════
# C1: Strict Market-Cap Metadata Policy
# ═══════════════════════════════════════════════════════════════════════════


class TestStrictMarketCapPolicy:
    """Tests for allow_missing_market_cap config."""

    @pytest.mark.asyncio
    async def test_permissive_mode_passes_no_data(self):
        """Default (allow_missing=True): tickers without cap data pass through."""
        broker = MockBroker()
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda t: t
        meta_store.get_market_caps.return_value = {}

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000.0,
            allow_missing_market_cap=True,
        )

        cap_stages = [s for s in result.pipeline_stages if s.name == "Market Cap"]
        assert len(cap_stages) == 1
        assert cap_stages[0].output_count > 0
        assert "no data: passed" in cap_stages[0].detail

    @pytest.mark.asyncio
    async def test_strict_mode_drops_no_data(self):
        """Strict (allow_missing=False): tickers without cap data are dropped."""
        broker = MockBroker()
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda t: t
        meta_store.get_market_caps.return_value = {}

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000.0,
            allow_missing_market_cap=False,
        )

        cap_stages = [s for s in result.pipeline_stages if s.name == "Market Cap"]
        assert len(cap_stages) == 1
        assert cap_stages[0].output_count == 0
        assert "no data: dropped" in cap_stages[0].detail

    @pytest.mark.asyncio
    async def test_strict_mode_passes_above_threshold(self):
        """Strict mode: tickers WITH cap data above threshold still pass."""
        broker = MockBroker()
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda t: t
        meta_store.get_market_caps.return_value = {
            "AAPL": 3_000_000_000_000,
            "PL": 2_000_000_000,
        }

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["AAPL", "PL"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000.0,
            allow_missing_market_cap=False,
        )

        cap_stages = [s for s in result.pipeline_stages if s.name == "Market Cap"]
        assert len(cap_stages) == 1
        assert cap_stages[0].output_count == 1
        assert "below threshold" in cap_stages[0].detail

    @pytest.mark.asyncio
    async def test_stage_detail_reports_below_count(self):
        """Stage detail should report how many were dropped below threshold."""
        broker = MockBroker()
        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.filter_equity_only.side_effect = lambda t: t
        meta_store.get_market_caps.return_value = {
            "A": 100_000,
            "B": 200_000,
            "C": 10_000_000_000,
        }

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=StrategyEngine(),
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["A", "B", "C"],
            ticker_meta_store=meta_store,
            min_market_cap=5_000_000_000.0,
            allow_missing_market_cap=True,
        )

        cap_stages = [s for s in result.pipeline_stages if s.name == "Market Cap"]
        assert "2 below threshold" in cap_stages[0].detail


# ═══════════════════════════════════════════════════════════════════════════
# C2: Always-on Institutional Filter with Batching
# ═══════════════════════════════════════════════════════════════════════════


class TestInstitutionalBatchedFilter:
    """Tests for filter_by_institutional_ownership_batched."""

    @pytest.mark.asyncio
    async def test_basic_batching(self):
        """Small list should work in a single batch."""
        tickers = ["AAPL", "PL", "MSFT"]

        async def _mock_ownership(ticker):
            return {"AAPL": 0.79, "PL": 0.50, "MSFT": 0.85}.get(ticker)

        with patch(
            "tyche.market_data.institutional.get_institutional_ownership",
            side_effect=_mock_ownership,
        ):
            passed, ownership, stats = await filter_by_institutional_ownership_batched(
                tickers, min_pct=0.60, batch_size=10,
            )

        assert set(passed) == {"AAPL", "MSFT"}
        assert stats.tickers_passed == 2
        assert stats.tickers_dropped == 1
        assert stats.batches_run == 1

    @pytest.mark.asyncio
    async def test_multiple_batches(self):
        """batch_size=2 on 5 tickers should produce 3 batches."""
        tickers = ["A", "B", "C", "D", "E"]

        async def _mock(ticker):
            return 0.90

        with patch(
            "tyche.market_data.institutional.get_institutional_ownership",
            side_effect=_mock,
        ):
            passed, ownership, stats = await filter_by_institutional_ownership_batched(
                tickers, min_pct=0.40, batch_size=2,
            )

        assert len(passed) == 5
        assert stats.batches_run == 3

    @pytest.mark.asyncio
    async def test_no_data_passes_through(self):
        """Tickers with no ownership data pass (failure-safe)."""
        async def _mock(ticker):
            return None

        with patch(
            "tyche.market_data.institutional.get_institutional_ownership",
            side_effect=_mock,
        ):
            passed, ownership, stats = await filter_by_institutional_ownership_batched(
                ["X", "Y"], min_pct=0.40, batch_size=10,
            )

        assert set(passed) == {"X", "Y"}
        assert stats.tickers_no_data == 2

    @pytest.mark.asyncio
    async def test_empty_tickers(self):
        passed, ownership, stats = await filter_by_institutional_ownership_batched(
            [], min_pct=0.40,
        )
        assert passed == []
        assert stats.total_tickers == 0
        assert stats.batches_run == 0

    @pytest.mark.asyncio
    async def test_stats_to_dict(self):
        stats = InstitutionalFilterStats(
            total_tickers=10, batches_run=2, batches_failed=0,
            tickers_passed=8, tickers_dropped=2, tickers_no_data=0, retries=0,
        )
        d = stats.to_dict()
        assert d["total_tickers"] == 10
        assert d["batches_run"] == 2


class TestInstitutionalAlwaysOn:
    """The size gate (len <= 100) is removed; filter always runs."""

    @pytest.mark.asyncio
    async def test_runs_for_large_watchlist(self):
        """Institutional filter should run regardless of watchlist size."""
        broker = MockBroker()
        tickers = [f"T{i}" for i in range(150)]

        with patch(
            "tyche.workflow.morning_scan.filter_by_institutional_ownership_batched",
            new_callable=AsyncMock,
            return_value=(tickers, {}, InstitutionalFilterStats(total_tickers=150, batches_run=8)),
        ) as mock_filter:
            result = await run_morning_scan(
                broker=broker,
                strategy_engine=StrategyEngine(),
                analysis_agent=None,
                earnings_client=None,
                universe_builder=UniverseBuilder(min_avg_volume=0),
                watchlist=tickers,
                min_institutional_pct=0.40,
                institutional_batch_size=20,
            )

            mock_filter.assert_called_once()
            call_args = mock_filter.call_args
            assert call_args.kwargs.get("batch_size") == 20 or call_args[1].get("batch_size") == 20

    @pytest.mark.asyncio
    async def test_failure_safe_continues_scan(self):
        """If batched filter raises, scan continues (warn and proceed)."""
        broker = MockBroker()

        with patch(
            "tyche.workflow.morning_scan.filter_by_institutional_ownership_batched",
            new_callable=AsyncMock,
            side_effect=RuntimeError("yfinance down"),
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

        assert result.csp_candidates is not None


class TestInstitutionalBatchRetry:
    """Tests for retry/backoff in batched filter."""

    @pytest.mark.asyncio
    async def test_retry_on_exception_then_succeed(self):
        """Should retry after a failure and succeed on next attempt."""
        call_count = 0

        async def _flaky(ticker):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ConnectionError("transient failure")
            return 0.80

        with patch(
            "tyche.market_data.institutional.get_institutional_ownership",
            side_effect=_flaky,
        ):
            passed, ownership, stats = await filter_by_institutional_ownership_batched(
                ["AAPL"], min_pct=0.40, batch_size=10,
                max_retries=2, backoff_base=0.01,
            )

        assert len(passed) == 1
        assert stats.retries >= 1

    @pytest.mark.asyncio
    async def test_exhausted_retries_still_passes_no_data(self):
        """After all retries fail, tickers without data pass through."""
        async def _always_fail(ticker):
            raise ConnectionError("permanent failure")

        with patch(
            "tyche.market_data.institutional.get_institutional_ownership",
            side_effect=_always_fail,
        ):
            passed, ownership, stats = await filter_by_institutional_ownership_batched(
                ["AAPL"], min_pct=0.40, batch_size=10,
                max_retries=1, backoff_base=0.01,
            )

        assert "AAPL" in passed
        assert stats.batches_failed == 1


# ═══════════════════════════════════════════════════════════════════════════
# C3: Pre-Allocator Pool vs Final Display Shortlist
# ═══════════════════════════════════════════════════════════════════════════


class TestPreAllocatorPool:
    """Tests for two-level shortlist (pool_size vs top_n)."""

    @pytest.mark.asyncio
    async def test_default_no_pool(self):
        """When pre_allocator_pool_size=0, engine returns top_n."""
        broker = MockBroker()
        engine = StrategyEngine()
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100_000.0,
            top_n=3,
            pre_allocator_pool_size=0,
            earliest_expiration_only=False,
        )
        assert len(candidates) <= 3
        assert diag.get("pre_allocator_pool_size", 0) <= 3

    @pytest.mark.asyncio
    async def test_pool_larger_than_top_n(self):
        """Pool returns more candidates than top_n for allocator."""
        broker = MockBroker()
        engine = StrategyEngine()
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100_000.0,
            top_n=2,
            pre_allocator_pool_size=20,
            earliest_expiration_only=False,
        )
        if diag.get("symbols_with_candidates", 0) > 0:
            assert len(candidates) >= 2 or len(candidates) == diag["pre_allocator_pool_size"]

    @pytest.mark.asyncio
    async def test_morning_scan_splits_pool_vs_display(self):
        """Morning scan: allocator gets full pool, display gets top_n."""
        broker = MockBroker()

        csp_pool = [MagicMock(spec=ScoredCandidate, symbol=f"S{i}") for i in range(10)]

        async def _fake_scan(**kwargs):
            return csp_pool, {"symbols_with_candidates": 5, "pre_allocator_pool_size": 10}

        engine = StrategyEngine()
        engine.scan_csp_candidates = _fake_scan

        allocator = MagicMock()
        allocator.optimize.return_value = MagicMock()

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=engine,
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            portfolio_allocator=allocator,
            top_n=3,
            pre_allocator_pool_size=10,
            min_institutional_pct=0,
        )

        assert len(result.csp_candidates) == 3
        alloc_call = allocator.optimize.call_args
        alloc_csp = alloc_call.kwargs.get("csp_candidates") or alloc_call[0][0]
        assert len(alloc_csp) == 10

    @pytest.mark.asyncio
    async def test_pool_disabled_allocator_gets_top_n(self):
        """When pool_size=0, allocator gets the same top_n list."""
        broker = MockBroker()

        csp_top = [MagicMock(spec=ScoredCandidate, symbol=f"S{i}") for i in range(3)]

        async def _fake_scan(**kwargs):
            return csp_top, {"symbols_with_candidates": 2, "pre_allocator_pool_size": 3}

        engine = StrategyEngine()
        engine.scan_csp_candidates = _fake_scan

        allocator = MagicMock()
        allocator.optimize.return_value = MagicMock()

        result = await run_morning_scan(
            broker=broker,
            strategy_engine=engine,
            analysis_agent=None,
            earnings_client=None,
            universe_builder=UniverseBuilder(min_avg_volume=0),
            watchlist=["PL"],
            portfolio_allocator=allocator,
            top_n=3,
            pre_allocator_pool_size=0,
            min_institutional_pct=0,
        )

        assert len(result.csp_candidates) == 3
        alloc_call = allocator.optimize.call_args
        alloc_csp = alloc_call.kwargs.get("csp_candidates") or alloc_call[0][0]
        assert len(alloc_csp) == 3


# ═══════════════════════════════════════════════════════════════════════════
# Config fields
# ═══════════════════════════════════════════════════════════════════════════


class TestConfigFields:
    """Verify new config fields exist with correct defaults."""

    def test_allow_missing_market_cap_default(self):
        from tyche.config import TycheSettings
        s = TycheSettings(tradier_api_token="t", tradier_account_id="a", gemini_api_key="g")
        assert s.allow_missing_market_cap is True

    def test_institutional_batch_size_default(self):
        from tyche.config import TycheSettings
        s = TycheSettings(tradier_api_token="t", tradier_account_id="a", gemini_api_key="g")
        assert s.institutional_batch_size == 20

    def test_institutional_max_retries_default(self):
        from tyche.config import TycheSettings
        s = TycheSettings(tradier_api_token="t", tradier_account_id="a", gemini_api_key="g")
        assert s.institutional_max_retries == 2

    def test_pre_allocator_pool_size_default(self):
        from tyche.config import TycheSettings
        s = TycheSettings(tradier_api_token="t", tradier_account_id="a", gemini_api_key="g")
        assert s.pre_allocator_pool_size == 0
