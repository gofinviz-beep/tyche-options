"""Pydantic schemas for the Stocks module — conviction snapshots, transitions, and batch results."""

from __future__ import annotations

from pydantic import BaseModel

from tyche.schemas.alerts import PullbackAlertResponse, StockBuyRecommendationResponse


class ConvictionSnapshotResponse(BaseModel):
    """API response for a single conviction snapshot."""

    ticker: str
    as_of_date: str | None = None
    trend_state: str
    conviction_level: str
    raw_conviction: str = "none"
    csp_eligible: bool
    last_close: float
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    price_to_8ema_pct: float
    price_to_21ema_pct: float
    volume_declining: bool
    days_above_both_emas: int
    prior_streak: int = 0
    avg_volume_20d: int
    latest_volume: int
    ema_50: float = 0.0
    ema_50_slope: float = 0.0
    rsi_14: float = 0.0
    iv_rank: float | None = None
    iv_percentile: float | None = None
    atm_iv: float | None = None
    vrp: float | None = None
    conviction_score: float = 0.0
    csp_safety_prob: float | None = None
    computed_at: str | None = None
    market_cap: float | None = None
    institutional_pct: float | None = None
    sector: str | None = None


class ConvictionTransitionResponse(BaseModel):
    """API response for a single state transition."""

    id: str
    ticker: str
    from_state: str
    to_state: str
    transition_date: str | None = None
    last_close: float
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    conviction_level: str
    raw_conviction: str = "none"
    detected_at: str | None = None


class ActivePullbacksResponse(BaseModel):
    """Active pullbacks split by watchlist vs. universe."""

    watchlist: list[PullbackAlertResponse]
    universe: list[PullbackAlertResponse]
    transitions_today: list[ConvictionTransitionResponse]
    as_of_date: str


class ConvictionBatchStatusResponse(BaseModel):
    """Response for a conviction batch run."""

    as_of_date: str
    total_tickers_in_store: int
    tickers_after_market_cap_filter: int
    tickers_after_price_volume_filter: int
    signals_computed: int
    snapshots_upserted: int
    transitions_detected: int
    new_pullback_transitions: int
    duration_ms: float
    errors: list[str]


class StockRecommendationsResponse(BaseModel):
    """Stock buy recommendations with pullback context."""

    recommendations: list[StockBuyRecommendationResponse]
    as_of_date: str


class ConvictionHistoryResponse(BaseModel):
    """Historical conviction snapshots for a ticker."""

    ticker: str
    snapshots: list[ConvictionSnapshotResponse]
    transitions: list[ConvictionTransitionResponse]


class TransitionsListResponse(BaseModel):
    """List of conviction transitions with metadata."""

    transitions: list[ConvictionTransitionResponse]
    from_date: str
    to_date: str
