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
from tyche.models.filing import FilingSignal
from tyche.models.news import NewsSignal
from tyche.persistence.database import (
    check_db_health,
    create_tables,
    create_tables_for_models,
    dispose_engine,
    init_backtest_db,
    init_conviction_db,
    init_db,
    init_news_db,
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


async def _scheduled_news_ingest() -> None:
    """Wrapper for the scheduled news ingestion pipeline."""
    from tyche.workflow.news_pipeline import run_news_pipeline

    try:
        result = await run_news_pipeline()
        logger.info(
            "scheduled_news_ingest_complete",
            articles_classified=result.articles_classified,
            signals_rebuilt=result.signals_rebuilt,
            errors=len(result.errors),
        )
    except Exception:
        logger.error("scheduled_news_ingest_failed", exc_info=True)


async def _scheduled_edgar_ingest() -> None:
    """Wrapper for the scheduled EDGAR filing ingestion pipeline."""
    from tyche.workflow.edgar_pipeline import run_edgar_pipeline

    try:
        result = await run_edgar_pipeline()
        logger.info(
            "scheduled_edgar_ingest_complete",
            eightk_classified=result.eightk_classified,
            insider_tx=result.insider_tx_persisted,
            signals_rebuilt=result.signals_rebuilt,
            errors=len(result.errors),
        )
    except Exception:
        logger.error("scheduled_edgar_ingest_failed", exc_info=True)


async def _scheduled_conviction_batch() -> None:
    """Run conviction batch after daily OHLCV refresh."""
    from tyche.api.deps import get_data_store, get_conviction_engine
    from tyche.config import get_settings as _gs
    from tyche.market_data.data_store import TickerMetaStore
    from tyche.workflow.conviction_batch import run_conviction_batch

    settings = _gs()
    try:
        store = get_data_store(settings)
        meta_store = TickerMetaStore(data_dir=settings.data_dir)
        engine = get_conviction_engine(settings)

        result = await run_conviction_batch(
            data_store=store,
            conviction_engine=engine,
            ticker_meta_store=meta_store,
            min_market_cap=settings.conviction_batch_min_market_cap_millions * 1_000_000,
            min_price=settings.conviction_batch_min_price,
            min_avg_volume=settings.conviction_batch_min_avg_volume,
            retention_days=settings.conviction_snapshot_retention_days,
        )
        logger.info(
            "scheduled_conviction_batch_complete",
            signals=result.signals_computed,
            snapshots=result.snapshots_upserted,
            transitions=result.transitions_detected,
            duration_ms=round(result.duration_ms),
        )
    except Exception:
        logger.error("scheduled_conviction_batch_failed", exc_info=True)


async def _scheduled_bridge_tradier_iv() -> None:
    """Bridge Tradier options snapshots into IV/derived metrics pipeline."""
    import asyncio
    from tyche.config import get_settings as _gs

    settings = _gs()
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_bridge_tradier_iv_sync, settings.data_dir)
        logger.info("scheduled_bridge_tradier_iv_complete")
    except Exception:
        logger.error("scheduled_bridge_tradier_iv_failed", exc_info=True)


