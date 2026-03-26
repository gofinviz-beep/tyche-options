"""Tests for the intent builder — scan results → OrderIntent records."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.conviction.engine import ConvictionSignal, TrendState
from tyche.models.order_intent import OrderIntent
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.workflow.intent_builder import (
    create_intents_from_scan,
    create_manual_intent,
)


def _make_analysis(
    ticker: str = "PL",
    strike: float = 23.0,
    premium: float = 1.80,
    contracts: int = 40,
    confidence: str = "high",
) -> CSPAnalysis:
    return CSPAnalysis(
        ticker=ticker,
        assignment_comfort=confidence,
        assignment_comfort_reasoning="Strong conviction",
        thesis=f"Bullish on {ticker}",
        recommended_strike=strike,
        recommended_expiration="2026-04-03",
        target_premium=premium,
        annualized_return_pct=22.5,
        invalidation="Breakdown below support",
        confidence=confidence,
        risks=["Market crash", "Earnings miss"],
        would_you_hold_if_assigned="Yes",
        suggested_contracts=contracts,
        collateral_required=strike * contracts * 100,
        allocation_mode="concentrated",
    )


def _make_candidate(
    symbol: str = "PL",
    strike: float = 23.0,
    score: float = 85.0,
) -> ScoredCandidate:
    return ScoredCandidate(
        symbol=symbol,
        option_symbol=f"{symbol}260403P{int(strike * 1000):08d}",
        option_type="put",
        strike=strike,
        expiration=date(2026, 4, 3),
        dte=11,
        bid=1.75,
        ask=1.85,
        mid=1.80,
        underlying_price=strike + 1.5,
        strategy="csp",
        premium_per_contract=175.0,
        collateral_required=strike * 100,
        annualized_return_pct=22.5,
        score=score,
        delta=-0.25,
        theta=0.05,
        implied_volatility=0.45,
        volume=500,
        open_interest=2000,
        earnings_within_dte=False,
    )


def _make_signal(
    ticker: str = "PL",
    conviction: str = "high",
    trend: TrendState = TrendState.STRONG_UPTREND,
) -> ConvictionSignal:
    return ConvictionSignal(
        ticker=ticker,
        trend_state=trend,
        conviction_level=conviction,
        csp_eligible=True,
        last_close=24.5,
        ema_8=24.2,
        ema_21=23.8,
        ema_8_slope=0.15,
        ema_21_slope=0.08,
        price_to_8ema_pct=1.2,
        price_to_21ema_pct=2.9,
        volume_declining_on_pullback=False,
        avg_volume_20d=5_000_000,
        latest_volume=4_800_000,
        days_above_both_emas=12,
        as_of_date=date(2026, 3, 21),
    )


def _mock_session():
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.refresh = AsyncMock()
    return session


class TestCreateIntentsFromScan:

    @pytest.mark.asyncio
    async def test_creates_intents_from_analyses(self):
        session = _mock_session()

        analyses = [_make_analysis("PL"), _make_analysis("AAPL", strike=175.0)]
        candidates = [_make_candidate("PL"), _make_candidate("AAPL", strike=175.0)]
        signals = {
            "PL": _make_signal("PL"),
            "AAPL": _make_signal("AAPL"),
        }

        intents = await create_intents_from_scan(
            session=session,
            scan_id="scan-123",
            csp_analyses=analyses,
            csp_candidates=candidates,
            conviction_signals=signals,
        )

        assert len(intents) == 2
        assert session.add.call_count == 2
        session.commit.assert_awaited_once()

        pl_intent = next(i for i in intents if i.symbol == "PL")
        assert pl_intent.status == "pending"
        assert pl_intent.strategy == "csp"
        assert pl_intent.side == "sell_to_open"
        assert pl_intent.strike == 23.0
        assert pl_intent.quantity == 40
        assert pl_intent.scan_id == "scan-123"
        assert pl_intent.conviction_level == "high"
        assert pl_intent.trend_state == "strong_uptrend"
        assert pl_intent.thesis == "Bullish on PL"
        assert "Market crash" in pl_intent.risks
        assert pl_intent.estimated_premium == 1.80 * 40 * 100

    @pytest.mark.asyncio
    async def test_empty_analyses_returns_empty(self):
        session = _mock_session()

        intents = await create_intents_from_scan(
            session=session,
            scan_id="scan-empty",
            csp_analyses=[],
            csp_candidates=[],
            conviction_signals={},
        )

        assert intents == []
        session.commit.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_missing_candidate(self):
        session = _mock_session()

        analyses = [_make_analysis("NVDA", strike=500.0)]
        intents = await create_intents_from_scan(
            session=session,
            scan_id="scan-no-candidate",
            csp_analyses=analyses,
            csp_candidates=[],
            conviction_signals={},
        )

        assert len(intents) == 1
        assert intents[0].option_symbol is None
        assert intents[0].conviction_level == "high"

    @pytest.mark.asyncio
    async def test_picks_best_candidate_by_score(self):
        session = _mock_session()

        analyses = [_make_analysis("PL")]
        low_score = _make_candidate("PL", strike=22.0, score=60.0)
        high_score = _make_candidate("PL", strike=23.0, score=90.0)

        intents = await create_intents_from_scan(
            session=session,
            scan_id="scan-score",
            csp_analyses=analyses,
            csp_candidates=[low_score, high_score],
            conviction_signals={},
        )

        assert intents[0].option_symbol == high_score.option_symbol


class TestCreateManualIntent:

    @pytest.mark.asyncio
    async def test_creates_manual_intent(self):
        session = _mock_session()

        intent = await create_manual_intent(
            session=session,
            symbol="PL",
            strike=23.0,
            expiration="2026-04-03",
            quantity=40,
            limit_price=1.80,
            thesis="Strong earnings ahead",
        )

        assert intent.symbol == "PL"
        assert intent.strike == 23.0
        assert intent.quantity == 40
        assert intent.status == "pending"
        assert intent.strategy == "csp"
        assert intent.estimated_premium == 1.80 * 40 * 100
        assert intent.collateral_required == 23.0 * 40 * 100
        assert intent.risk_summary == "Manual entry — risk not evaluated"
        session.add.assert_called_once()
        session.commit.assert_awaited_once()
        session.refresh.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_uppercases_symbol(self):
        session = _mock_session()

        intent = await create_manual_intent(
            session=session,
            symbol="pl",
            strike=23.0,
            expiration="2026-04-03",
            quantity=10,
        )

        assert intent.symbol == "PL"
