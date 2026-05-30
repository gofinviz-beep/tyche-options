"""Directional Alpha engine schemas for API responses."""

from __future__ import annotations

from pydantic import BaseModel


class AlphaFactorScores(BaseModel):
    """Deterministic 0-1 technical factor sub-scores."""

    momentum: float = 0.0
    relative_strength: float = 0.0
    trend_quality: float = 0.0
    breakout: float = 0.0
    volume_thrust: float = 0.0


class AlphaSignalResponse(BaseModel):
    """Directional alpha assessment for a single ticker."""

    ticker: str
    alpha_score: float
    signal: str
    horizon: str
    factors: AlphaFactorScores

    breakout_prob_swing: float | None = None
    breakout_prob_trend: float | None = None
    breakout_prob_thematic: float | None = None

    last_close: float = 0.0
    return_63d: float | None = None
    return_126d: float | None = None
    return_252d: float | None = None
    rs_126d: float | None = None
    pct_off_52w_high: float | None = None
    ema_stack_score: int = 0
    volume_thrust_ratio: float | None = None
    as_of_date: str | None = None

    market_cap: float | None = None
    institutional_pct: float | None = None
    sector: str | None = None
    is_watchlist: bool = False


class AlphaScanResponse(BaseModel):
    """Full directional alpha scan response."""

    scanned_at: str
    as_of_date: str | None = None
    computed_at: str | None = None
    ml_available: bool = False
    total: int = 0
    strong_buy_count: int = 0
    buy_count: int = 0
    signals: list[AlphaSignalResponse] = []


class AlphaBatchResponse(BaseModel):
    """Result of triggering an alpha batch recompute."""

    status: str
    signals: int = 0
    buy_signals: int = 0
    ml_available: bool = False
    as_of_date: str | None = None
    elapsed_s: float | None = None
