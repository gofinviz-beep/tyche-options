"""Account-related Pydantic schemas for API requests/responses."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class AccountBalanceResponse(BaseModel):
    """Account balance as returned by the API."""

    cash: float
    buying_power: float
    net_liquidation_value: float
    market_value: float
    total_equity: float
    open_pl: float
    close_pl: float
    pending_cash: float
    captured_at: datetime


class PositionResponse(BaseModel):
    """Position as returned by the API."""

    id: str
    symbol: str
    quantity: float
    cost_basis: float
    average_cost: float
    market_value: float
    unrealized_pl: float
    unrealized_pl_pct: float
    option_symbol: str | None = None
    option_type: str | None = None
    strike: float | None = None
    expiration: str | None = None
    strategy: str
    contracts: int
    wheel_cycle_id: str | None = None


class AccountSummaryResponse(BaseModel):
    """Combined account summary for the dashboard."""

    balance: AccountBalanceResponse
    positions: list[PositionResponse]
    position_count: int
    total_unrealized_pl: float
    cash_available_for_csp: float