def _run_bridge_tradier_iv_sync(data_dir: str) -> None:
    """Run Tradier IV bridge in a thread (CPU-bound derived metrics)."""
    import math
    from datetime import date, timedelta

    from tyche.market_data.data_store import OHLCVStore, OptionsChainStore
    from tyche.market_data.derived_store import DerivedMetricsStore
    from tyche.market_data.historical_iv_store import HistoricalIVStore

    snapshot_date = date.today()
    chain_store = OptionsChainStore(data_dir=data_dir)
    ohlcv_store = OHLCVStore(data_dir=data_dir)
    iv_store = HistoricalIVStore(data_dir=data_dir)
    derived_store = DerivedMetricsStore(data_dir=data_dir)

    available_dates = chain_store.list_snapshot_dates()
    if snapshot_date not in available_dates:
        logger.info("bridge_iv_no_snapshot", date=str(snapshot_date))
        return

    all_tickers = sorted(
        p.stem.upper()
        for p in chain_store.store_dir.glob("*.parquet")
        if not p.name.startswith("_")
    )

    iv_written = 0
    derived_written = 0
    target_dte = 30
    dte_tolerance = 15

    for ticker in all_tickers:
        try:
            df = chain_store.read_ticker(ticker, snapshot_date=snapshot_date, option_type="put")
            if df.empty:
                continue

            ohlcv_df = ohlcv_store.read_ticker(ticker)
            if ohlcv_df.empty:
                continue

            ohlcv_df["date"] = ohlcv_df["date"].apply(
                lambda d: d.date() if hasattr(d, "date") else d
            )
            close_row = ohlcv_df[ohlcv_df["date"] == snapshot_date]
            if close_row.empty:
                yesterday = snapshot_date - timedelta(days=1)
                close_row = ohlcv_df[ohlcv_df["date"] == yesterday]
            if close_row.empty:
                close_row = ohlcv_df.sort_values("date").tail(1)
            if close_row.empty:
                continue

            underlying_close = float(close_row.iloc[-1]["close"])
            if underlying_close <= 0:
                continue

            df = df.copy()
            df["dte"] = df["expiration"].apply(
                lambda exp: (exp - snapshot_date).days if exp > snapshot_date else 0
            )
            df = df[df["dte"] > 0]
            if df.empty:
                continue

            dte_diff = (df["dte"] - target_dte).abs()
            within_tolerance = df[dte_diff <= dte_tolerance + 10]
            if within_tolerance.empty:
                within_tolerance = df

            best_dte_idx = (within_tolerance["dte"] - target_dte).abs().idxmin()
            best_dte = within_tolerance.loc[best_dte_idx, "dte"]
            dte_group = within_tolerance[within_tolerance["dte"] == best_dte]

            atm_idx = (dte_group["strike"] - underlying_close).abs().idxmin()
            row = dte_group.loc[atm_idx]

            iv = float(row.get("implied_volatility", 0))
            strike = float(row["strike"])
            dte_val = int(row["dte"])
            option_close = float(row.get("last", 0) or row.get("mid", 0))

            if iv <= 0 or math.isnan(iv) or dte_val <= 0:
                continue

            iv_store.write_iv_data(ticker, [{
                "date": snapshot_date,
                "strike": strike,
                "expiration": row["expiration"],
                "contract_ticker": f"O:{ticker}_TRADIER_SNAPSHOT",
                "option_close": option_close,
                "underlying_close": underlying_close,
                "dte": dte_val,
                "implied_volatility": iv,
            }])
            iv_written += 1

            iv_df = iv_store.read_ticker(ticker)
            ohlcv_full = ohlcv_store.read_ticker(ticker)
            metrics_df = DerivedMetricsStore.compute_metrics(iv_df, ohlcv_full)
            if not metrics_df.empty:
                derived_store.write_metrics(ticker, metrics_df)
                derived_written += 1

        except Exception:
            continue

    if iv_written > 0:
        iv_store.write_checkpoint(
            last_options_date=snapshot_date.isoformat(),
            tickers_processed=iv_written,
            iv_points=iv_written,
        )

    logger.info(
        "bridge_tradier_iv_sync_complete",
        iv_written=iv_written,
        derived_written=derived_written,
    )


async def _scheduled_correlation_refresh() -> None:
    """Monthly refresh of rolling correlations and betas."""
    import asyncio
    from tyche.config import get_settings as _gs

    settings = _gs()
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_correlation_refresh_sync, settings.data_dir)
        logger.info("scheduled_correlation_refresh_complete")
    except Exception:
        logger.error("scheduled_correlation_refresh_failed", exc_info=True)


