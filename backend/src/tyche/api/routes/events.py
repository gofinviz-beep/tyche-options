"""Server-Sent Events route — real-time order monitoring stream."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncGenerator
from typing import Any

import structlog
from fastapi import APIRouter, Depends
from sse_starlette.sse import EventSourceResponse

from tyche.api.deps import get_analysis_agent, get_broker
from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient
from tyche.workflow.order_monitor import run_order_monitor

logger = structlog.get_logger()
router = APIRouter(prefix="/events", tags=["events"])


async def _order_monitor_stream(
    broker: BrokerClient,
    analysis: AnalysisAgent | None,
    interval_seconds: int = 60,
) -> AsyncGenerator[dict[str, Any], None]:
    """Generate SSE events from periodic order monitoring."""
    while True:
        try:
            result = await run_order_monitor(
                broker=broker, analysis_agent=analysis
            )

            yield {
                "event": "order_monitor",
                "data": json.dumps({
                    "monitored_at": result.monitored_at.isoformat(),
                    "orders_checked": result.orders_checked,
                    "alerts": result.alerts,
                    "analyses": [a.model_dump() for a in result.analyses],
                    "errors": result.errors,
                }),
            }
        except asyncio.CancelledError:
            logger.info("sse_stream_cancelled")
            return
        except Exception:
            logger.warning("sse_monitor_error", exc_info=True)
            yield {
                "event": "error",
                "data": json.dumps({"error": "Monitor cycle failed"}),
            }

        await asyncio.sleep(interval_seconds)


@router.get("/orders")
async def stream_order_monitor(
    broker: BrokerClient = Depends(get_broker),
    analysis: AnalysisAgent | None = Depends(get_analysis_agent),
) -> EventSourceResponse:
    """SSE endpoint for real-time order monitoring updates."""
    return EventSourceResponse(
        _order_monitor_stream(broker, analysis)
    )
