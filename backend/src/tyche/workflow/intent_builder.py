"""Converts morning scan results (LLM analyses) into OrderIntent DB records.

This is the bridge between the scan pipeline and the human-in-the-loop
approval workflow. Each CSPAnalysis from the LLM becomes a pending OrderIntent
that the user can review, approve, and later record as executed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import structlog
from sqlalchemy.ext.asyncio import AsyncSession

from tyche.conviction.engine import ConvictionSignal
from tyche.models.order_intent import OrderIntent
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()


@dataclass
class RiskVerdict:
    """Result of the deterministic risk gate evaluation."""

    passed: bool
    reasons: list[str]

    @property
    def summary(self) -> str:
        return " | ".join(self.reasons)


_CONVICTION_RANK = {"high": 3, "medium": 2, "low": 1, "none": 0}


def _resolve_numeric(
    field_name: str,
    symbol: str,
    llm_value: float,
    broker_value: float | None,
    tolerance_pct: float = 5.0,
) -> float:
    """Prefer broker-sourced data over LLM output for numerical fields.

    If the broker value exists, use it. Log a warning when the LLM value
    diverges significantly so we can improve prompts over time.
    """
    if broker_value is None or broker_value == 0:
        return llm_value

    if llm_value > 0 and broker_value > 0:
        divergence_pct = abs(llm_value - broker_value) / broker_value * 100
        if divergence_pct > tolerance_pct:
            logger.warning(
                "llm_broker_divergence",
                symbol=symbol,
                field=field_name,
                llm_value=round(llm_value, 4),
                broker_value=round(broker_value, 4),
                divergence_pct=round(divergence_pct, 1),
                using="broker",
            )

    return broker_value


def evaluate_risk(
    analysis: CSPAnalysis,
    signal: ConvictionSignal | None,
    candidate: ScoredCandidate | None,
) -> RiskVerdict:
    """Deterministic risk gate that must pass before an intent is surfaced.

    Checks:
      1. LLM confidence must be medium or high
      2. Assignment comfort must be medium or high
      3. Conviction engine must mark the ticker CSP-eligible
      4. Strike must be OTM (below underlying) for puts
      5. Price must not be >15% extended above 8-EMA (blow-off risk)
    """
    reasons: list[str] = []
    failed = False

    ema_conv = signal.conviction_level if signal else "none"
    reasons.append(f"EMA conviction: {ema_conv}")
    reasons.append(f"LLM confidence: {analysis.confidence}")

    if analysis.confidence == "low":
        reasons.append("FAIL: LLM confidence is low")
        failed = True

    if analysis.assignment_comfort == "low":
        reasons.append("FAIL: Assignment comfort is low")
        failed = True
    else:
        reasons.append(f"Comfort: {analysis.assignment_comfort}")

    if signal:
        if not signal.csp_eligible:
            reasons.append(f"FAIL: Not CSP-eligible (trend: {signal.trend_state.value})")
            failed = True
        else:
            reasons.append(f"CSP eligible ({signal.trend_state.value})")

        if signal.price_to_8ema_pct > 15.0:
            reasons.append(
                f"FAIL: Price {signal.price_to_8ema_pct:.1f}% above 8-EMA (blow-off risk)"
            )
            failed = True
        elif signal.price_to_8ema_pct > 10.0:
            reasons.append(
                f"WARN: Price {signal.price_to_8ema_pct:.1f}% above 8-EMA (extended)"
            )

    if candidate:
        underlying = candidate.underlying_price
        if underlying > 0 and analysis.recommended_strike >= underlying:
            reasons.append(
                f"FAIL: Strike ${analysis.recommended_strike:.2f} >= underlying "
                f"${underlying:.2f} (ITM)"
            )
            failed = True
        elif underlying > 0:
            buffer_pct = ((underlying - analysis.recommended_strike) / underlying) * 100
            reasons.append(f"OTM buffer: {buffer_pct:.1f}%")

    return RiskVerdict(passed=not failed, reasons=reasons)


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

        verdict = evaluate_risk(analysis, signal, candidate)

        ema_conviction = signal.conviction_level if signal else "none"
        llm_confidence = analysis.confidence
        trend_state = signal.trend_state.value if signal else "unknown"

        ema_rank = _CONVICTION_RANK.get(ema_conviction, 0)
        llm_rank = _CONVICTION_RANK.get(llm_confidence, 0)
        conviction_level = ema_conviction if ema_rank <= llm_rank else llm_confidence

        strike = _resolve_numeric(
            "strike", analysis.ticker,
            llm_value=analysis.recommended_strike,
            broker_value=candidate.strike if candidate else None,
        )
        premium_per_share = _resolve_numeric(
            "premium", analysis.ticker,
            llm_value=analysis.target_premium,
            broker_value=candidate.mid if candidate else None,
        )
        if strike > 0 and premium_per_share > strike:
            logger.warning(
                "premium_unit_correction",
                symbol=analysis.ticker,
                raw_premium=premium_per_share,
                strike=strike,
            )
            premium_per_share = premium_per_share / 100

        quantity = analysis.suggested_contracts
        collateral = _resolve_numeric(
            "collateral", analysis.ticker,
            llm_value=analysis.collateral_required,
            broker_value=candidate.collateral_required if candidate else None,
        )
        ann_return = _resolve_numeric(
            "annualized_return", analysis.ticker,
            llm_value=analysis.annualized_return_pct,
            broker_value=candidate.annualized_return_pct if candidate else None,
        )
        expiration = (
            candidate.expiration.isoformat()
            if candidate
            else analysis.recommended_expiration
        )

        intent = OrderIntent(
            id=str(uuid.uuid4()),
            created_at=now,
            updated_at=now,
            status="pending",
            symbol=analysis.ticker,
            option_symbol=candidate.option_symbol if candidate else None,
            side="sell_to_open",
            strategy="csp",
            strike=strike,
            expiration=expiration,
            quantity=quantity,
            limit_price=premium_per_share,
            estimated_premium=premium_per_share * quantity * 100,
            collateral_required=collateral,
            annualized_return_pct=ann_return,
            conviction_level=conviction_level,
            trend_state=trend_state,
            thesis=analysis.thesis,
            risks=" | ".join(analysis.risks) if analysis.risks else None,
            invalidation=analysis.invalidation,
            risk_passed=verdict.passed,
            risk_summary=verdict.summary,
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
            conviction=conviction_level,
            risk_passed=verdict.passed,
            risk_reasons=verdict.reasons,
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
