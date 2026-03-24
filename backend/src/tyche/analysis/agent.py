"""Analysis orchestration — connects strategy candidates to LLM reasoning."""

from __future__ import annotations

import json
from typing import Any

import structlog

from tyche.analysis.client import GeminiClient
from tyche.analysis.prompts import (
    SYSTEM_PROMPT_CSP,
    SYSTEM_PROMPT_ORDER_MONITOR,
    SYSTEM_PROMPT_POSITION_REVIEW,
    build_csp_analysis_prompt,
    build_order_monitor_prompt,
    build_position_review_prompt,
)
from tyche.broker.base import AccountBalance, BrokerOrder, BrokerPosition, Quote
from tyche.schemas.analysis import (
    CSPAnalysis,
    CoveredCallAnalysis,
    OrderMonitorAnalysis,
)
from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()


class AnalysisAgent:
    """Orchestrates LLM analysis for trade candidates and order monitoring."""

    def __init__(self, gemini: GeminiClient) -> None:
        self._gemini = gemini

    async def analyze_csp_candidates(
        self,
        candidates: list[ScoredCandidate],
        balance: AccountBalance,
        positions: list[BrokerPosition],
        earnings_context: dict[str, Any] | None = None,
    ) -> list[CSPAnalysis]:
        """Analyze CSP candidates through the LLM for ranking and reasoning."""
        if not candidates:
            return []

        candidates_json = json.dumps(
            [_candidate_to_dict(c) for c in candidates], indent=2
        )
        account_summary = (
            f"Cash: ${balance.cash:,.2f}, "
            f"Buying Power: ${balance.buying_power:,.2f}, "
            f"Net Liq: ${balance.net_liquidation_value:,.2f}, "
            f"Open P&L: ${balance.open_pl:,.2f}"
        )
        positions_summary = json.dumps(
            [_position_to_dict(p) for p in positions], indent=2
        ) if positions else "No current positions"

        earnings_str = json.dumps(earnings_context, indent=2, default=str) if earnings_context else "No earnings data available"

        prompt = build_csp_analysis_prompt(
            candidates_json=candidates_json,
            account_summary=account_summary,
            positions_summary=positions_summary,
            earnings_context=earnings_str,
        )

        try:
            results = await self._gemini.analyze_batch(
                prompt=prompt,
                response_model=CSPAnalysis,
                system_prompt=SYSTEM_PROMPT_CSP,
                temperature=0.3,
            )
            logger.info("csp_analysis_complete", count=len(results))
            return results
        except Exception:
            logger.error("csp_analysis_failed", exc_info=True)
            return []

    async def analyze_orders(
        self,
        orders: list[BrokerOrder],
        quotes: dict[str, Quote],
        chain_context: dict[str, Any],
        positions: list[BrokerPosition],
    ) -> list[OrderMonitorAnalysis]:
        """Analyze open orders for fill probability and alternatives."""
        if not orders:
            return []

        orders_json = json.dumps(
            [_order_to_dict(o) for o in orders], indent=2
        )
        quotes_json = json.dumps(
            {s: _quote_to_dict(q) for s, q in quotes.items()}, indent=2
        )
        chain_str = json.dumps(chain_context, indent=2, default=str)
        positions_json = json.dumps(
            [_position_to_dict(p) for p in positions], indent=2
        )

        prompt = build_order_monitor_prompt(
            orders_json=orders_json,
            quotes_json=quotes_json,
            chain_context=chain_str,
            positions_json=positions_json,
        )

        try:
            results = await self._gemini.analyze_batch(
                prompt=prompt,
                response_model=OrderMonitorAnalysis,
                system_prompt=SYSTEM_PROMPT_ORDER_MONITOR,
                temperature=0.2,
            )
            logger.info("order_analysis_complete", count=len(results))
            return results
        except Exception:
            logger.error("order_analysis_failed", exc_info=True)
            return []

    async def generate_journal_summary(
        self,
        account_summary: str,
        positions_summary: str,
        trades_today: str,
        recommendations_summary: str,
    ) -> str:
        """Generate an end-of-day journal summary."""
        prompt = f"""Write a concise end-of-day trading journal entry.

## Account
{account_summary}

## Positions
{positions_summary}

## Today's Activity
{trades_today}

## Recommendations Made
{recommendations_summary}

Summarize key observations, what went well, what to watch tomorrow.
Keep it under 300 words.
"""
        try:
            return await self._gemini.generate_text(
                prompt=prompt,
                system_prompt=SYSTEM_PROMPT_POSITION_REVIEW,
                temperature=0.5,
            )
        except Exception:
            logger.error("journal_summary_failed", exc_info=True)
            return "Journal summary unavailable — LLM error."


def _candidate_to_dict(c: ScoredCandidate) -> dict[str, Any]:
    return {
        "symbol": c.symbol,
        "option_symbol": c.option_symbol,
        "option_type": c.option_type,
        "strike": c.strike,
        "expiration": c.expiration.isoformat(),
        "dte": c.dte,
        "bid": c.bid,
        "ask": c.ask,
        "volume": c.volume,
        "open_interest": c.open_interest,
        "implied_volatility": round(c.implied_volatility, 4),
        "underlying_price": c.underlying_price,
        "delta": round(c.delta, 4),
        "theta": round(c.theta, 4),
        "premium_per_contract": c.premium_per_contract,
        "collateral_required": c.collateral_required,
        "annualized_return_pct": c.annualized_return_pct,
        "score": c.score,
        "earnings_within_dte": c.earnings_within_dte,
        "earnings_date": c.earnings_date.isoformat() if c.earnings_date else None,
    }


def _position_to_dict(p: BrokerPosition) -> dict[str, Any]:
    return {
        "symbol": p.symbol,
        "quantity": p.quantity,
        "cost_basis": p.cost_basis,
        "market_value": p.market_value,
        "unrealized_pl": p.unrealized_pl,
        "option_symbol": p.option_symbol,
    }


def _order_to_dict(o: BrokerOrder) -> dict[str, Any]:
    return {
        "broker_order_id": o.broker_order_id,
        "symbol": o.symbol,
        "side": o.side,
        "order_type": o.order_type,
        "quantity": o.quantity,
        "limit_price": o.limit_price,
        "status": o.status,
        "option_symbol": o.option_symbol,
        "strategy": o.strategy,
    }


def _quote_to_dict(q: Quote) -> dict[str, Any]:
    return {
        "symbol": q.symbol,
        "last": q.last,
        "bid": q.bid,
        "ask": q.ask,
        "volume": q.volume,
        "change_pct": q.change_pct,
    }
