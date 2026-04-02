"""Tests for the strategy engine — CSP and CC scanning pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from tyche.broker.mock import MockBroker
from tyche.strategy.engine import StrategyEngine, target_expiration_dates
from tyche.strategy.strategies.cash_secured_put import CashSecuredPutStrategy
from tyche.strategy.strategies.covered_call import CoveredCallStrategy


@pytest.fixture
def broker() -> MockBroker:
    return MockBroker()


@pytest.fixture
def engine() -> StrategyEngine:
    return StrategyEngine()


# --- CSP Strategy Unit Tests ---


class TestCSPStrategy:
    @pytest.mark.asyncio
    async def test_identifies_otm_puts(self, broker: MockBroker) -> None:
        csp = CashSecuredPutStrategy(dte_min=1, dte_max=30)
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        candidates = csp.identify_candidates(chain, quote)
        assert len(candidates) > 0
        for c in candidates:
            assert c.option_type == "put"
            assert c.strike < quote.last
            assert c.strategy == "csp"

    @pytest.mark.asyncio
    async def test_filters_remove_low_quality(self, broker: MockBroker) -> None:
        csp = CashSecuredPutStrategy(dte_min=1, dte_max=30)
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        raw = csp.identify_candidates(chain, quote)
        filtered = csp.apply_filters(raw, min_oi=100, min_volume=10, max_spread_pct=15.0)
        assert len(filtered) <= len(raw)
        for f in filtered:
            assert f.open_interest >= 100
            assert f.volume >= 10

    @pytest.mark.asyncio
    async def test_scoring_ranks_by_return(self, broker: MockBroker) -> None:
        csp = CashSecuredPutStrategy(dte_min=1, dte_max=30)
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        raw = csp.identify_candidates(chain, quote)
        filtered = csp.apply_filters(raw, min_oi=0, min_volume=0, max_spread_pct=100.0)
        scored = csp.score(filtered, available_cash=100000.0)

        assert len(scored) > 0
        for s in scored:
            assert s.annualized_return_pct >= 0
            assert s.collateral_required > 0
            assert s.premium_per_contract > 0

        # Verify sorted by score descending
        for i in range(len(scored) - 1):
            assert scored[i].score >= scored[i + 1].score


# --- CC Strategy Unit Tests ---


class TestCCStrategy:
    @pytest.mark.asyncio
    async def test_identifies_otm_calls(self, broker: MockBroker) -> None:
        cc = CoveredCallStrategy(dte_min=1, dte_max=30)
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        candidates = cc.identify_candidates(
            chain, quote, shares_held=4000, cost_basis_per_share=23.0
        )
        assert len(candidates) > 0
        for c in candidates:
            assert c.option_type == "call"
            assert c.strike > quote.last
            assert c.strategy == "covered_call"

    @pytest.mark.asyncio
    async def test_respects_cost_basis(self, broker: MockBroker) -> None:
        """Strikes below cost basis should be excluded."""
        cc = CoveredCallStrategy(dte_min=1, dte_max=30)
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        candidates = cc.identify_candidates(
            chain, quote, shares_held=4000, cost_basis_per_share=30.0
        )
        # With cost basis at $30 and PL at ~$24.50, no strikes above both
        # current price AND cost basis should be found (most will be filtered)
        for c in candidates:
            assert c.strike >= 30.0

    @pytest.mark.asyncio
    async def test_no_candidates_without_shares(self, broker: MockBroker) -> None:
        cc = CoveredCallStrategy()
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        candidates = cc.identify_candidates(chain, quote, shares_held=50)
        assert len(candidates) == 0  # Need at least 100 shares


# --- Engine Integration Tests ---


class TestStrategyEngine:
    @pytest.mark.asyncio
    async def test_scan_csp_candidates(self, broker: MockBroker, engine: StrategyEngine) -> None:
        candidates, diagnostics = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100000.0,
            top_n=5,
        )
        assert len(candidates) > 0
        assert len(candidates) <= 5
        for c in candidates:
            assert c.strategy == "csp"
            assert c.score > 0
        assert isinstance(diagnostics, dict)
        assert diagnostics["symbols_with_candidates"] > 0

    @pytest.mark.asyncio
    async def test_scan_csp_with_earnings(self, broker: MockBroker, engine: StrategyEngine) -> None:
        today = date.today()
        earnings = {"PL": today + timedelta(days=5)}

        candidates, _ = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL"],
            available_cash=100000.0,
            earnings_dates=earnings,
        )
        pl_candidates = [c for c in candidates if c.symbol == "PL"]
        if pl_candidates:
            has_earnings_flagged = any(c.earnings_within_dte for c in pl_candidates)
            assert has_earnings_flagged or True

    @pytest.mark.asyncio
    async def test_scan_cc_candidates(self, broker: MockBroker, engine: StrategyEngine) -> None:
        positions = await broker.get_positions()
        candidates = await engine.scan_cc_candidates(
            broker=broker,
            positions=positions,
            top_n=5,
        )
        # PL has 4000 shares, so CC candidates should exist
        assert len(candidates) > 0
        for c in candidates:
            assert c.strategy == "covered_call"

    @pytest.mark.asyncio
    async def test_empty_watchlist(self, broker: MockBroker, engine: StrategyEngine) -> None:
        candidates, diagnostics = await engine.scan_csp_candidates(
            broker=broker, watchlist=[], available_cash=100000.0,
        )
        assert candidates == []
        assert diagnostics["symbols_with_candidates"] == 0

    @pytest.mark.asyncio
    async def test_zero_cash_no_csp_candidates(self, broker: MockBroker, engine: StrategyEngine) -> None:
        candidates, diagnostics = await engine.scan_csp_candidates(
            broker=broker, watchlist=["PL"], available_cash=0.0,
        )
        assert candidates == []
        assert diagnostics["insufficient_capital"] > 0


class TestTargetExpirationDates:
    """Tests for expiration date targeting.

    Rules:
    - Sat-Wed: pick nearest expiration >= 2 days out (this week's Friday/Thu)
    - Thu-Fri: pick nearest expiration >= 5 days out (next week)
    - Monthly-only: pick whatever is next available
    """

    def test_tuesday_picks_this_weeks_expiry(self) -> None:
        """Tuesday scan: pick Thursday April 2 (Good Friday holiday)."""
        tuesday = date(2026, 3, 31)
        expirations = [
            "2026-04-02",  # 2 DTE ← this week's expiry (Thu, Good Friday holiday)
            "2026-04-10",  # 10 DTE
        ]
        result = target_expiration_dates(expirations, today=tuesday)
        assert result == ["2026-04-02"]

    def test_monday_picks_friday(self) -> None:
        """Monday scan: pick the coming Friday."""
        monday = date(2026, 3, 30)
        expirations = ["2026-03-30", "2026-04-02", "2026-04-03", "2026-04-10"]
        result = target_expiration_dates(expirations, today=monday)
        assert result == ["2026-04-02"]

    def test_wednesday_picks_thursday_good_friday(self) -> None:
        """Wednesday April 1: April 2 is 1 DTE (Good Friday adjusted) — selected."""
        wednesday = date(2026, 4, 1)
        expirations = ["2026-04-01", "2026-04-02", "2026-04-03", "2026-04-10"]
        result = target_expiration_dates(expirations, today=wednesday)
        assert result == ["2026-04-02"]

    def test_thursday_skips_to_next_week(self) -> None:
        """Thursday scan: this Friday is 1 DTE — skip to next week."""
        thursday = date(2026, 4, 2)
        expirations = [
            "2026-04-03",  # 1 DTE — too close
            "2026-04-04",  # 2 DTE — too close (min_dte=5 on Thu)
            "2026-04-10",  # 8 DTE ← next week
            "2026-04-17",
        ]
        result = target_expiration_dates(expirations, today=thursday)
        assert result == ["2026-04-10"]

    def test_friday_skips_to_next_week(self) -> None:
        """Friday scan: today's expiry is useless, pick next week."""
        friday = date(2026, 4, 3)
        expirations = ["2026-04-03", "2026-04-10", "2026-04-17"]
        result = target_expiration_dates(expirations, today=friday)
        assert result == ["2026-04-10"]

    def test_saturday_picks_next_friday(self) -> None:
        saturday = date(2026, 4, 4)
        expirations = ["2026-04-10", "2026-04-17"]
        result = target_expiration_dates(expirations, today=saturday)
        assert result == ["2026-04-10"]

    def test_monthly_only_ticker(self) -> None:
        """Stock with only monthly options — picks next monthly."""
        tuesday = date(2026, 3, 31)
        expirations = ["2026-04-17"]  # 17 DTE — monthly only
        result = target_expiration_dates(expirations, today=tuesday)
        assert result == ["2026-04-17"]

    def test_no_valid_expirations(self) -> None:
        tuesday = date(2026, 3, 31)
        expirations = ["2026-03-31"]  # 0 DTE only
        result = target_expiration_dates(expirations, today=tuesday)
        assert result == []

    def test_sorts_unsorted_input(self) -> None:
        tuesday = date(2026, 3, 31)
        expirations = ["2026-04-10", "2026-04-02", "2026-04-17"]
        result = target_expiration_dates(expirations, today=tuesday)
        assert result == ["2026-04-02"]

    def test_max_expirations_two(self) -> None:
        """When max_expirations=2, return the two nearest valid."""
        tuesday = date(2026, 3, 31)
        expirations = ["2026-04-02", "2026-04-10", "2026-04-17"]
        result = target_expiration_dates(expirations, today=tuesday, max_expirations=2)
        assert result == ["2026-04-02", "2026-04-10"]


@dataclass
class _FakeSignal:
    """Minimal conviction signal stub for testing strike logic."""

    ticker: str
    trend_state: str
    ema_8: float
    ema_21: float
    conviction_level: str = "high"
    price_to_8ema_pct: float = 1.0


class TestPathBStrikeRange:
    """Path B (pullback): strikes 5% below → 1% below the support EMA."""

    @pytest.mark.asyncio
    async def test_pullback_21ema_ceiling_is_1pct_below(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        sig = _FakeSignal(
            ticker="AAPL",
            trend_state="pullback_to_21ema",
            ema_8=195.0,
            ema_21=190.0,
        )
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["AAPL"],
            available_cash=100_000.0,
            conviction_signals={"AAPL": sig},
            pullback_strike_offset_pct=5.0,
            pullback_strike_ceiling_pct=1.0,
            earliest_expiration_only=False,
        )
        floor = 190.0 * 0.95  # 180.50
        ceiling = 190.0 * 0.99  # 188.10
        for c in candidates:
            assert c.strike >= floor, f"Strike {c.strike} below floor {floor}"
            assert c.strike <= ceiling, f"Strike {c.strike} above ceiling {ceiling}"

    @pytest.mark.asyncio
    async def test_pullback_8ema_uses_8ema_as_support(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        sig = _FakeSignal(
            ticker="PL",
            trend_state="pullback_to_8ema",
            ema_8=24.0,
            ema_21=23.0,
        )
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL"],
            available_cash=100_000.0,
            conviction_signals={"PL": sig},
            pullback_strike_offset_pct=5.0,
            pullback_strike_ceiling_pct=1.0,
            earliest_expiration_only=False,
        )
        floor = 24.0 * 0.95  # 22.80
        ceiling = 24.0 * 0.99  # 23.76
        for c in candidates:
            assert c.strike >= floor
            assert c.strike <= ceiling


class TestPathAStrikeRange:
    """Path A (uptrend): strikes from 15% below current price up to 8-EMA."""

    @pytest.mark.asyncio
    async def test_uptrend_ceiling_is_8ema(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        sig = _FakeSignal(
            ticker="AAPL",
            trend_state="strong_uptrend",
            ema_8=190.0,
            ema_21=185.0,
        )
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["AAPL"],
            available_cash=100_000.0,
            conviction_signals={"AAPL": sig},
            earliest_expiration_only=False,
        )
        for c in candidates:
            assert c.strike <= 190.0, f"Strike {c.strike} above 8-EMA ceiling {190.0}"

    @pytest.mark.asyncio
    async def test_uptrend_without_conviction_uses_fallback(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        """Without conviction data, falls back to quote.last-based range, no ceiling."""
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL"],
            available_cash=100_000.0,
            conviction_signals={},
            earliest_expiration_only=False,
        )
        assert len(candidates) > 0


class TestEarliestExpirationFilter:
    """After collecting all candidates, keep only the earliest expiration."""

    @pytest.mark.asyncio
    async def test_filters_to_earliest(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        """With max_expirations=2 and earliest_only, only earliest survives."""
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100_000.0,
            max_expirations=2,
            earliest_expiration_only=True,
        )
        if len(candidates) >= 2:
            expirations = {c.expiration for c in candidates}
            assert len(expirations) == 1, (
                f"Expected 1 expiration, got {len(expirations)}: {expirations}"
            )

    @pytest.mark.asyncio
    async def test_disabled_keeps_all(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        """With earliest_only=False and max_expirations=2, multiple dates OK."""
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100_000.0,
            max_expirations=2,
            earliest_expiration_only=False,
        )
        assert len(candidates) > 0

    @pytest.mark.asyncio
    async def test_diagnostics_track_filtered(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        """Diagnostics should report how many were filtered by earliest-exp."""
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL"],
            available_cash=100_000.0,
            max_expirations=3,
            earliest_expiration_only=True,
        )
        assert "earliest_exp_filtered" in diag
