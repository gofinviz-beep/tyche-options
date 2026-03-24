"""Analysis-related Pydantic schemas for LLM structured outputs."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class ExitLevel(BaseModel):
    """A single exit target in an exit ladder."""

    pct_of_position: float
    target_price: float
    reasoning: str


class CSPAnalysis(BaseModel):
    """Structured LLM output for cash-secured put screening."""

    ticker: str
    assignment_comfort: Literal["high", "medium", "low"]
    assignment_comfort_reasoning: str
    thesis: str
    recommended_strike: float
    recommended_expiration: str
    target_premium: float
    annualized_return_pct: float
    earnings_proximity: str | None = None
    earnings_risk_assessment: str | None = None
    invalidation: str
    confidence: Literal["low", "medium", "high"]
    risks: list[str]
    would_you_hold_if_assigned: str
    suggested_contracts: int
    collateral_required: float
    allocation_mode: Literal["concentrated", "diversified"]


class CoveredCallAnalysis(BaseModel):
    """Structured LLM output for covered call screening on held shares."""

    ticker: str
    shares_held: int
    current_share_price: float
    cost_basis: float
    unrealized_pl: float
    recommended_strike: float
    recommended_expiration: str
    target_premium: float
    annualized_return_pct: float
    called_away_profit: float
    confidence: Literal["low", "medium", "high"]
    thesis: str
    risks: list[str]


class OrderMonitorAnalysis(BaseModel):
    """Structured LLM output for 15-min order monitoring."""

    order_id: str
    current_bid: float
    current_ask: float
    your_limit_price: float
    volume_at_strike: int
    open_interest_at_strike: int
    fill_probability: Literal["likely", "possible", "unlikely"]
    why_not_filling: str | None = None
    recommendation: Literal["hold", "reprice_to_bid", "reprice_custom", "cancel"]
    reprice_suggestion: float | None = None
    alternative_action: AlternativeAction | None = None
    reasoning: str


class AlternativeAction(BaseModel):
    """Fallback when an options order isn't filling."""

    action_type: Literal[
        "sell_shares_at_market",
        "sell_shares_at_limit",
        "reprice_option",
        "cancel_and_wait",
        "roll_to_different_strike",
    ]
    description: str
    estimated_proceeds: float | None = None
    estimated_profit: float | None = None
    reasoning: str


class TradeAnalysis(BaseModel):
    """Structured LLM output for directional trade analysis."""

    ticker: str
    direction: Literal["bullish", "bearish", "neutral"]
    strategy: str
    thesis: str
    entry_guidance: str
    exit_ladder: list[ExitLevel] = Field(default_factory=list)
    invalidation: str
    confidence: Literal["low", "medium", "high"]
    risks: list[str]
    holding_period: str
    depends_on: Literal["momentum", "mean_reversion", "event", "volatility"]


class CapitalAllocationSuggestion(BaseModel):
    """LLM suggestion for how to allocate available cash across opportunities."""

    mode: Literal["concentrated", "diversified"]
    reasoning: str
    allocations: list[AllocationEntry]
    total_collateral_used: float
    remaining_cash: float
    projected_weekly_income: float


class AllocationEntry(BaseModel):
    """One position in a capital allocation plan."""

    symbol: str
    strategy: str
    strike: float
    contracts: int
    collateral_required: float
    estimated_premium: float
    annualized_return_pct: float
    conviction_level: Literal["high", "medium", "low"]
