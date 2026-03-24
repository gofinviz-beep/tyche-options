"""System routes — health, config, scheduler status."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends

from tyche.api.deps import get_scheduler, get_settings
from tyche.config import TycheSettings
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
        "risk_limits": {
            "max_risk_per_trade_pct": settings.max_risk_per_trade_pct,
            "max_account_exposure_pct": settings.max_account_exposure_pct,
            "max_concentration_pct": settings.max_concentration_per_ticker_pct,
            "max_open_positions": settings.max_open_positions,
            "max_daily_trades": settings.max_new_trades_per_day,
            "max_contracts": settings.max_contracts_per_position,
        },
        "wheel_params": {
            "csp_dte_min": settings.csp_target_dte_min,
            "csp_dte_max": settings.csp_target_dte_max,
            "cc_dte_min": settings.cc_target_dte_min,
            "cc_dte_max": settings.cc_target_dte_max,
            "min_annualized_return_pct": settings.min_annualized_return_pct,
        },
        "workflow_schedule": {
            "morning_scan": settings.morning_scan_time,
            "order_monitor_interval_min": settings.order_monitor_interval_min,
            "midday_review": settings.midday_review_time,
            "eod_journal": settings.eod_journal_time,
        },
        "watchlist": settings.watchlist_symbols,
    }


@router.get("/scheduler")
async def get_scheduler_status(
    scheduler: WorkflowScheduler = Depends(get_scheduler),
) -> dict[str, Any]:
    """Get status of scheduled workflows."""
    return {
        "running": scheduler.running,
        "jobs": scheduler.get_job_status(),
    }
