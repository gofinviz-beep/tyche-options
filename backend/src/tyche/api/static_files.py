"""Serve the built SPA from the API process.

Used by the single-container Cloud Run deployment: the same origin serves
``/api/v1`` and the React bundle, which is why ``frontend/src/api/client.ts`` can
keep its relative ``BASE_URL = "/api/v1"`` with no build-time configuration.

Local development does not use this — Vite serves the SPA on its own port and
proxies ``/api`` to uvicorn, so ``static_dir`` is empty and nothing is mounted.
"""

from __future__ import annotations

from pathlib import Path

import structlog
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse

logger = structlog.get_logger()

# Prefixes owned by the API. A request under one of these that reached the SPA
# catch-all is a genuine 404, not a client route — returning index.html there
# would hand API callers an HTML page with a 200.
_API_PREFIXES = ("api/", "health", "docs", "redoc", "openapi.json")

# Vite emits content-hashed filenames under assets/, so they can never go stale.
_IMMUTABLE = "public, max-age=31536000, immutable"

# Everything else (index.html, favicon) keeps its name across deploys, so it must
# be revalidated or a new revision would keep serving the previous bundle.
_REVALIDATE = "no-cache"


def mount_spa(app: FastAPI, static_dir: str) -> bool:
    """Serve the built SPA from ``app``.

    Must be called AFTER the API routers are registered: the catch-all matches
    any unclaimed path, so it would otherwise shadow every API route.

    Args:
        app: The application to serve from.
        static_dir: Directory holding the Vite build output (``dist/``).

    Returns:
        True if the SPA was wired up, False if ``static_dir`` is unset or has no
        ``index.html`` (in which case the API still serves normally).
    """
    if not static_dir:
        return False

    root = Path(static_dir).resolve()
    index = root / "index.html"
    if not index.is_file():
        logger.warning("spa_static_dir_missing_index", static_dir=str(root))
        return False

    def _resolve(rel_path: str) -> Path | None:
        """Resolve a request path to a file inside ``root``, or None."""
        candidate = (root / rel_path).resolve()
        try:
            candidate.relative_to(root)
        except ValueError:
            return None  # Path traversal attempt.
        return candidate if candidate.is_file() else None

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> FileResponse:
        """Serve a build artifact, falling back to index.html for client routes.

        The fallback is what lets a deep link like ``/stocks/screener`` survive a
        refresh: the browser asks the server for a path only React knows about.
        """
        if full_path.startswith(_API_PREFIXES):
            raise HTTPException(status_code=404, detail="Not Found")

        if full_path and (found := _resolve(full_path)) is not None:
            cache = _IMMUTABLE if full_path.startswith("assets/") else _REVALIDATE
            return FileResponse(found, headers={"Cache-Control": cache})

        return FileResponse(index, headers={"Cache-Control": _REVALIDATE})

    logger.info("spa_mounted", static_dir=str(root))
    return True


__all__ = ["mount_spa"]
