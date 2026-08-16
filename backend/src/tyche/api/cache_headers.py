"""Conditional-GET support for routes backed by published artifacts.

These routes serve the output of one nightly publish run — the screener payload
alone is ~2.4 MiB — and that output does not change until the next run. Tagging
the responses with the publish ``run_id`` lets the browser revalidate cheaply and
skip re-downloading megabytes it already has.

The ETag covers the run id plus the full request path and query string, because
most of these routes apply server-side filters, so two query strings against the
same run are genuinely different payloads.

This saves bandwidth, not server work: the response body is still produced before
the comparison happens. Cutting the compute too would mean hoisting the run id
lookup above the route, which is not worth the coupling.
"""

from __future__ import annotations

import hashlib
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from tyche.config import get_settings
from tyche.persistence.published_cache import get_published_cache
from tyche.storage.paths import storage_context_from_settings

logger = structlog.get_logger()

# Route prefixes (under the API prefix) whose payload comes from a publish run.
CACHEABLE_PREFIXES: tuple[str, ...] = (
    "/stocks/screener",
    "/stocks/conviction",
    "/stocks/deep-dips",
    "/stocks/history",
    "/stocks/alpha",
    "/alpha/scan",
    "/alpha/persistence",
    "/conviction/scan",
    "/scanner/latest",
    "/news/signals",
    "/filings/signals",
)

# Short max-age with a long stale-while-revalidate: the browser serves instantly
# from cache and revalidates in the background, and revalidation is a 304 for the
# rest of the publish cycle.
_CACHE_CONTROL = "private, max-age=60, stale-while-revalidate=600"


def _is_cacheable(path: str) -> bool:
    return any(prefix in path for prefix in CACHEABLE_PREFIXES)


def _compute_etag(run_id: str, request: Request) -> str:
    raw = f"{run_id}|{request.url.path}?{request.url.query}"
    digest = hashlib.sha256(raw.encode()).hexdigest()[:32]
    return f'W/"{digest}"'


def _resolve_settings(request: Request):
    """Return the settings this app is running with.

    Honours ``app.dependency_overrides`` so the middleware sees the same
    settings as the routes. Middleware sits outside FastAPI's dependency
    injection, so without this it would read the process-wide settings and
    ignore an override — which in tests means reaching for the real storage
    backend configured in ``.env`` instead of the isolated one.
    """
    from tyche.api import deps

    override = getattr(request.app, "dependency_overrides", {}).get(deps.get_settings)
    if override is not None:
        return override()
    return get_settings()


class PublishedCacheHeadersMiddleware(BaseHTTPMiddleware):
    """Add ``ETag``/``Cache-Control`` and answer ``304`` on published routes."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        if request.method not in ("GET", "HEAD") or not _is_cacheable(
            request.url.path
        ):
            return await call_next(request)

        settings = _resolve_settings(request)
        if not settings.published_cache_enabled:
            return await call_next(request)

        run_id = self._run_id(settings)
        if not run_id:
            # No publish manifest (local dev): nothing stable to key an ETag on.
            return await call_next(request)

        etag = _compute_etag(run_id, request)

        if request.headers.get("if-none-match") == etag:
            return Response(
                status_code=304,
                headers={"ETag": etag, "Cache-Control": _CACHE_CONTROL},
            )

        response = await call_next(request)
        if response.status_code == 200:
            response.headers["ETag"] = etag
            response.headers["Cache-Control"] = _CACHE_CONTROL
        return response

    @staticmethod
    def _run_id(settings) -> str | None:
        """Return the current publish run id, or None when unavailable."""
        try:
            cache = get_published_cache(
                manifest_ttl_seconds=settings.published_manifest_ttl_seconds,
            )
            manifest = cache.manifest(storage_context_from_settings(settings))
        except Exception as exc:
            # Never fail a request over a caching optimization.
            logger.warning("cache_header_run_id_failed", error=str(exc))
            return None
        return manifest.run_id if manifest else None


__all__ = ["CACHEABLE_PREFIXES", "PublishedCacheHeadersMiddleware"]
