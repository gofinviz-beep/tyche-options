"""Converts morning scan results (LLM analyses) into OrderIntent DB records.

This is the bridge between the scan pipeline and the human-in-the-loop
approval workflow. Each CSPAnalysis from the LLM becomes a pending OrderIntent
that the user can review, approve, and later record as executed.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tyche.conviction.engine import ConvictionSignal
from tyche.models.order_intent import OrderIntent
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()


async def create_intents_from_scan(
    session: AsyncSession,
    scan_id: str,
    csp_analyses: list[CSPAnalysis],
    csp_candidates: list[ScoredCandidate],
    conviction_signals: dict[str, ConvictionSignal],
) -> list[OrderIntent]:
    """Create pending OrderIntent records from LLM analyses.

    For each LLM analysis, we match it with the best scored candidate
    to populate option-level details (option_symbol, bid/ask, etc).

    Args:
        session: Active DB session (caller manages commit)
        scan_id: ID of the scan that produced these results
        csp_analyses: LLM-produced analysis recommendations
        csp_candidates: Scored option candidates from the strategy engine
        conviction_signals: EMA conviction data keyed by ticker

    Returns:
        List of created (but not yet committed) OrderIntent objects
    """
    candidates_by_ticker: dict[str, ScoredCandidate] = {}
    for c in csp_candidates:
        if c.symbol not in candidates_by_ticker or c.score > candidates_by_ticker[c.symbol].score:
            candidates_by_ticker[c.symbol] = c

    now = datetime.now(timezone.utc)
    intents: list[OrderIntent] = []

    for analysis in csp_analyses:
        candidate = candidates_by_ticker.get(analysis.ticker)
        signal = conviction_signals.get(analysis.ticker)

        intent = OrderIntent(
            id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            status="pending",
            symbol=analysis.ticker,
            option_symbol=candidate.option_symbol if candidate else None,
            side="sell_to_open",
            strategy="csp",
            strike=analysis.recommended_strike,
            expiration=analysis.recommended_expiration,
            quantity=analysis.suggested_contracts,
            limit_price=analysis.target_premium,
            estimated_premium=analysis.target_premium * analysis.suggested_contracts * 100,
            collateral_required=analysis.collateral_required,
            annualized_return_pct=analysis.annualized_return_pct,
            conviction_level=signal.conviction_level if signal else analysis.confidence,
            trend_state=signal.trend_state.value if signal else "unknown",
            thesis=analysis.thesis,
            risks=" | ".join(analysis.risks) if analysis.risks else None,
            invalidation=analysis.invalidation,
            risk_passed=True,
            risk_summary=f"Confidence: {analysis.confidence}, Comfort: {analysis.assignment_comfort}",
            scan_id=scan_id,
        )

        session.add(intent)
        intents.append(intent)

        logger.info(
            "intent_created",
            intent_id=intent.id,
            symbol=intent.symbol,
            strike=intent.strike,
            contracts=intent.quantity,
            premium=intent.estimated_premium,
            conviction=intent.conviction_level,
        )

    if intents:
        await session.commit()
        logger.info(
            "intents_batch_created",
            scan_id=scan_id,
            count=len(intents),
            symbols=[i.symbol for i in intents],
        )

    return intents


async def create_manual_intent(
    session: AsyncSession,
    symbol: str,
    strike: float,
    expiration: str,
    quantity: int,
    limit_price: float | None = None,
    strategy: str = "csp",
    side: str = "sell_to_open",
    conviction_level: str = "none",
    trend_state: str = "unknown",
    thesis: str | None = None,
) -> OrderIntent:
    """Create a single OrderIntent manually (not from a scan).

    Args:
        session: Active DB session
        symbol: Underlying ticker
        strike: Option strike price
        expiration: Expiration date string (YYYY-MM-DD)
        quantity: Number of contracts
        limit_price: Target per-contract premium
        strategy: Trading strategy (csp, covered_call)
        side: Order side (sell_to_open, etc.)
        conviction_level: Manual conviction assessment
        trend_state: Manual trend state
        thesis: Why this trade

    Returns:
        The created OrderIntent
    """
    now = datetime.now(timezone.utc)
    est_premium = (limit_price or 0) * quantity * 100
    collateral = strike * quantity * 100

    intent = OrderIntent(
        id=str(uuid.uuid4()),
        created_at=now,
        updated_at=now,
        status="pending",
        symbol=symbol.upper(),
        side=side,
        strategy=strategy,
        strike=strike,
        expiration=expiration,
        quantity=quantity,
        limit_price=limit_price,
        estimated_premium=est_premium,
        collateral_required=collateral,
        annualized_return_pct=0.0,
        conviction_level=conviction_level,
        trend_state=trend_state,
        thesis=thesis,
        risk_passed=False,
        risk_summary="Manual entry — risk not evaluated",
    )

    session.add(intent)
    await session.commit()
    await session.refresh(intent)

    logger.info(
        "manual_intent_created",
        intent_id=intent.id,
        symbol=intent.symbol,
        strike=intent.strike,
        quantity=intent.quantity,
    )

    return intent
