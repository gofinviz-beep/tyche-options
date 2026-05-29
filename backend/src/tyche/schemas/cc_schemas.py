"""Pydantic schemas for the Covered Call Recommender API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CCPositionRequest(BaseModel):
    """A single position to analyze for CC opportunities."""

    ticker: str
    shares: int = 100
    cost_basis: float = 0.0


class CCBatchRequest(BaseModel):
    """Batch request to analyze multiple positions."""

    positions: list[CCPositionRequest]
    target_dte: int = Field(default=8, ge=1, le=60)


class CCSignalResponse(BaseModel):
    """Summary CC signal for one ticker."""

    ticker: str
    signal: str
    signal_reason: str
    last_close: float
    ema_8: float
    ema_21: float
    ema_50: float
    ema_21_slope: float = 0.0
    extension_pct_8: float
    extension_pct_21: float
    rsi_14: float
    iv_rank: float | None = None
    vrp: float | None = None
    rv_20d: float | None = None
    suggested_strike: float
    suggested_otm_pct: float
    suggested_expiry_dte: int
    suggested_premium_est: float | None = None
    optimal_entry_day: str
    assignment_prob_1w: float
    assignment_prob_2w: float
    estimated_next_earnings: str | None = None
    earnings_in_window: bool
    price_source: str = "ohlcv_close"
    live_price: float | None = None
    prev_close: float | None = None


class CCDeepDiveResponse(BaseModel):
    """Full deep-dive analysis for a single ticker."""

    signal: CCSignalResponse
    total_episodes: int
    episode_table: list[dict]
    days_to_8ema: dict
    days_to_21ema: dict
    days_to_50ema: dict
    drawdown_at_8ema: dict
    drawdown_at_21ema: dict
    forward_returns: list[dict]
    dow_analysis: list[dict]
    rally_peak_day_distribution: dict
    call_candidates: list[dict] | None = None
    pnl_scenarios: dict
    recommended_action: dict


class CCPortfolioResponse(BaseModel):
    """Response for batch CC analysis."""

    analyses: list[CCDeepDiveResponse]
    portfolio_summary: dict
