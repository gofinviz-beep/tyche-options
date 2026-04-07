"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from collections.abc import AsyncGenerator

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from tyche.api.middleware import RequestTimingMiddleware, global_exception_handler
from tyche.config import get_settings
from tyche.logging import configure_logging
from tyche.models.backtest import (
    ExitSignal,
    PullbackEvent,
    StockPosition,
    TickerPullbackProfile,
)
from tyche.models.conviction import ConvictionSnapshot, ConvictionTransition
from tyche.models.scan import LLMAnalysisRecord, ScanCandidate, ScanRun
from tyche.persistence.database import (
    check_db_health,
    create_tables,
    create_tables_for_models,
    dispose_engine,
    init_backtest_db,
    init_conviction_db,
    init_db,
    init_scanner_dbs,
)
from tyche.telemetry import configure_telemetry, shutdown_telemetry

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
        pullback_strike_offset_pct=settings.pullback_strike_offset_pct,
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


async def _scheduled_ohlcv_refresh() -> None:
    """Fetch today's OHLCV data from Polygon after market close."""
    from tyche.api.deps import get_data_store, get_polygon
    from tyche.config import get_settings as _gs
    from tyche.market_data.data_store import bootstrap_ohlcv

    settings = _gs()
    try:
        polygon = get_polygon(settings)
        if polygon is None:
            logger.warning("ohlcv_refresh_skipped_no_polygon_key")
            return
        store = get_data_store(settings)
        result = await bootstrap_ohlcv(
            polygon, store, days=5, include_today=True,
        )
        logger.info("scheduled_ohlcv_refresh_complete", **result)
    except Exception:
        logger.error("scheduled_ohlcv_refresh_failed", exc_info=True)


async def _scheduled_exit_monitor() -> None:
    """Check active stock positions for exit signals after market close.

    Refreshes OHLCV data first as a safety net, in case the scheduled
    refresh job hasn't run or failed.
    """
    from tyche.api.deps import get_data_store, get_polygon
    from tyche.config import get_settings as _gs
    from tyche.market_data.data_store import bootstrap_ohlcv
    from tyche.workflow.exit_monitor import check_exit_signals

    settings = _gs()
    try:
        polygon = get_polygon(settings)
        store = get_data_store(settings)
        if polygon is not None:
            await bootstrap_ohlcv(
                polygon, store, days=5, include_today=True,
            )
        result = await check_exit_signals(store)
        logger.info(
            "scheduled_exit_monitor_complete",
            checked=result.positions_checked,
            profit_targets=result.profit_targets_hit,
            stop_losses=result.stop_losses_hit,
        )
    except Exception:
        logger.error("scheduled_exit_monitor_failed", exc_info=True)


async def _scheduled_options_snapshot() -> None:
    """Capture daily options chain snapshot from Tradier after market close.

    Fetches live put chains for all large-cap tickers and persists them
    to the OptionsChainStore.  Runs at ~120 RPM (Tradier hard limit),
    typically completing in ~30 minutes for ~1,100 tickers.
    """
    from tyche.config import get_settings as _gs
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
    from tyche.workflow.options_snapshot import run_options_snapshot

    settings = _gs()

    if not settings.tradier_api_token:
        logger.warning("options_snapshot_skipped_no_tradier_token")
        return

    try:
        ohlcv_store = OHLCVStore(data_dir=settings.data_dir)
        meta_store = TickerMetaStore(data_dir=settings.data_dir)

        all_tickers = ohlcv_store.get_all_tickers()
        if not all_tickers:
            logger.warning("options_snapshot_skipped_no_ohlcv_tickers")
            return

        tickers = all_tickers
        if meta_store.exists:
            tickers = meta_store.filter_equity_only(tickers)
            if settings.options_snapshot_min_market_cap > 0:
                caps = meta_store.get_market_caps(tickers)
                tickers = [
                    t for t in tickers
                    if caps.get(t, 0) >= settings.options_snapshot_min_market_cap
                ]

        logger.info("options_snapshot_starting", tickers=len(tickers))

        stats = await run_options_snapshot(
            tickers=tickers,
            settings=settings,
        )

        logger.info(
            "scheduled_options_snapshot_complete",
            tickers_succeeded=stats.tickers_succeeded,
            tickers_failed=stats.tickers_failed,
            contracts_stored=stats.contracts_stored,
            elapsed_seconds=round(stats.elapsed_seconds, 1),
        )
    except Exception:
        logger.error("scheduled_options_snapshot_failed", exc_info=True)


async def _scheduled_daily_digest() -> None:
    """Send a daily digest email with active pullbacks and transitions."""
    from datetime import date

    from tyche.config import get_settings as _gs
    from tyche.notification.dispatcher import NotificationDispatcher
    from tyche.persistence.conviction_repository import (
        get_active_pullbacks,
        get_transitions,
    )

    settings = _gs()
    today = date.today()

    try:
        pullbacks = await get_active_pullbacks(today)
        transitions = await get_transitions(
            from_date=today,
            to_date=today,
        )

        dispatcher = NotificationDispatcher.from_settings(settings)
        if dispatcher.channel_count > 0:
            await dispatcher.dispatch_daily_digest(
                pullbacks, transitions, context={"date": today.isoformat()}
            )
    except Exception:
        logger.error("scheduled_daily_digest_failed", exc_info=True)


