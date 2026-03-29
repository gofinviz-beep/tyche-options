"""System routes — health, config, scheduler status."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

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
        "workflow_schedule": {
            "morning_scan": settings.morning_scan_time,
            "order_monitor_interval_min": settings.order_monitor_interval_min,
            "midday_review": settings.midday_review_time,
            "eod_journal": settings.eod_journal_time,
        },
        "watchlist": settings.watchlist_symbols,
    }


class ConfigUpdate(BaseModel):
    """Partial config update — all fields optional."""

    watchlist: list[str] | None = None
    available_capital: float | None = None
    max_risk_per_trade_pct: float | None = None
    max_account_exposure_pct: float | None = None
    max_concentration_per_ticker_pct: float | None = None
    max_open_positions: int | None = None
    max_new_trades_per_day: int | None = None
    max_contracts_per_position: int | None = None
    csp_target_dte_min: int | None = None
    csp_target_dte_max: int | None = None
    cc_target_dte_min: int | None = None
    cc_target_dte_max: int | None = None
    min_annualized_return_pct: float | None = None


_CONFIG_ENV_MAP: dict[str, str] = {
    "watchlist": "TYCHE_WATCHLIST_SYMBOLS",
    "available_capital": "TYCHE_AVAILABLE_CAPITAL",
    "max_risk_per_trade_pct": "TYCHE_MAX_RISK_PER_TRADE_PCT",
    "max_account_exposure_pct": "TYCHE_MAX_ACCOUNT_EXPOSURE_PCT",
    "max_concentration_per_ticker_pct": "TYCHE_MAX_CONCENTRATION_PER_TICKER_PCT",
    "max_open_positions": "TYCHE_MAX_OPEN_POSITIONS",
    "max_new_trades_per_day": "TYCHE_MAX_NEW_TRADES_PER_DAY",
    "max_contracts_per_position": "TYCHE_MAX_CONTRACTS_PER_POSITION",
    "csp_target_dte_min": "TYCHE_CSP_TARGET_DTE_MIN",
    "csp_target_dte_max": "TYCHE_CSP_TARGET_DTE_MAX",
    "cc_target_dte_min": "TYCHE_CC_TARGET_DTE_MIN",
    "cc_target_dte_max": "TYCHE_CC_TARGET_DTE_MAX",
    "min_annualized_return_pct": "TYCHE_MIN_ANNUALIZED_RETURN_PCT",
}


def _update_env_file(updates: dict[str, str], env_path: Path) -> None:
    """Read .env, update/add keys, write back."""
    lines: list[str] = []
    updated_keys: set[str] = set()

    if env_path.exists():
        for line in env_path.read_text().splitlines():
            stripped = line.strip()
            if stripped and not stripped.startswith("#") and "=" in stripped:
                key = stripped.split("=", 1)[0].strip()
                if key in updates:
                    lines.append(f"{key}={updates[key]}")
                    updated_keys.add(key)
                else:
                    lines.append(line)
            else:
                lines.append(line)

    for key, value in updates.items():
        if key not in updated_keys:
            lines.append(f"{key}={value}")

    env_path.write_text("\n".join(lines) + "\n")


@router.patch("/config")
async def update_config(
    body: ConfigUpdate,
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Update editable configuration values. Persists changes to .env file."""
    updates: dict[str, str] = {}
    changed: dict[str, Any] = {}

    data = body.model_dump(exclude_none=True)
    if not data:
        raise HTTPException(status_code=400, detail="No fields to update.")

    for field, value in data.items():
        env_key = _CONFIG_ENV_MAP.get(field)
        if not env_key:
            continue

        if field == "watchlist":
            env_value = json.dumps(value)
        else:
            env_value = str(value)

        updates[env_key] = env_value
        changed[field] = value

    if not updates:
        raise HTTPException(status_code=400, detail="No valid fields to update.")

    env_path = Path(".env")
    try:
        _update_env_file(updates, env_path)
        logger.info("config_updated", changed_fields=list(changed.keys()))
    except Exception as exc:
        logger.error("config_update_failed", error=str(exc))
        raise HTTPException(status_code=500, detail=f"Failed to save settings: {exc}")

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
