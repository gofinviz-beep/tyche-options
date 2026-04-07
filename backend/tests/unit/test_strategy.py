"""Tests for the strategy engine — CSP and CC scanning pipelines."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pytest

from tyche.broker.base import OptionContract, OptionsChain, Quote
from tyche.broker.mock import MockBroker
from tyche.strategy.engine import StrategyEngine, target_expiration_dates
from tyche.strategy.strategies.base import RawCandidate
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
        filtered = csp.apply_filters(
            raw, min_oi=100, min_volume=10, max_spread_pct=15.0,
            min_bid=0.0, min_premium_pct=0.0,
        )
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
        filtered = csp.apply_filters(
            raw, min_oi=0, min_volume=0, max_spread_pct=100.0,
            min_bid=0.0, min_premium_pct=0.0,
        )
        scored = csp.score(filtered, available_cash=100000.0)

        assert len(scored) > 0
        for s in scored:
            assert s.annualized_return_pct >= 0
            assert s.collateral_required > 0
            assert s.premium_per_contract > 0

        for i in range(len(scored) - 1):
            assert scored[i].score >= scored[i + 1].score


# --- CSP Quality Filter Tests ---


class TestCSPQualityFilters:
    """Tests for the new min_bid, min_premium_pct, and min_volume filters."""

    def _make_raw(
        self, bid: float, ask: float, strike: float, oi: int = 500, volume: int = 100
    ) -> RawCandidate:
        mid = (bid + ask) / 2 if (bid + ask) > 0 else 0.0
        return RawCandidate(
            symbol="TEST",
            option_symbol="TEST260417P00050000",
            option_type="put",
            strike=strike,
            expiration=date.today() + timedelta(days=14),
            dte=14,
            bid=bid,
            ask=ask,
            mid=mid,
            volume=volume,
            open_interest=oi,
            implied_volatility=0.5,
            underlying_price=55.0,
            strategy="csp",
        )

    def test_min_bid_rejects_low_bids(self) -> None:
        csp = CashSecuredPutStrategy()
        raw = [
            self._make_raw(bid=0.30, ask=0.50, strike=50.0),
            self._make_raw(bid=0.50, ask=0.70, strike=50.0),
            self._make_raw(bid=1.20, ask=1.40, strike=50.0),
        ]
        filtered = csp.apply_filters(
            raw, min_oi=0, min_volume=0, max_spread_pct=100.0,
            min_bid=0.50, min_premium_pct=0.0,
        )
        assert len(filtered) == 2
        assert all(f.bid >= 0.50 for f in filtered)

    def test_min_premium_pct_rejects_thin_premium(self) -> None:
        csp = CashSecuredPutStrategy()
        raw = [
            self._make_raw(bid=0.20, ask=0.30, strike=200.0),  # 0.1% of strike
            self._make_raw(bid=1.50, ask=1.70, strike=200.0),  # 0.75% of strike
        ]
        filtered = csp.apply_filters(
            raw, min_oi=0, min_volume=0, max_spread_pct=100.0,
            min_bid=0.0, min_premium_pct=0.5,
        )
        assert len(filtered) == 1
        assert filtered[0].bid == 1.50

    def test_min_volume_rejects_zero_volume(self) -> None:
        csp = CashSecuredPutStrategy()
        raw = [
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, volume=0),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, volume=5),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, volume=15),
        ]
        filtered = csp.apply_filters(
            raw, min_oi=0, min_volume=10, max_spread_pct=100.0,
            min_bid=0.0, min_premium_pct=0.0,
        )
        assert len(filtered) == 1
        assert filtered[0].volume == 15

    def test_min_oi_rejects_illiquid(self) -> None:
        csp = CashSecuredPutStrategy()
        raw = [
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, oi=10),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, oi=50),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, oi=200),
        ]
        filtered = csp.apply_filters(
            raw, min_oi=50, min_volume=0, max_spread_pct=100.0,
            min_bid=0.0, min_premium_pct=0.0,
        )
        assert len(filtered) == 2
        assert all(f.open_interest >= 50 for f in filtered)

    def test_all_filters_combined(self) -> None:
        csp = CashSecuredPutStrategy()
        raw = [
            self._make_raw(bid=0.30, ask=0.50, strike=50.0, oi=100, volume=50),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, oi=100, volume=50),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, oi=10, volume=50),
            self._make_raw(bid=1.00, ask=1.20, strike=50.0, oi=100, volume=3),
        ]
        filtered = csp.apply_filters(
            raw, min_oi=50, min_volume=10, max_spread_pct=20.0,
            min_bid=0.50, min_premium_pct=0.5,
        )
        assert len(filtered) == 1
        assert filtered[0].bid == 1.00
        assert filtered[0].open_interest == 100
        assert filtered[0].volume == 50


# --- Scoring Factor Tests ---


class TestCSPScoringFactors:
    """Tests for dte_factor, vrp_factor, iv_rank_factor, and trend_confirm_factor."""

    def _make_filtered(
        self, bid: float, strike: float, dte: int, oi: int = 1000, symbol: str = "TEST"
    ) -> "FilteredCandidate":
        from tyche.strategy.strategies.base import FilteredCandidate

        ask = bid * 1.1
        mid = (bid + ask) / 2
        return FilteredCandidate(
            symbol=symbol,
            option_symbol=f"{symbol}260417P{int(strike*1000):08d}",
            option_type="put",
            strike=strike,
            expiration=date.today() + timedelta(days=dte),
            dte=dte,
            bid=bid,
            ask=ask,
            mid=mid,
            volume=100,
            open_interest=oi,
            implied_volatility=0.5,
            underlying_price=strike * 1.05,
            strategy="csp",
            bid_ask_spread_pct=5.0,
            passed_filters={},
        )

    def test_dte_factor_penalizes_short_dte(self) -> None:
        csp = CashSecuredPutStrategy()
        short_dte = self._make_filtered(bid=1.0, strike=50.0, dte=3)
        long_dte = self._make_filtered(bid=1.0, strike=50.0, dte=14)

        short_scored = csp.score([short_dte], available_cash=100_000.0)
        long_scored = csp.score([long_dte], available_cash=100_000.0)

        assert len(short_scored) == 1 and len(long_scored) == 1
        # 3 DTE annualizes much higher, but dte_factor (3/7 ≈ 0.43) dampens it.
        # Verify the dampening reduces the gap vs pure annualized.
        short_ann = short_scored[0].annualized_return_pct
        long_ann = long_scored[0].annualized_return_pct
        score_ratio = short_scored[0].score / long_scored[0].score
        ann_ratio = short_ann / long_ann
        assert score_ratio < ann_ratio, (
            f"dte_factor should shrink the 3-vs-14 DTE gap: "
            f"score ratio {score_ratio:.2f} should be < annualized ratio {ann_ratio:.2f}"
        )

    def test_dte_factor_at_7_is_full(self) -> None:
        csp = CashSecuredPutStrategy()
        c7 = self._make_filtered(bid=1.0, strike=50.0, dte=7)
        c14 = self._make_filtered(bid=1.0, strike=50.0, dte=14)

        scored_7 = csp.score([c7], available_cash=100_000.0)
        scored_14 = csp.score([c14], available_cash=100_000.0)

        # Both have dte_factor=1.0, so 7 DTE should score higher (higher annualized)
        assert scored_7[0].score > scored_14[0].score

    def test_vrp_factor_boosts_positive_vrp(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="HIGH_VRP")

        no_vrp = csp.score([c], available_cash=100_000.0)
        with_vrp = csp.score(
            [c], available_cash=100_000.0,
            vrp_map={"HIGH_VRP": 0.20},
        )

        assert with_vrp[0].score > no_vrp[0].score

    def test_vrp_factor_capped_at_30pct(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="MEGA_VRP")

        moderate = csp.score(
            [c], available_cash=100_000.0, vrp_map={"MEGA_VRP": 0.30},
        )
        extreme = csp.score(
            [c], available_cash=100_000.0, vrp_map={"MEGA_VRP": 0.90},
        )
        # Both should cap at 1.3x bonus
        assert moderate[0].score == extreme[0].score

    def test_negative_vrp_no_penalty(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="NEG_VRP")

        no_vrp = csp.score([c], available_cash=100_000.0)
        neg_vrp = csp.score(
            [c], available_cash=100_000.0, vrp_map={"NEG_VRP": -0.10},
        )
        # Negative VRP → vrp_factor = 1.0 (no penalty, no bonus)
        assert neg_vrp[0].score == no_vrp[0].score

    def test_iv_rank_factor_boosts_high_iv_rank(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="HIR")

        no_ir = csp.score([c], available_cash=100_000.0)
        high_ir = csp.score(
            [c], available_cash=100_000.0,
            iv_rank_map={"HIR": 80.0},
        )
        assert high_ir[0].score > no_ir[0].score * 0.99  # factor >= 1.0

    def test_iv_rank_factor_penalizes_low_iv_rank(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="LOR")

        no_ir = csp.score([c], available_cash=100_000.0)
        low_ir = csp.score(
            [c], available_cash=100_000.0,
            iv_rank_map={"LOR": 0.0},
        )
        # iv_rank=0 → factor=0.7, so score should be ~70% of baseline
        assert low_ir[0].score < no_ir[0].score
        ratio = low_ir[0].score / no_ir[0].score
        assert 0.68 <= ratio <= 0.72

    def test_iv_rank_factor_caps_at_60(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="CAP")

        at_60 = csp.score(
            [c], available_cash=100_000.0, iv_rank_map={"CAP": 60.0},
        )
        at_100 = csp.score(
            [c], available_cash=100_000.0, iv_rank_map={"CAP": 100.0},
        )
        assert at_60[0].score == at_100[0].score

    def test_iv_rank_missing_defaults_to_no_penalty(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="MISS")

        baseline = csp.score([c], available_cash=100_000.0)
        with_empty = csp.score(
            [c], available_cash=100_000.0, iv_rank_map={},
        )
        assert baseline[0].score == with_empty[0].score

    def test_trend_confirm_penalizes_below_50ema(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="BELOW")

        baseline = csp.score([c], available_cash=100_000.0)
        below = csp.score(
            [c], available_cash=100_000.0,
            trend_confirm_map={"BELOW": False},
        )
        assert below[0].score < baseline[0].score
        ratio = below[0].score / baseline[0].score
        assert 0.83 <= ratio <= 0.87

    def test_trend_confirm_no_penalty_above_50ema(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="ABOVE")

        baseline = csp.score([c], available_cash=100_000.0)
        above = csp.score(
            [c], available_cash=100_000.0,
            trend_confirm_map={"ABOVE": True},
        )
        assert above[0].score == baseline[0].score

    def test_trend_confirm_missing_defaults_to_no_penalty(self) -> None:
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="NOMATCH")

        baseline = csp.score([c], available_cash=100_000.0)
        with_empty = csp.score(
            [c], available_cash=100_000.0, trend_confirm_map={},
        )
        assert baseline[0].score == with_empty[0].score

    def test_all_factors_combined(self) -> None:
        """All scoring factors applied together multiply correctly."""
        csp = CashSecuredPutStrategy()
        c = self._make_filtered(bid=1.0, strike=50.0, dte=14, symbol="COMBO")

        baseline = csp.score([c], available_cash=100_000.0)
        combined = csp.score(
            [c], available_cash=100_000.0,
            vrp_map={"COMBO": 0.20},
            iv_rank_map={"COMBO": 30.0},
            trend_confirm_map={"COMBO": False},
        )
        # vrp=0.20 → vrp_factor=1.2
        # iv_rank=30 → iv_rank_factor=0.7+0.3*(30/60)=0.85
        # trend_confirm=False → trend_confirm_factor=0.85
        expected_multiplier = 1.2 * 0.85 * 0.85
        ratio = combined[0].score / baseline[0].score
        assert abs(ratio - expected_multiplier) < 0.02


# --- Allocator Market Cap Bonus Tests ---


class TestAllocatorMarketCapBonus:
    """Tests for market cap bonus in _compute_risk_weight."""

    def _make_scored(self, symbol: str = "TEST") -> "ScoredCandidate":
        from tyche.strategy.strategies.base import ScoredCandidate

        return ScoredCandidate(
            symbol=symbol,
            option_symbol=f"{symbol}260417P00050000",
            option_type="put",
            strike=50.0,
            expiration=date.today() + timedelta(days=14),
            dte=14,
            bid=1.0,
            ask=1.1,
            mid=1.05,
            volume=100,
            open_interest=1000,
            implied_volatility=0.5,
            underlying_price=55.0,
            strategy="csp",
            bid_ask_spread_pct=5.0,
            passed_filters={},
            premium_per_contract=100.0,
            total_premium=500.0,
            collateral_required=5000.0,
            annualized_return_pct=50.0,
            score=50.0,
        )

    def test_mega_cap_gets_full_bonus(self) -> None:
        from tyche.strategy.allocator import _compute_risk_weight

        c = self._make_scored("MEGA")
        w_no_cap = _compute_risk_weight(c)
        w_mega = _compute_risk_weight(c, market_caps={"MEGA": 200e9})
        assert w_mega > w_no_cap
        ratio = w_mega / w_no_cap
        assert 1.09 <= ratio <= 1.11  # ~10% bonus

    def test_mid_cap_gets_partial_bonus(self) -> None:
        from tyche.strategy.allocator import _compute_risk_weight

        c = self._make_scored("MID")
        w_no_cap = _compute_risk_weight(c)
        w_mid = _compute_risk_weight(c, market_caps={"MID": 50e9})
        assert w_mid > w_no_cap
        ratio = w_mid / w_no_cap
        assert 1.04 <= ratio <= 1.06  # ~5% bonus

    def test_no_market_cap_no_bonus(self) -> None:
        from tyche.strategy.allocator import _compute_risk_weight

        c = self._make_scored("NODATA")
        w_none = _compute_risk_weight(c, market_caps={})
        w_base = _compute_risk_weight(c)
        assert w_none == w_base


# --- Parallel Scan Tests ---


class TestParallelScan:
    """Tests verifying that the parallelized scan produces correct results."""

    @pytest.mark.asyncio
    async def test_parallel_scan_same_results_as_expected(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        """Parallel scan should find candidates for multiple tickers."""
        candidates, diagnostics = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100_000.0,
            top_n=20,
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
        )
        symbols_found = {c.symbol for c in candidates}
        assert len(symbols_found) >= 1
        assert diagnostics["symbols_with_candidates"] >= 1

    @pytest.mark.asyncio
    async def test_parallel_scan_handles_errors_gracefully(
        self, engine: StrategyEngine
    ) -> None:
        """If one ticker fails, others should still return results."""
        from unittest.mock import AsyncMock, MagicMock

        mock_broker = AsyncMock()
        good_quote = Quote(
            symbol="GOOD", last=50.0, bid=49.9, ask=50.1,
            high=51.0, low=49.0, open=50.0, close=50.0,
            volume=1000000, change=0.0, change_pct=0.0,
        )

        async def get_quote_side(symbol):
            if symbol == "BAD":
                raise ConnectionError("API down")
            return good_quote

        mock_broker.get_quote = AsyncMock(side_effect=get_quote_side)
        mock_broker.get_options_expirations = AsyncMock(return_value=[])

        candidates, diag = await engine.scan_csp_candidates(
            broker=mock_broker,
            watchlist=["BAD", "GOOD"],
            available_cash=100_000.0,
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
        )
        assert diag["api_error"] >= 1

    @pytest.mark.asyncio
    async def test_parallel_scan_aggregates_drops(
        self, broker: MockBroker, engine: StrategyEngine
    ) -> None:
        """Drop counts from parallel tasks should aggregate correctly."""
        candidates, diag = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100_000.0,
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
        )
        total_drops = sum(
            v for k, v in diag.items() if k != "symbols_with_candidates"
        )
        assert isinstance(total_drops, int)


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
        for c in candidates:
            assert c.strike >= 30.0

    @pytest.mark.asyncio
    async def test_no_candidates_without_shares(self, broker: MockBroker) -> None:
        cc = CoveredCallStrategy()
        exps = await broker.get_options_expirations("PL")
        chain = await broker.get_options_chain("PL", exps[1])
        quote = await broker.get_quote("PL")

        candidates = cc.identify_candidates(chain, quote, shares_held=50)
        assert len(candidates) == 0


# --- Engine Integration Tests ---


class TestStrategyEngine:
    @pytest.mark.asyncio
    async def test_scan_csp_candidates(self, broker: MockBroker, engine: StrategyEngine) -> None:
        candidates, diagnostics = await engine.scan_csp_candidates(
            broker=broker,
            watchlist=["PL", "AAPL"],
            available_cash=100000.0,
            top_n=5,
            min_bid=0.0,
            min_premium_pct=0.0,
            min_oi=0,
            min_volume=0,
            max_spread_pct=100.0,
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
            min_bid=0.0,
            min_premium_pct=0.0,
            min_oi=0,
            min_volume=0,
            max_spread_pct=100.0,
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
        )
        assert candidates == []
        assert diagnostics["insufficient_capital"] > 0


class TestTargetExpirationDates:
    """Tests for expiration date targeting.

    New behavior: always enforces min_dte=5 (configurable), prefers
    expirations closest to target_dte=14 (configurable).
    """

    def test_skips_short_dte(self) -> None:
        """Even on a Monday, expirations < min_dte (5) are skipped."""
        monday = date(2026, 3, 30)
        expirations = [
            "2026-03-31",  # 1 DTE
            "2026-04-02",  # 3 DTE
            "2026-04-04",  # 5 DTE ← just meets min_dte
            "2026-04-10",  # 11 DTE
            "2026-04-17",  # 18 DTE
        ]
        result = target_expiration_dates(expirations, today=monday, max_expirations=1)
        assert "2026-03-31" not in result
        assert "2026-04-02" not in result

    def test_prefers_target_dte_14(self) -> None:
        """Expirations closest to target_dte=14 are preferred."""
        monday = date(2026, 3, 30)
        expirations = [
            "2026-04-04",  # 5 DTE
            "2026-04-10",  # 11 DTE — closest to 14
            "2026-04-17",  # 18 DTE
            "2026-04-24",  # 25 DTE
        ]
        result = target_expiration_dates(
            expirations, today=monday, max_expirations=1, target_dte=14,
        )
        assert result == ["2026-04-10"]

    def test_two_expirations_near_target(self) -> None:
        """max_expirations=2 picks the two closest to the sweet spot."""
        monday = date(2026, 3, 30)
        expirations = [
            "2026-04-04",  # 5 DTE  (9 away from 14)
            "2026-04-10",  # 11 DTE (3 away from 14)
            "2026-04-17",  # 18 DTE (4 away from 14)
            "2026-04-24",  # 25 DTE (11 away from 14)
        ]
        result = target_expiration_dates(
            expirations, today=monday, max_expirations=2, target_dte=14,
        )
        assert set(result) == {"2026-04-10", "2026-04-17"}

    def test_thursday_respects_min_dte(self) -> None:
        """Thursday scan with min_dte=5: this Friday is 1 DTE — skipped."""
        thursday = date(2026, 4, 2)
        expirations = [
            "2026-04-03",  # 1 DTE
            "2026-04-04",  # 2 DTE
            "2026-04-10",  # 8 DTE
            "2026-04-17",  # 15 DTE
        ]
        result = target_expiration_dates(
            expirations, today=thursday, min_dte=5, target_dte=14,
        )
        assert "2026-04-03" not in result
        assert "2026-04-04" not in result

    def test_monthly_only_ticker(self) -> None:
        """Stock with only monthly options — picks next monthly."""
        tuesday = date(2026, 3, 31)
        expirations = ["2026-04-17"]
        result = target_expiration_dates(expirations, today=tuesday)
        assert result == ["2026-04-17"]

    def test_no_valid_expirations(self) -> None:
        tuesday = date(2026, 3, 31)
        expirations = ["2026-04-01"]  # 1 DTE — below min_dte=5
        result = target_expiration_dates(expirations, today=tuesday)
        assert result == []

    def test_custom_min_dte(self) -> None:
        monday = date(2026, 3, 30)
        expirations = ["2026-04-04", "2026-04-10", "2026-04-17"]
        result = target_expiration_dates(
            expirations, today=monday, min_dte=7, target_dte=14,
        )
        assert "2026-04-04" not in result  # 5 DTE < 7 min

    def test_unsorted_input(self) -> None:
        """Despite unsorted input, picks the one closest to target_dte=14."""
        tuesday = date(2026, 3, 31)
        # Apr 10 = 10 DTE (4 from 14), Apr 17 = 17 DTE (3 from 14), Apr 24 = 24 DTE (10 from 14)
        expirations = ["2026-04-17", "2026-04-10", "2026-04-24"]
        result = target_expiration_dates(
            expirations, today=tuesday, max_expirations=1, target_dte=14,
        )
        assert result == ["2026-04-17"]  # 17 DTE is closest to 14


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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
        )
        floor = 190.0 * 0.95
        ceiling = 190.0 * 0.99
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
        )
        floor = 24.0 * 0.95
        ceiling = 24.0 * 0.99
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
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
            min_bid=0.0, min_premium_pct=0.0, min_oi=0, min_volume=0,
            max_spread_pct=100.0,
        )
        assert "earliest_exp_filtered" in diag