def _run_correlation_refresh_sync(data_dir: str) -> None:
    """Compute 60d rolling correlations in a thread (CPU-bound matrix ops)."""
    from tyche.market_data.correlation_store import (
        CorrelationStore,
        compute_rolling_correlations,
    )
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore

    ohlcv_store = OHLCVStore(data_dir=data_dir)
    meta_store = TickerMetaStore(data_dir=data_dir)

    all_tickers = ohlcv_store.get_all_tickers()
    if meta_store.exists:
        all_tickers = meta_store.filter_equity_only(all_tickers)
        market_caps = meta_store.get_market_caps()
        all_tickers = [
            t for t in all_tickers
            if market_caps.get(t, float("inf")) >= 4e9
        ]

    corr_df, beta_df = compute_rolling_correlations(
        ohlcv_store=ohlcv_store,
        tickers=all_tickers,
        window=60,
        top_n=20,
    )

    store = CorrelationStore(data_dir=data_dir)
    if not corr_df.empty:
        store.write_correlations(corr_df)
    if not beta_df.empty:
        store.write_betas(beta_df)


async def _scheduled_etf_refresh() -> None:
    """Quarterly ETF constituent list refresh."""
    import asyncio
    from tyche.config import get_settings as _gs

    settings = _gs()
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_etf_refresh_sync, settings.data_dir)
        logger.info("scheduled_etf_refresh_complete")
    except Exception:
        logger.error("scheduled_etf_refresh_failed", exc_info=True)


def _run_etf_refresh_sync(data_dir: str) -> None:
    """Build ETF data from static lists + yfinance in a thread."""
    from tyche.market_data.etf_store import ETFConstituentStore, build_etf_data

    etf_data = build_etf_data(use_yfinance=True)
    store = ETFConstituentStore(data_dir=data_dir)
    store.write_all(etf_data)


async def _scheduled_quarterly_meta() -> None:
    """Quarterly sector/SIC + institutional ownership refresh."""
    from tyche.api.deps import get_polygon
    from tyche.config import get_settings as _gs
    from tyche.market_data.data_store import TickerMetaStore, _backfill_sic_data

    settings = _gs()
    try:
        polygon = get_polygon(settings)
        if polygon is None:
            logger.warning("quarterly_meta_skipped_no_polygon_key")
            return

        meta_store = TickerMetaStore(data_dir=settings.data_dir)

        sic_updated = await _backfill_sic_data(
            polygon, meta_store,
            concurrency=settings.polygon_market_cap_concurrency,
            rate_limit_rpm=settings.polygon_rate_limit_rpm,
        )
        logger.info("quarterly_sic_refresh_complete", updated=sic_updated)

    except Exception:
        logger.error("quarterly_sic_refresh_failed", exc_info=True)

    try:
        from tyche.market_data.institutional import get_institutional_ownership
        from tyche.market_data.data_store import TickerMetaStore

        meta_store = TickerMetaStore(data_dir=settings.data_dir)
        if not meta_store.exists:
            return

        import asyncio

        all_tickers = sorted(meta_store.filter_equity_only(
            list(meta_store.get_ticker_types().keys())
        ))
        existing = meta_store.get_institutional_pcts(all_tickers)
        missing = [t for t in all_tickers if t not in existing]

        if not missing:
            logger.info("quarterly_institutional_already_complete")
            return

        results: dict[str, float] = {}
        sem = asyncio.Semaphore(5)

        async def _fetch(ticker: str) -> None:
            async with sem:
                await asyncio.sleep(0.5)
                try:
                    pct = await get_institutional_ownership(ticker)
                    if pct is not None:
                        results[ticker] = pct
                except Exception:
                    pass

        batch_size = 50
        for i in range(0, len(missing), batch_size):
            batch = missing[i:i + batch_size]
            await asyncio.gather(*[_fetch(t) for t in batch])
            if results:
                meta_store.update_institutional_pcts(results)
                results.clear()

        logger.info("quarterly_institutional_complete", tickers=len(missing))
    except Exception:
        logger.error("quarterly_institutional_failed", exc_info=True)


