"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tyche.config import get_settings
from tyche.logging import configure_logging
from tyche.persistence.database import create_tables, dispose_engine, init_db

logger = structlog.get_logger()


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    settings = get_settings()
    configure_logging()

    logger.info(
        "tyche_starting",
        sandbox=settings.tradier_sandbox,
        preview_only=settings.preview_only_mode,
        watchlist_count=len(settings.watchlist_symbols),
    )

    init_db(settings.database_url)
    await create_tables()

    logger.info("tyche_ready")
    yield

    await dispose_engine()
    logger.info("tyche_shutdown")


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title="Tyche Options",
        description="Laptop-based options trading copilot — Wheel Strategy focused",
        version="0.1.0",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from tyche.api.routes import account, events, orders, scanner, system, watchlist

    app.include_router(account.router, prefix="/api/v1")
    app.include_router(scanner.router, prefix="/api/v1")
    app.include_router(orders.router, prefix="/api/v1")
    app.include_router(watchlist.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "sandbox" if settings.tradier_sandbox else "live",
            "trading": "preview_only" if settings.preview_only_mode else "live_enabled",
        }

    return app


app = create_app()
