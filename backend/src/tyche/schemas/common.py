"""Common Pydantic schemas shared across the API."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class StatusResponse(BaseModel):
    """Generic API status response."""

    status: str = "ok"
    message: str = ""
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class ErrorResponse(BaseModel):
    """Structured error response."""

    status: str = "error"
    error_type: str
    message: str
    details: dict[str, Any] | None = None
    timestamp: datetime = Field(default_factory=datetime.utcnow)


class PaginatedResponse(BaseModel):
    """Wrapper for paginated list responses."""

    items: list[Any]
    total: int
    page: int = 1
    page_size: int = 50
