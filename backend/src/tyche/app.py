"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tyche.config import get_settings
from tyche.logging import configure_logging
from tyche.models.scan import LLMAnalysisRecord, ScanCandidate, ScanRun
from tyche.persistence.database import (
    create_tables,
    create_tables_for_models,
    dispose_engine,
    init_db,
    init_scanner_dbs,
)

logger = structlog.get_logger()


async def _scheduled_morning_scan() -> None:
    """Wrapper that runs the morning scan with proper dependency resolution.

    Called by APScheduler — resolves all dependencies at call time so
    singletons are already initialized by the app lifespan.
    """
    from tyche.api.deps import (
        get_analysis_agent,
        get_broker,
        get_conviction_engine,
        get_data_store,
        get_earnings_client,
        get_settings as dep_settings,
        get_strategy_engine,
        get_universe_builder,
    )
    from tyche.persistence.database import get_session
    from tyche.persistence.scan_repository import cleanup_old_scans, save_scan
    from tyche.workflow.intent_builder import create_intents_from_scan
    from tyche.workflow.morning_scan import run_morning_scan

    settings = dep_settings()
    result = await run_morning_scan(
        broker=get_broker(settings),
        strategy_engine=get_strategy_engine(settings),
        analysis_agent=get_analysis_agent(None),
        earnings_client=get_earnings_client(settings),
        universe_builder=get_universe_builder(settings),
        watchlist=settings.watchlist_symbols,
        conviction_engine=get_conviction_engine(settings),
        data_store=get_data_store(settings),
        top_n=5,
    )

    intents_created = 0
    if result.csp_analyses:
        try:
            async with get_session() as session:
                intents = await create_intents_from_scan(
                    session=session,
                    scan_id=result.scan_id,
                    csp_analyses=result.csp_analyses,
                    csp_candidates=result.csp_candidates,
                    conviction_signals=result.conviction_signals,
                )
                intents_created = len(intents)
        except Exception:
            logger.error("scheduled_intent_creation_failed", exc_info=True)

    try:
        await save_scan(result, intents_created=intents_created, trigger="scheduled")
        await cleanup_old_scans(settings.scan_retention_count)
    except Exception:
        logger.error("scheduled_scan_persistence_failed", exc_info=True)

    logger.info(
        "scheduled_morning_scan_complete",
        scan_id=result.scan_id,
        candidates=len(result.csp_candidates),
        analyses=len(result.csp_analyses),
        errors=len(result.errors),
    )


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

    init_db(settings.effective_database_url)
    await create_tables()

    init_scanner_dbs(settings.db_dir)
    await create_tables_for_models("scans", ScanRun)
    await create_tables_for_models("candidates", ScanCandidate)
    await create_tables_for_models("analyses", LLMAnalysisRecord)

    from tyche.api.deps import get_scheduler

    scheduler = get_scheduler()
    scheduler.schedule_morning_scan(_scheduled_morning_scan)
    scheduler.start()

    logger.info("tyche_ready")
    yield

    scheduler.shutdown()
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

    from tyche.api.routes import (
        account,
        conviction,
        events,
        intents,
        monitor,
        orders,
        scanner,
        system,
        watchlist,
    )

    app.include_router(account.router, prefix="/api/v1")
    app.include_router(scanner.router, prefix="/api/v1")
    app.include_router(orders.router, prefix="/api/v1")
    app.include_router(watchlist.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(conviction.router, prefix="/api/v1")
    app.include_router(intents.router, prefix="/api/v1")
    app.include_router(monitor.router, prefix="/api/v1")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "sandbox" if settings.tradier_sandbox else "live",
            "trading": "preview_only" if settings.preview_only_mode else "live_enabled",
        }

    return app


app = create_app()
