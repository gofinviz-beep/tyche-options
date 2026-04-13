"""System routes — health, config, scheduler status."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel

from tyche.api.deps import get_scheduler, get_settings, reset_all
from tyche.config import TycheSettings, get_config_store, invalidate_settings
from tyche.workflow.scheduler import WorkflowScheduler

logger = structlog.get_logger()
router = APIRouter(prefix="/system", tags=["system"])


@router.get("/config")
async def get_config(
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Get current configuration (sensitive values redacted)."""
    return {
        "sandbox_mode": settings.tradier_sandbox,
        "preview_only": settings.preview_only_mode,
        "broker_configured": bool(settings.tradier_api_token),
        "llm_configured": bool(settings.gemini_api_key),
        "earnings_api_configured": bool(settings.earnings_api_key),
        "available_capital": settings.available_capital,
        "risk_limits": {
            "max_risk_per_trade_pct": settings.max_risk_per_trade_pct,
            "max_account_exposure_pct": settings.max_account_exposure_pct,
            "max_concentration_per_ticker_pct": settings.max_concentration_per_ticker_pct,
            "max_open_positions": settings.max_open_positions,
            "max_new_trades_per_day": settings.max_new_trades_per_day,
            "max_contracts_per_position": settings.max_contracts_per_position,
        },
        "wheel_params": {
            "csp_target_dte_min": settings.csp_target_dte_min,
            "csp_target_dte_max": settings.csp_target_dte_max,
            "cc_target_dte_min": settings.cc_target_dte_min,
            "cc_target_dte_max": settings.cc_target_dte_max,
            "min_annualized_return_pct": settings.min_annualized_return_pct,
        },
        "universe_filters": {
            "min_market_cap_millions": settings.min_market_cap_millions,
            "min_institutional_pct": settings.min_institutional_pct,
            "min_avg_volume": settings.min_avg_volume,
            "min_stock_price": settings.min_stock_price,
        },
        "options_scan": {
            "max_expiration_dates": settings.max_expiration_dates,
            "expiration_mode": settings.expiration_mode,
            "strike_range_pct": settings.strike_range_pct,
            "llm_concurrency": settings.llm_concurrency,
        },
        "conviction_engine": {
            "ema_fast_period": settings.ema_fast_period,
            "ema_slow_period": settings.ema_slow_period,
            "max_extension_pct": settings.max_extension_pct,
            "min_days_above_emas": settings.min_days_above_emas,
            "max_days_above_emas": settings.max_days_above_emas,
            "pullback_proximity_pct": settings.pullback_proximity_pct,
            "bootstrap_days": settings.bootstrap_days,
        },
        "pullback_csp": {
            "pullback_csp_enabled": settings.pullback_csp_enabled,
            "min_prior_streak": settings.min_prior_streak,
            "pullback_strike_offset_pct": settings.pullback_strike_offset_pct,
            "pullback_strike_ceiling_pct": settings.pullback_strike_ceiling_pct,
            "earliest_expiration_only": settings.earliest_expiration_only,
        },
        "workflow_schedule": {
            "morning_scan_time": settings.morning_scan_time,
            "order_monitor_interval_min": settings.order_monitor_interval_min,
            "midday_review_time": settings.midday_review_time,
            "eod_journal_time": settings.eod_journal_time,
        },
        "options_snapshot": {
            "options_snapshot_enabled": settings.options_snapshot_enabled,
            "options_snapshot_time": settings.options_snapshot_time,
            "options_snapshot_min_market_cap": settings.options_snapshot_min_market_cap,
            "options_snapshot_rpm": settings.options_snapshot_rpm,
            "options_snapshot_concurrency": settings.options_snapshot_concurrency,
        },
        "notifications": {
            "notification_email_enabled": settings.notification_email_enabled,
            "notification_email_to": settings.notification_email_to,
            "notification_pullback_alert_enabled": settings.notification_pullback_alert_enabled,
            "daily_digest_enabled": settings.daily_digest_enabled,
            "daily_digest_time": settings.daily_digest_time,
        },
        "llm": {
            "gemini_model_fast": settings.gemini_model_fast,
            "gemini_model_deep": settings.gemini_model_deep,
            "gemini_model_classify": settings.gemini_model_classify,
        },
        "scan_persistence": {
            "scan_retention_count": settings.scan_retention_count,
        },
        "news_pipeline": {
            "news_ingestion_enabled": settings.news_ingestion_enabled,
            "news_finnhub_enabled": settings.news_finnhub_enabled,
            "news_ingest_interval_minutes": settings.news_ingest_interval_minutes,
            "news_classify_workers": settings.news_classify_workers,
            "news_classify_rpm": settings.news_classify_rpm,
            "news_lookback_hours": settings.news_lookback_hours,
            "news_risk_threshold": settings.news_risk_threshold,
        },
        "edgar_pipeline": {
            "edgar_ingestion_enabled": settings.edgar_ingestion_enabled,
            "edgar_ingest_interval_minutes": settings.edgar_ingest_interval_minutes,
            "edgar_lookback_days": settings.edgar_lookback_days,
        },
        "watchlist": settings.watchlist_symbols,
    }