async def _scheduled_weekly_meta() -> None:
    """Weekly ticker metadata refresh from Polygon."""
    from tyche.api.deps import get_polygon
    from tyche.config import get_settings as _gs
    from tyche.market_data.data_store import TickerMetaStore, _backfill_market_caps

    settings = _gs()
    try:
        polygon = get_polygon(settings)
        if polygon is None:
            logger.warning("weekly_meta_skipped_no_polygon_key")
            return

        meta_store = TickerMetaStore(data_dir=settings.data_dir)

        ticker_infos = await polygon.get_tickers(
            market="stocks", active=True, ticker_type="CS",
        )
        if ticker_infos:
            count = meta_store.write_meta(ticker_infos)
            logger.info("weekly_meta_tickers_written", count=count)

            updated = await _backfill_market_caps(
                polygon, meta_store,
                concurrency=settings.polygon_market_cap_concurrency,
                rate_limit_rpm=settings.polygon_rate_limit_rpm,
            )
            logger.info("weekly_meta_caps_updated", count=updated)

    except Exception:
        logger.error("scheduled_weekly_meta_failed", exc_info=True)


async def _scheduled_ml_retrain() -> None:
    """Monthly retrain of the XGBoost CSP safety model."""
    import asyncio

    from tyche.config import get_settings
    from tyche.api.deps import reset_all

    settings = get_settings()
    try:
        loop = asyncio.get_running_loop()
        await loop.run_in_executor(None, _run_ml_retrain_sync, settings.data_dir)
        reset_all()
        logger.info("scheduled_ml_retrain_complete")
    except Exception:
        logger.error("scheduled_ml_retrain_failed", exc_info=True)


def _run_ml_retrain_sync(data_dir: str) -> None:
    """Run ML retrain in a thread (XGBoost training is CPU-bound)."""
    from tyche.ml.dataset import build_dataset
    from tyche.ml.xgb_baseline import train_production_model

    dataset = build_dataset(
        data_dir=data_dir,
        include_neighbors=True,
        include_etf=True,
        include_correlation=True,
    )
    if dataset.empty:
        logger.error("ml_retrain_empty_dataset")
        return

    train_production_model(
        dataset=dataset,
        target="csp_win_5d",
        data_dir=data_dir,
    )


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
        ("conviction_snapshots", "conviction_score", "REAL DEFAULT 0.0"),
        ("conviction_snapshots", "csp_safety_prob", "REAL DEFAULT NULL"),
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

    init_news_db(settings.db_dir)
    await create_tables_for_models("news", NewsSignal, FilingSignal)

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

    if settings.news_ingestion_enabled:
        scheduler.schedule_news_ingest(
            _scheduled_news_ingest,
            interval_minutes=settings.news_ingest_interval_minutes,
        )

    if settings.edgar_ingestion_enabled and settings.edgar_user_agent_email:
        scheduler.schedule_edgar_ingest(
            _scheduled_edgar_ingest,
            interval_minutes=settings.edgar_ingest_interval_minutes,
        )

    if settings.ml_retrain_enabled:
        parts = settings.ml_retrain_time.split(":")
        retrain_h = int(parts[0]) if len(parts) >= 1 else 2
        retrain_m = int(parts[1]) if len(parts) >= 2 else 0
        scheduler.schedule_ml_retrain(
            _scheduled_ml_retrain,
            day=settings.ml_retrain_day_of_month,
            hour=retrain_h,
            minute=retrain_m,
        )

    if settings.conviction_batch_after_ohlcv:
        scheduler.schedule_conviction_batch(_scheduled_conviction_batch)

    if settings.bridge_tradier_iv_enabled and settings.tradier_api_token:
        scheduler.schedule_bridge_tradier_iv(_scheduled_bridge_tradier_iv)

    if settings.correlation_refresh_enabled:
        scheduler.schedule_correlation_refresh(_scheduled_correlation_refresh)

    if settings.etf_refresh_enabled:
        scheduler.schedule_etf_refresh(_scheduled_etf_refresh)

    if settings.quarterly_meta_refresh_enabled:
        scheduler.schedule_quarterly_meta(_scheduled_quarterly_meta)

    if settings.weekly_meta_refresh_enabled:
        scheduler.schedule_weekly_meta(_scheduled_weekly_meta)

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
        filings,
        intents,
        monitor,
        news,
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
    app.include_router(news.router, prefix="/api/v1")
    app.include_router(filings.router, prefix="/api/v1")

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
