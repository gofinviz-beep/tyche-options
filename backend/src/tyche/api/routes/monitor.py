"""Active position & order monitoring routes."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from datetime import date, datetime
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from tyche.api.deps import get_active_monitor
from tyche.workflow.active_monitor import ActiveMonitor, TrackedPosition

logger = structlog.get_logger()
router = APIRouter(prefix="/monitor", tags=["monitor"])


class TrackPositionRequest(BaseModel):
    symbol: str
    option_symbol: str
    position_type: str = "short_put"
    strike: float
    expiration: str  # YYYY-MM-DD
    entry_price: float
    contracts: int
    underlying_at_entry: float


class TrackPositionResponse(BaseModel):
    status: str
    option_symbol: str
    message: str


@router.post("/track", response_model=TrackPositionResponse)
async def track_position(
    req: TrackPositionRequest,
    monitor: ActiveMonitor = Depends(get_active_monitor),
) -> TrackPositionResponse:
    """Start tracking a filled position for real-time monitoring."""
    pos = TrackedPosition(
        symbol=req.symbol.upper(),
        option_symbol=req.option_symbol,
        position_type=req.position_type,
        strike=req.strike,
        expiration=datetime.strptime(req.expiration, "%Y-%m-%d").date(),
        entry_price=req.entry_price,
        contracts=req.contracts,
        entry_date=date.today(),
        underlying_at_entry=req.underlying_at_entry,
    )
    monitor.track_position(pos)
    type_char = "C" if "call" in req.position_type else "P"
    return TrackPositionResponse(
        status="tracking",
        option_symbol=req.option_symbol,
        message=f"Now tracking {req.symbol} {req.strike}{type_char} exp {req.expiration} x{req.contracts}",
    )


@router.delete("/track/{option_symbol}")
async def untrack_position(
    option_symbol: str,
    monitor: ActiveMonitor = Depends(get_active_monitor),
) -> dict[str, str]:
    """Stop tracking a position."""
    monitor.untrack_position(option_symbol)
    return {"status": "untracked", "option_symbol": option_symbol}


@router.get("/positions")
async def get_all_position_status(
    monitor: ActiveMonitor = Depends(get_active_monitor),
) -> dict[str, Any]:
    """Get current status of all tracked positions."""
    statuses = await monitor.check_all_positions()
    return {
        "tracked_count": len(statuses),
        "positions": [s.to_dict() for s in statuses],
        "alerts": [
            alert.to_dict()
            for s in statuses
            for alert in s.alerts
        ],
    }


@router.get("/positions/{option_symbol}")
async def get_position_status(
    option_symbol: str,
    monitor: ActiveMonitor = Depends(get_active_monitor),
) -> dict[str, Any]:
    """Get real-time status of a specific tracked position."""
    status = await monitor.check_position(option_symbol)
    if not status:
        raise HTTPException(404, f"Position {option_symbol} not tracked")
    return status.to_dict()


async def _position_monitor_stream(
    monitor: ActiveMonitor,
    interval_seconds: int = 30,
) -> AsyncGenerator[dict[str, Any], None]:
    """Stream real-time position updates via SSE."""
    while True:
        try:
            statuses = await monitor.check_all_positions()
            all_alerts = [
                alert.to_dict()
                for s in statuses
                for alert in s.alerts
            ]
            yield {
                "event": "position_update",
                "data": json.dumps({
                    "positions": [s.to_dict() for s in statuses],
                    "alerts": all_alerts,
                    "alert_count": len(all_alerts),
                }),
            }
        except asyncio.CancelledError:
            return
        except Exception:
            logger.warning("position_stream_error", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": "Monitor cycle failed"}),
            }
        await asyncio.sleep(interval_seconds)


@router.get("/stream")
async def stream_position_monitor(
    interval: int = Query(default=30, ge=10, le=300),
    monitor: ActiveMonitor = Depends(get_active_monitor),
) -> EventSourceResponse:
    """SSE endpoint for real-time position monitoring.

    Default: polls every 30 seconds. Sends alerts for:
    - Profit targets hit (50%, 80%)
    - Stock approaching strike
    - Adverse intraday trends
    - Significant losses
    """
    return EventSourceResponse(
        _position_monitor_stream(monitor, interval)
    )
