"""Conviction and Order Intent schemas for API requests/responses."""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


# ── Conviction Signals ──────────────────────────────────────────────────


class GateResultResponse(BaseModel):
    """Result of a single CSP eligibility gate check."""

    gate: str
    passed: bool
    actual: str
    threshold: str
    reason: str


class ConvictionSignalResponse(BaseModel):
    """Conviction signal for a single ticker — API response."""

    ticker: str
    trend_state: str
    conviction_level: str
    csp_eligible: bool
    is_watchlist: bool = False
    last_close: float
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    price_to_8ema_pct: float
    price_to_21ema_pct: float
    volume_declining_on_pullback: bool
    avg_volume_20d: int
    latest_volume: int
    days_above_both_emas: int
    prior_streak: int = 0
    as_of_date: str | None = None
    ema_50: float = 0.0
    ema_50_slope: float = 0.0
    rsi_14: float = 0.0
    iv_rank: float | None = None
    iv_percentile: float | None = None
    atm_iv: float | None = None
    vrp: float | None = None
    conviction_score: float = 0.0
    csp_safety_prob: float | None = None
    market_cap: float | None = None
    institutional_pct: float | None = None
    sector: str | None = None
    gate_results: list[GateResultResponse] = []


class TrendSummary(BaseModel):
    """Breakdown of how many tickers are in each trend state."""

    strong_uptrend: int = 0
    uptrend: int = 0
    pullback_to_8ema: int = 0
    pullback_to_21ema: int = 0
    consolidation: int = 0
    downtrend: int = 0
    insufficient_data: int = 0


class ConvictionScanResponse(BaseModel):
    """Result of a conviction scan across multiple tickers."""

    scan_id: str
    scanned_at: str
    total_screened: int
    eligible_count: int
    uptrend_eligible: int = 0
    pullback_eligible: int = 0
    pullback_count: int = 0
    trend_summary: TrendSummary | None = None
    signals: list[ConvictionSignalResponse]


# ── Order Intents ───────────────────────────────────────────────────────


class OrderIntentResponse(BaseModel):
    """Order intent — API response."""

    id: str
    created_at: str
    updated_at: str
    status: str
    symbol: str
    option_symbol: str | None = None
    side: str
    strategy: str
    strike: float | None = None
    expiration: str | None = None
    quantity: int
    limit_price: float | None = None
    estimated_premium: float
    collateral_required: float
    annualized_return_pct: float
    conviction_level: str
    trend_state: str
    thesis: str | None = None
    risks: str | None = None
    invalidation: str | None = None
    risk_passed: bool
    risk_summary: str | None = None
    approved_at: str | None = None
    rejected_at: str | None = None
    user_note: str | None = None
    executed_at: str | None = None
    actual_fill_price: float | None = None
    actual_quantity: int | None = None
    actual_premium: float | None = None
    broker_confirmation: str | None = None
    scan_id: str | None = None
    wheel_cycle_id: str | None = None


class ApproveIntentRequest(BaseModel):
    """Request to approve an order intent for manual execution."""

    user_note: str | None = None


class RejectIntentRequest(BaseModel):
    """Request to reject an order intent."""

    reason: str | None = None


class CreateIntentRequest(BaseModel):
    """Manually create a trade intent (not from a scan)."""

    symbol: str
    strike: float
    expiration: str
    quantity: int = Field(ge=1)
    limit_price: float | None = None
    strategy: str = "csp"
    side: str = "sell_to_open"
    conviction_level: str = "none"
    trend_state: str = "unknown"
    thesis: str | None = None


class RecordExecutionRequest(BaseModel):
    """Record that an approved intent was executed manually (e.g., in Fidelity)."""

    fill_price: float
    quantity: int
    premium_received: float | None = None
    broker_confirmation: str | None = None
    note: str | None = None


class OrderIntentListResponse(BaseModel):
    """List of order intents with summary stats."""

    intents: list[OrderIntentResponse]
    total: int
    pending: int
    approved: int
    executed: int


# ── Data Store ──────────────────────────────────────────────────────────


class DataStoreStatusResponse(BaseModel):
    """Status of the local OHLCV data store."""

    exists: bool
    total_rows: int
    ticker_count: int
    earliest_date: str | None = None
    latest_date: str | None = None
    store_path: str


class BootstrapRequest(BaseModel):
    """Request to bootstrap the OHLCV data store."""

    days: int = Field(default=120, ge=30, le=365)


class BootstrapResponse(BaseModel):
    """Result of bootstrapping the data store."""

    dates_fetched: int
    bars_stored: int
    tickers_found: int
    tickers_meta: int = 0
    status: str = "complete"
