"""Pydantic response schemas for the v3 Stock Screener."""

from __future__ import annotations

from pydantic import BaseModel


class ScreenerRow(BaseModel):
    """One ticker's compact screener signals + Diamond Finder scoring."""

    ticker: str
    name: str = ""
    sector: str = ""
    as_of_date: str = ""
    last_close: float = 0.0
    market_cap: float | None = None
    institutional_pct: float | None = None
    pct_off_52w_high: float | None = None

    rsi_daily: float = 50.0
    rsi_weekly: float = 50.0
    rsi_monthly: float = 50.0
    rsi_quarterly: float = 50.0

    ema_8: float = 0.0
    ema_21: float = 0.0
    ema_50: float = 0.0
    sma_200: float = 0.0
    pct_vs_ema_8: float = 0.0
    pct_vs_ema_21: float = 0.0
    pct_vs_sma_200: float = 0.0
    slope_ema_8: float = 0.0
    slope_ema_21: float = 0.0
    slope_ema_50: float = 0.0
    days_above_ema_8: int = 0
    days_above_ema_21: int = 0
    stack_score: int = 0
    above_sma_200: bool = False

    ret_1m: float | None = None
    ret_3m: float | None = None
    ret_6m: float | None = None
    ret_1y: float | None = None
    macd_histogram: float = 0.0

    setup_score: float = 0.0
    setup_label: str = "Watch / Base Building"


class ScreenerResponse(BaseModel):
    """Response envelope for ``GET /stocks/screener``."""

    scanned_at: str = ""
    as_of_date: str | None = None
    computed_at: str | None = None
    total: int = 0
    stale: bool = False
    rows: list[ScreenerRow] = []