async def _migrate_conviction_columns() -> None:
    """Add missing columns to existing conviction tables."""
    from tyche.persistence.database import _engines

    engine = _engines.get("conviction")
    if engine is None:
        return
    from sqlalchemy import text
    from sqlalchemy.exc import OperationalError

    migrations = [
        ("conviction_snapshots", "raw_conviction", "VARCHAR(10) DEFAULT 'none'"),
        ("conviction_transitions", "raw_conviction", "VARCHAR(10) DEFAULT 'none'"),
        ("conviction_snapshots", "prior_streak", "INTEGER DEFAULT 0"),
        ("conviction_snapshots", "ema_50", "REAL DEFAULT 0.0"),
        ("conviction_snapshots", "ema_50_slope", "REAL DEFAULT 0.0"),
        ("conviction_snapshots", "rsi_14", "REAL DEFAULT 0.0"),
        ("conviction_snapshots", "iv_rank", "REAL DEFAULT NULL"),
        ("conviction_snapshots", "iv_percentile", "REAL DEFAULT NULL"),
        ("conviction_snapshots", "atm_iv", "REAL DEFAULT NULL"),
        ("conviction_snapshots", "vrp", "REAL DEFAULT NULL"),
    ]

    async with engine.begin() as conn:
        for table, column, col_type in migrations:
            try:
                await conn.execute(text(
                    f"ALTER TABLE {table} ADD COLUMN {column} {col_type}"
                ))
                logger.info("migration_column_added", table=table, column=column)
            except OperationalError:
                pass


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """Application lifespan: startup and shutdown hooks."""
    settings = get_settings()

    configure_telemetry(
        service_name=settings.otel_service_name,
        gcp_project_id=settings.gcp_project_id,
        enabled=settings.otel_enabled,
    )
    configure_logging(log_level=settings.log_level)

    logger.info(
        "tyche_starting",
        sandbox=settings.tradier_sandbox,
        preview_only=settings.preview_only_mode,
        watchlist_count=len(settings.watchlist_symbols),
        otel_enabled=settings.otel_enabled,
        gcp_project=bool(settings.gcp_project_id),
    )

    init_db(settings.effective_database_url)
    await create_tables()

    init_scanner_dbs(settings.db_dir)
    await create_tables_for_models("scans", ScanRun)
    await create_tables_for_models("candidates", ScanCandidate)
    await create_tables_for_models("analyses", LLMAnalysisRecord)

    init_conviction_db(settings.db_dir)
    await create_tables_for_models(
        "conviction", ConvictionSnapshot, ConvictionTransition
    )
    await _migrate_conviction_columns()

    init_backtest_db(settings.db_dir)
    await create_tables_for_models(
        "backtest", PullbackEvent, TickerPullbackProfile,
        StockPosition, ExitSignal,
    )

    from tyche.api.deps import get_scheduler

    scheduler = get_scheduler()
    scheduler.schedule_morning_scan(_scheduled_morning_scan)
    scheduler.schedule_ohlcv_refresh(_scheduled_ohlcv_refresh)
    scheduler.schedule_exit_monitor(_scheduled_exit_monitor)

    if settings.options_snapshot_enabled and settings.tradier_api_token:
        parts = settings.options_snapshot_time.split(":")
        snap_h = int(parts[0]) if len(parts) >= 1 else 16
        snap_m = int(parts[1]) if len(parts) >= 2 else 10
        scheduler.schedule_options_snapshot(
            _scheduled_options_snapshot, hour=snap_h, minute=snap_m
        )

    if settings.daily_digest_enabled:
        parts = settings.daily_digest_time.split(":")
        digest_h = int(parts[0]) if len(parts) >= 1 else 16
        digest_m = int(parts[1]) if len(parts) >= 2 else 0
        scheduler.schedule_daily_digest(
            _scheduled_daily_digest, hour=digest_h, minute=digest_m
        )

    scheduler.start()

    logger.info("tyche_ready")
    yield

    scheduler.shutdown()
    await dispose_engine()
    shutdown_telemetry()
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

    app.add_middleware(RequestTimingMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://localhost:3000"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.add_exception_handler(Exception, global_exception_handler)

    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        pass

    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument()
    except Exception:
        pass

    from tyche.api.routes import (
        account,
        conviction,
        events,
        intents,
        monitor,
        orders,
        scanner,
        stocks,
        system,
        telemetry,
        watchlist,
    )

    app.include_router(account.router, prefix="/api/v1")
    app.include_router(stocks.router, prefix="/api/v1")
    app.include_router(scanner.router, prefix="/api/v1")
    app.include_router(orders.router, prefix="/api/v1")
    app.include_router(watchlist.router, prefix="/api/v1")
    app.include_router(events.router, prefix="/api/v1")
    app.include_router(system.router, prefix="/api/v1")
    app.include_router(conviction.router, prefix="/api/v1")
    app.include_router(intents.router, prefix="/api/v1")
    app.include_router(monitor.router, prefix="/api/v1")
    app.include_router(telemetry.router, prefix="/api/v1")

    @app.get("/health")
    async def health_check() -> dict[str, str]:
        return {
            "status": "ok",
            "mode": "sandbox" if settings.tradier_sandbox else "live",
            "trading": "preview_only" if settings.preview_only_mode else "live_enabled",
        }

    @app.get("/health/ready")
    async def readiness_check() -> dict[str, str | bool]:
        db_ok = await check_db_health()
        status = "ok" if db_ok else "degraded"
        return {
            "status": status,
            "database": db_ok,
            "mode": "sandbox" if settings.tradier_sandbox else "live",
        }

    return app


app = create_app()
