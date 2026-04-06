"""Pydantic schemas for pullback alerts, stock buy recommendations, and CSP fallbacks."""

from __future__ import annotations

from pydantic import BaseModel


class HistoricalBounceStats(BaseModel):
    """Historical bounce statistics from backtest for a single pullback type."""

    pullback_type: str
    event_count: int
    median_peak_gain_pct: float
    mean_peak_gain_pct: float
    p25_peak_gain_pct: float
    p75_peak_gain_pct: float
    median_exit_gain_pct: float
    win_rate_5pct: float
    win_rate_10pct: float
    median_days_to_peak: int
    median_days_to_exit: int
    avg_max_drawdown_pct: float
    suggested_exit_pct: float


class PullbackAlertResponse(BaseModel):
    """API response for a single pullback alert."""

    ticker: str
    alert_type: str
    severity: str
    trend_state: str
    conviction_level: str
    raw_conviction: str = "none"
    last_close: float
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    ema_50: float = 0.0
    ema_50_slope: float = 0.0
    rsi_14: float = 0.0
    volume_declining: bool
    institutional_pct: float | None = None
    institutional_label: str
    suggested_action: str
    position_size_hint: str
    stop_loss_level: float
    detected_at: str
    market_cap: float | None = None
    market_cap_label: str = ""
    exchange: str = ""
    name: str = ""
    days_above_both_emas: int = 0
    avg_volume_20d: float = 0
    price_to_8ema_pct: float = 0
    price_to_21ema_pct: float = 0
    historical_bounce: HistoricalBounceStats | None = None


class StockBuyRecommendationResponse(BaseModel):
    """API response for a stock buy recommendation."""

    ticker: str
    entry_type: str
    entry_price: float
    target_ema_value: float
    stop_loss: float
    conviction: str
    institutional_pct: float | None = None
    institutional_label: str
    volume_confirmation: bool
    position_size_hint: str
    days_above_emas: int
    ema_8_slope: float
    ema_21_slope: float
    ema_50_slope: float = 0.0
    rsi_14: float = 0.0
    related_csp_strike: float | None = None
    has_active_csp: bool
    recommendation: str
    risk_reward_note: str
    created_at: str


class CSPFallbackAlertResponse(BaseModel):
    """API response for a CSP expiry fallback alert."""

    ticker: str
    expired_strike: float
    expiry_date: str
    premium_collected: float
    pullback_alert: PullbackAlertResponse
    message: str


class ExpiredCSPResponse(BaseModel):
    """API response for an expired CSP record."""

    ticker: str
    expired_strike: float
    expiry_date: str
    premium_collected: float
    recorded_at: str


class PullbackScanResponse(BaseModel):
    """Full response for a pullback scan."""

    scan_id: str
    scanned_at: str
    pullback_alerts: list[PullbackAlertResponse]
    stock_recommendations: list[StockBuyRecommendationResponse]
    csp_fallback_alerts: list[CSPFallbackAlertResponse]
    total_signals_analyzed: int


class RecordCSPExpiryRequest(BaseModel):
    """Request to record a CSP that expired worthless."""

    ticker: str
    strike: float
    expiry_date: str
    premium_collected: float


class StockPositionRequest(BaseModel):
    """Request to record a stock purchase."""

    ticker: str
    purchase_price: float
    quantity: int = 1
    purchase_date: str
    pullback_type: str = "manual"


class StockPositionResponse(BaseModel):
    """API response for a stock position."""

    id: str
    ticker: str
    quantity: int
    purchase_date: str | None
    purchase_price: float
    pullback_type: str
    target_exit_pct: float | None = None
    target_exit_price: float | None = None
    stop_loss_price: float | None = None
    current_price: float | None = None
    current_gain_pct: float | None = None
    status: str
    exit_date: str | None = None
    exit_price: float | None = None
    exit_reason: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


class ExitSignalResponse(BaseModel):
    """API response for a triggered exit signal."""

    id: str
    position_id: str
    ticker: str
    signal_type: str
    trigger_price: float
    current_price: float
    gain_pct: float
    triggered_at: str | None = None


class ExitCheckResponse(BaseModel):
    """API response for an exit monitor run."""

    positions_checked: int
    prices_updated: int
    profit_targets_hit: int
    stop_losses_hit: int
    errors: int
    signals: list[ExitSignalResponse]