class ConfigUpdate(BaseModel):
    """Partial config update — all fields optional."""

    # Watchlist
    watchlist: list[str] | None = None

    # Capital
    available_capital: float | None = None

    # Risk limits
    max_risk_per_trade_pct: float | None = None
    max_account_exposure_pct: float | None = None
    max_concentration_per_ticker_pct: float | None = None
    max_open_positions: int | None = None
    max_new_trades_per_day: int | None = None
    max_contracts_per_position: int | None = None

    # Wheel params
    csp_target_dte_min: int | None = None
    csp_target_dte_max: int | None = None
    cc_target_dte_min: int | None = None
    cc_target_dte_max: int | None = None
    min_annualized_return_pct: float | None = None

    # Universe filters
    min_market_cap_millions: float | None = None
    min_institutional_pct: float | None = None
    min_avg_volume: int | None = None
    min_stock_price: float | None = None

    # Options scan
    max_expiration_dates: int | None = None
    expiration_mode: str | None = None
    strike_range_pct: float | None = None
    llm_concurrency: int | None = None

    # Conviction engine
    ema_fast_period: int | None = None
    ema_slow_period: int | None = None
    max_extension_pct: float | None = None
    min_days_above_emas: int | None = None
    max_days_above_emas: int | None = None
    pullback_proximity_pct: float | None = None
    bootstrap_days: int | None = None

    # Pullback CSP
    pullback_csp_enabled: bool | None = None
    min_prior_streak: int | None = None
    pullback_strike_offset_pct: float | None = None
    pullback_strike_ceiling_pct: float | None = None
    earliest_expiration_only: bool | None = None

    # Workflow schedule
    morning_scan_time: str | None = None
    order_monitor_interval_min: int | None = None
    midday_review_time: str | None = None
    eod_journal_time: str | None = None

    # Options snapshot
    options_snapshot_enabled: bool | None = None
    options_snapshot_time: str | None = None
    options_snapshot_min_market_cap: float | None = None
    options_snapshot_rpm: int | None = None
    options_snapshot_concurrency: int | None = None

    # Notifications
    notification_email_enabled: bool | None = None
    notification_email_to: str | None = None
    notification_pullback_alert_enabled: bool | None = None
    daily_digest_enabled: bool | None = None
    daily_digest_time: str | None = None

    # LLM models
    gemini_model_fast: str | None = None
    gemini_model_deep: str | None = None

    # Scan persistence
    scan_retention_count: int | None = None

    # News pipeline
    news_ingestion_enabled: bool | None = None
    news_finnhub_enabled: bool | None = None
    news_ingest_interval_minutes: int | None = None
    news_classify_workers: int | None = None
    news_classify_rpm: int | None = None
    news_lookback_hours: int | None = None
    news_risk_threshold: float | None = None

    # EDGAR pipeline
    edgar_ingestion_enabled: bool | None = None
    edgar_ingest_interval_minutes: int | None = None
    edgar_lookback_days: int | None = None


# Map UI field names → config.db field names (most are 1:1)
_UI_TO_DB_KEY: dict[str, str] = {
    "watchlist": "watchlist_symbols",
}


@router.patch("/config")
async def update_config(
    body: ConfigUpdate,
) -> dict[str, Any]:
    """Update editable configuration. Persists to config.db and takes
    effect immediately (no restart required).
    """
    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    db_updates: dict[str, Any] = {}
    changed: dict[str, Any] = {}

    for ui_key, value in data.items():
        db_key = _UI_TO_DB_KEY.get(ui_key, ui_key)
        db_updates[db_key] = value
        changed[ui_key] = value

    store = get_config_store()
    try:
        store.set_many(db_updates)
    except Exception as exc:
        logger.error("config_update_failed", error=str(exc))
        raise HTTPException(
            status_code=500, detail=f"Failed to save settings: {exc}"
        )

    invalidate_settings()
    reset_all()
    logger.info("config_updated", changed_fields=list(changed.keys()))

    return {"status": "ok", "updated": changed}


@router.get("/scheduler")
async def get_scheduler_status(
    scheduler: WorkflowScheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    """Get status of scheduled workflows."""
    return {
        "running": scheduler.running,
        "jobs": scheduler.get_job_status(),
    }


@router.post("/ml/retrain")
async def trigger_ml_retrain(
    background_tasks: BackgroundTasks,
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, str]:
    """Manually trigger an ML model retrain."""
    from tyche.app import _scheduled_ml_retrain

    background_tasks.add_task(_scheduled_ml_retrain)
    return {"status": "started"}


@router.get("/ml/model-info")
async def get_ml_model_info(
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Get info about the currently loaded ML model."""
    from tyche.api.deps import get_csp_safety_predictor

    predictor = get_csp_safety_predictor(settings)
    if predictor is None or not predictor.is_available:
        return {"available": False}
    return {"available": True, **predictor.model_info}
