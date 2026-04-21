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
    iv_rank: float | None = None
    iv_percentile: float | None = None
    atm_iv: float | None = None
    vrp: float | None = None
    conviction_score: float = 0.0
    csp_safety_prob: float | None = None
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
    sector: str | None = None
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
    iv_rank: float | None = None
    iv_percentile: float | None = None
    atm_iv: float | None = None
    vrp: float | None = None
    conviction_score: float = 0.0
    csp_safety_prob: float | None = None
    related_csp_strike: float | None = None
    has_active_csp: bool
    recommendation: str
    risk_reward_note: str
    created_at: str


class DipClassificationResponse(BaseModel):
    """API response for a dip catalyst classification."""

    catalyst: str
    risk_level: str
    reasons: list[str]
    actionable: bool
    news_impact_score: float | None = None
    negative_news_count: int = 0
    insider_cluster_sell: bool = False
    last_8k_impact: float | None = None


class MarketContextResponse(BaseModel):
    """Market-wide context at the time of the dip scan."""

    concurrent_dips: int = 0
    total_universe: int = 0
    market_dip_breadth: float = 0.0
    spy_return_5d: float | None = None
    spy_drawdown_from_high: float | None = None
    spy_rsi_14: float | None = None
    is_broad_selloff: bool = False


class RecoverySignalResponse(BaseModel):
    """Backtest-validated recovery probability assessment."""

    actionable: bool = False
    recovery_20d_est: str = "unknown"
    recovery_40d_est: str = "unknown"
    meets_all_thresholds: bool = False
    threshold_checks: list[str] = []
    suggested_cc_dte: str = ""
    peak_recovery_est: str = ""


class DeepDipAlertResponse(BaseModel):
    """API response for an oversold / deep dip stock buy candidate."""

    ticker: str
    alert_type: str
    severity: str
    trend_state: str
    conviction_level: str
    last_close: float
    ema_8: float
    ema_21: float
    ema_50: float = 0.0
    ema_8_slope: float = 0.0
    ema_21_slope: float = 0.0
    ema_50_slope: float = 0.0
    rsi_14: float = 0.0
    prior_streak: int = 0
    dip_pct: float = 0.0
    price_to_21ema_pct: float = 0.0
    price_to_50ema_pct: float = 0.0
    iv_rank: float | None = None
    vrp: float | None = None
    conviction_score: float = 0.0
    volume_declining: bool = False
    institutional_pct: float | None = None
    suggested_action: str = ""
    position_size_hint: str = "standard"
    stop_loss_level: float = 0.0
    market_cap: float | None = None
    market_cap_label: str = ""
    sector: str | None = None
    name: str = ""
    dip_classification: DipClassificationResponse | None = None
    recovery_signal: RecoverySignalResponse | None = None
    detected_at: str = ""


class DeepDipScanResponse(BaseModel):
    """Full response for a deep dip oversold scan."""

    alerts: list[DeepDipAlertResponse]
    total_analyzed: int
    total_oversold: int = 0
    total_actionable: int = 0
    market_context: MarketContextResponse | None = None
    as_of_date: str


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


class BulkPositionItem(BaseModel):
    """Single position in a bulk import request."""

    ticker: str
    quantity: int
    purchase_price: float
    purchase_date: str | None = None
    pullback_type: str = "manual"


class BulkPositionRequest(BaseModel):
    """Request to import multiple positions at once."""

    positions: list[BulkPositionItem]
    skip_duplicates: bool = True


class BulkPositionResponse(BaseModel):
    """Response from a bulk import."""

    created: int
    skipped: int
    errors: list[str]


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
