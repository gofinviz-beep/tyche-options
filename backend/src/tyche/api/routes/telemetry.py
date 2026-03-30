"""Telemetry routes — ingests frontend error and timing events."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from tyche.telemetry import api_errors, http_request_duration

logger = structlog.get_logger()
router = APIRouter(prefix="/telemetry", tags=["telemetry"])

_MAX_EVENTS_PER_BATCH = 50


class TelemetryEvent(BaseModel):
    """A single telemetry event reported by the frontend."""

    type: str = Field(description="'error' | 'timing' | 'crash'")
    path: str = ""
    status: int | None = None
    message: str = ""
    duration_ms: float | None = None
    timestamp: str = ""
    extra: dict[str, Any] = Field(default_factory=dict)


class TelemetryBatch(BaseModel):
    """Batch of telemetry events from the frontend."""

    events: list[TelemetryEvent] = Field(max_length=_MAX_EVENTS_PER_BATCH)


@router.post("/events")
async def ingest_telemetry(batch: TelemetryBatch, request: Request) -> dict[str, int]:
    """Accept frontend telemetry events and log them as structured events.

    Events flow to Cloud Logging as JSON; error events also increment the
    ``api.errors`` counter so they appear in Cloud Monitoring dashboards.
    """
    processed = 0
    for event in batch.events:
        match event.type:
            case "error":
                logger.warning(
                    "frontend_error",
                    source="frontend",
                    path=event.path,
                    status=event.status,
                    message=event.message,
                    duration_ms=event.duration_ms,
                    **event.extra,
                )
                api_errors.add(
                    1,
                    {
                        "route": event.path,
                        "status_code": str(event.status or 0),
                        "error_type": "frontend",
                    },
                )
            case "crash":
                logger.error(
                    "frontend_crash",
                    source="frontend",
                    message=event.message,
                    **event.extra,
                )
                api_errors.add(
                    1,
                    {"route": "render", "status_code": "0", "error_type": "frontend_crash"},
                )
            case "timing":
                if event.duration_ms is not None:
                    http_request_duration.record(
                        event.duration_ms / 1000,
                        {
                            "http.method": "GET",
                            "http.route": event.path,
                            "http.status_code": str(event.status or 200),
                        },
                    )
                logger.debug(
                    "frontend_timing",
                    source="frontend",
                    path=event.path,
                    duration_ms=event.duration_ms,
                    status=event.status,
                )
            case _:
                logger.debug(
                    "frontend_event",
                    source="frontend",
                    type=event.type,
                    message=event.message,
                    **event.extra,
                )
        processed += 1

    return {"processed": processed}
