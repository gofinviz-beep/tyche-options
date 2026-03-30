"""HTTP middleware — request timing, correlation IDs, and global error handling."""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.responses import Response

from tyche.telemetry import api_errors, http_request_duration

logger = structlog.get_logger()

_SKIP_LOG_PATHS = frozenset({"/health", "/health/ready"})


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Measure request duration, inject correlation IDs, and log every request."""

    async def dispatch(
        self,
        request: Request,
        call_next: RequestResponseEndpoint,
    ) -> Response:
        request_id = request.headers.get("x-request-id") or str(uuid.uuid4())
        structlog.contextvars.bind_contextvars(request_id=request_id)

        start = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration = time.perf_counter() - start
            logger.error(
                "http_request_unhandled",
                method=request.method,
                path=request.url.path,
                duration_ms=round(duration * 1000, 2),
                exc_info=True,
            )
            api_errors.add(
                1,
                {"route": request.url.path, "status_code": "500", "error_type": "unhandled"},
            )
            http_request_duration.record(
                duration,
                {"http.method": request.method, "http.route": request.url.path, "http.status_code": "500"},
            )
            raise
        finally:
            structlog.contextvars.unbind_contextvars("request_id")

        duration = time.perf_counter() - start
        status = str(response.status_code)

        response.headers["X-Request-Id"] = request_id

        http_request_duration.record(
            duration,
            {"http.method": request.method, "http.route": request.url.path, "http.status_code": status},
        )

        if request.url.path not in _SKIP_LOG_PATHS:
            logger.info(
                "http_request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=round(duration * 1000, 2),
            )

        if response.status_code >= 400:
            api_errors.add(
                1,
                {"route": request.url.path, "status_code": status, "error_type": "http"},
            )

        return response


async def global_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Catch-all for unhandled exceptions — guarantees structured logging."""
    logger.error(
        "unhandled_exception",
        method=request.method,
        path=request.url.path,
        error=str(exc),
        exc_info=True,
    )
    api_errors.add(
        1,
        {"route": request.url.path, "status_code": "500", "error_type": "unhandled"},
    )
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error"},
    )
