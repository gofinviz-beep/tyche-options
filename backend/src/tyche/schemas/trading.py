"""Trading-related Pydantic schemas for order operations."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class OrderPreviewRequest(BaseModel):
    """Request to preview an order before submission."""

    symbol: str
    option_symbol: str | None = None
    side: Literal[
        "buy_to_open", "buy_to_close", "sell_to_open", "sell_to_close", "buy", "sell"
    ]
    quantity: int
    order_type: Literal["market", "limit", "stop", "stop_limit"] = "limit"
    limit_price: float | None = None
    duration: Literal["day", "gtc"] = "day"
    intent: Literal["income", "exit_position", "entry"] = "income"
    recommendation_id: str | None = None


class OrderPreviewResponse(BaseModel):
    """Preview result with risk summary."""

    estimated_cost: float
    estimated_commission: float
    estimated_fees: float
    estimated_premium: float = 0.0
    collateral_required: float = 0.0
    margin_impact: float | None = None
    risk_results: list[RiskRuleResultResponse]
    all_rules_passed: bool
    warnings: list[str] = Field(default_factory=list)


class RiskRuleResultResponse(BaseModel):
    """Individual risk rule evaluation result."""

    rule_name: str
    passed: bool
    reason: str
    details: dict | None = None


class OrderExecuteRequest(BaseModel):
    """Request to execute an approved order."""

    symbol: str
    option_symbol: str | None = None
    side: str
    quantity: int
    order_type: str = "limit"
    limit_price: float | None = None
    duration: str = "day"
    intent: str = "income"
    recommendation_id: str | None = None
    wheel_cycle_id: str | None = None
    user_note: str | None = None


class OrderExecuteResponse(BaseModel):
    """Result of order execution."""

    broker_order_id: str
    status: str
    execution_decision_id: str
    wheel_cycle_id: str | None = None
    message: str = ""


class OpenOrderResponse(BaseModel):
    """An open order with monitoring context."""

    id: str
    broker_order_id: str
    symbol: str
    option_symbol: str | None = None
    side: str
    order_type: str
    quantity: int
    limit_price: float | None = None
    status: str
    intent: str
    strategy: str
    duration: str
    recommendation_id: str | None = None
    wheel_cycle_id: str | None = None
    captured_at: datetime


class OrderMonitorResponse(BaseModel):
    """Order monitoring snapshot for the frontend."""

    order_id: str
    broker_order_id: str
    symbol: str
    underlying_price: float
    option_bid: float
    option_ask: float
    limit_price: float
    distance_to_fill_pct: float
    volume_at_strike: int
    open_interest_at_strike: int
    fill_probability: str
    recommendation: str
    reprice_suggestion: float | None = None
    alternative_action: AlternativeActionResponse | None = None
    reasoning: str
    captured_at: datetime


class AlternativeActionResponse(BaseModel):
    """Fallback action when an options order isn't filling."""

    action_type: str
    description: str
    estimated_proceeds: float | None = None
    estimated_profit: float | None = None
    reasoning: str
