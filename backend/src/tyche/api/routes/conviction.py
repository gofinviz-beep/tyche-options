"""Conviction engine routes — 8/21 EMA analysis and data store management.

Primary read path: pre-computed snapshots from conviction.db (written by the
scheduled conviction batch at 4:08 PM).  Live compute is the fallback when
no snapshots exist (first startup, manual bootstrap).

The ``/version`` endpoint returns the cache version for frontend staleness
checks.
"""

from __future__ import annotations

import json
import time
import uuid
from datetime import date, datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import get_conviction_engine, get_data_store, get_polygon, get_settings, get_ticker_meta_store
from tyche.api.cloud_mode import require_inline_compute_allowed, use_artifact_read_path
from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore, bootstrap_ohlcv
from tyche.market_data.institutional import get_cached_ownership_batch
from tyche.market_data.polygon import PolygonClient
from tyche.conviction.engine import TrendState as _TS
from tyche.models.conviction import ConvictionSnapshot
from tyche.persistence.conviction_repository import (
    get_conviction_version,
    get_latest_snapshot_date,
    get_snapshots_for_date,
)
from tyche.schemas.conviction import (
    BootstrapRequest,
    BootstrapResponse,
    ConvictionScanResponse,
    ConvictionSignalResponse,
    ConvictionVersionResponse,
    DataStoreStatusResponse,
    GateResultResponse,
    TrendSummary,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/conviction", tags=["conviction"])

_scan_cache: dict[str, ConvictionScanResponse] = {}
_deep_dip_cache: dict[str, Any] = {}


def invalidate_conviction_cache(clear_engine: bool = True) -> None:
    """Clear all conviction-related response caches across route modules.

    Args:
        clear_engine: If True, also clear the engine's in-memory EMA cache
            and on-disk Parquet signal store.  Set to False when the engine
            cache is known-good (e.g. right after a batch run) and we only
            want to bust the route-level response caches.
    """
    _scan_cache.clear()

    if clear_engine:
        from tyche.api.deps import _conviction_engine
        if _conviction_engine is not None:
            _conviction_engine.invalidate_cache()

    from tyche.api.routes.stocks import invalidate_deep_dip_cache
    invalidate_deep_dip_cache()

    logger.info("conviction_cache_invalidated", clear_engine=clear_engine)


@router.get("/version", response_model=ConvictionVersionResponse)
async def get_version() -> ConvictionVersionResponse:
    """Return the cache version — last_computed_at and as_of_date.

    Frontend polls this to decide whether its cached data is stale.
    Extremely cheap: single SQL query against conviction.db.
    """
    version = await get_conviction_version()
    return ConvictionVersionResponse(**version)


@router.get("/status", response_model=DataStoreStatusResponse)
async def get_data_store_status(
    store: OHLCVStore = Depends(get_data_store),
) -> DataStoreStatusResponse:
    """Get the status of the local OHLCV data store."""
    earliest, latest = store.get_date_range()
    return DataStoreStatusResponse(
        exists=store.exists,
        total_rows=store.get_row_count(),
        ticker_count=store.get_ticker_count(),
        earliest_date=earliest.isoformat() if earliest else None,
        latest_date=latest.isoformat() if latest else None,
        store_path=str(store.store_dir),
    )


@router.post("/bootstrap", response_model=BootstrapResponse)
async def bootstrap_data(
    req: BootstrapRequest,
    polygon: PolygonClient | None = Depends(get_polygon),
    store: OHLCVStore = Depends(get_data_store),
) -> BootstrapResponse:
    """Bootstrap the OHLCV data store with historical grouped daily bars."""
    if polygon is None:
        raise HTTPException(
            status_code=400,
            detail="Polygon API key not configured. Set TYCHE_POLYGON_API_KEY.",
        )

    try:
        stats = await bootstrap_ohlcv(polygon, store, days=req.days)
        invalidate_conviction_cache()
        return BootstrapResponse(**stats)
    except Exception as exc:
        logger.error("bootstrap_failed", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Bootstrap failed: {exc}")


@router.post("/update")
async def update_daily_data(
    polygon: PolygonClient | None = Depends(get_polygon),
    store: OHLCVStore = Depends(get_data_store),
) -> dict[str, Any]:
    """Fetch today's grouped daily bars and append to the store."""
    if polygon is None:
        raise HTTPException(
            status_code=400,
            detail="Polygon API key not configured.",
        )

    try:
        stats = await bootstrap_ohlcv(polygon, store, days=5)
        invalidate_conviction_cache()
        return {"status": "ok", **stats}
    except Exception as exc:
        logger.error("daily_update_failed", exc_info=True)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/scan", response_model=ConvictionScanResponse)
async def scan_conviction(
    symbols: str | None = Query(default=None, description="Comma-separated symbols"),
    limit_per_path: int = Query(default=100, ge=1, le=500, description="Max results per path (Path A, Path B, forming)"),
    force: bool = Query(default=False, description="Bypass cache and recompute"),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionScanResponse:
    """Return conviction signals for the full universe.

    Primary path: read pre-computed snapshots from conviction.db
    (written by the scheduled batch at 4:08 PM).  This is instant.

    Fallback: if no snapshots exist for today (or the latest trading day),
    run live ``analyze_batch`` — identical to the old behavior.

    Heavy dependencies (ConvictionEngine, OHLCVStore.screen_universe) are
    resolved lazily — only when the DB path misses and live compute is needed.
    This keeps cold-start page loads under 200ms.

    Use ``force=true`` to bypass the DB path and force live compute.
    Passing ``symbols=`` always computes live (per-ticker queries
    don't benefit from the batch cache).
    """
    if force:
        require_inline_compute_allowed(
            settings,
            operation="live conviction scan",
            job_hint="tyche-stocks-conviction-batch",
        )

    specific_symbols = bool(symbols)
    watchlist_set = frozenset(
        s.upper() for s in (settings.watchlist_symbols or [])
    )
    specific_tickers = (
        frozenset(s.strip().upper() for s in symbols.split(",") if s.strip())
        if symbols
        else None
    )

    if use_artifact_read_path(settings) and not force:
        from tyche.persistence.published_routes import get_options_conviction_scan

        loaded = get_options_conviction_scan(
            settings=settings,
            limit_per_path=limit_per_path,
            watchlist_set=watchlist_set,
            specific_tickers=specific_tickers,
        )
        if loaded is not None:
            scan, _layer = loaded
            if not specific_symbols:
                _scan_cache["artifact"] = scan
            return scan
        if not settings.api_allow_local_db_fallback:
            return ConvictionScanResponse(
                scan_id=str(uuid.uuid4()),
                scanned_at=datetime.now(timezone.utc).isoformat(),
                total_screened=0,
                eligible_count=0,
                signals=[],
            )

    # --- Fast DB path for full-universe scans (no heavy deps needed) ---
    if not force and not specific_symbols:
        if _scan_cache:
            cache_key = next(iter(_scan_cache))
            logger.info("conviction_scan_cache_hit")
            return _scan_cache[cache_key]

        response = await _build_scan_from_db(
            tickers=None,
            limit_per_path=limit_per_path,
            watchlist_set=watchlist_set,
            meta_store=meta_store,
        )
        if response is not None:
            _scan_cache["db"] = response
            return response

        if use_artifact_read_path(settings) and not settings.api_allow_local_db_fallback:
            return ConvictionScanResponse(
                scan_id=str(uuid.uuid4()),
                scanned_at=datetime.now(timezone.utc).isoformat(),
                total_screened=0,
                eligible_count=0,
                signals=[],
            )

        logger.info("conviction_scan_db_miss_falling_back_to_live")
        require_inline_compute_allowed(
            settings,
            operation="live conviction scan",
            job_hint="tyche-stocks-conviction-batch",
        )

    # --- Lazily resolve heavy deps only for live compute path ---
    store = get_data_store(settings)
    engine = get_conviction_engine(settings)

    if not store.exists:
        raise HTTPException(
            status_code=400,
            detail="No OHLCV data. Run POST /conviction/bootstrap first.",
        )

    # --- Resolve ticker list (expensive screen_universe) only for live path ---
    if symbols:
        tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        tickers = store.screen_universe(
            min_avg_volume=settings.min_avg_volume,
            min_price=settings.min_stock_price,
        )
        if meta_store.exists:
            tickers = meta_store.filter_equity_only(tickers)

    if not tickers:
        raise HTTPException(status_code=400, detail="No symbols found after screening.")

    # --- Live compute fallback ---
    return await _live_compute_scan(
        tickers=tickers,
        symbols=symbols,
        limit_per_path=limit_per_path,
        watchlist_set=watchlist_set,
        engine=engine,
        store=store,
        meta_store=meta_store,
    )


async def _build_scan_from_db(
    *,
    tickers: list[str] | None,
    limit_per_path: int,
    watchlist_set: frozenset[str],
    meta_store: TickerMetaStore,
) -> ConvictionScanResponse | None:
    """Build a ConvictionScanResponse from conviction.db snapshots.

    Returns None if no snapshots are available (triggers live fallback).
    When tickers is None, all snapshots for the date are used.
    """
    t0 = time.perf_counter()

    target = date.today()
    snaps = await get_snapshots_for_date(target)
    if not snaps:
        latest = await get_latest_snapshot_date()
        if latest and latest < target:
            snaps = await get_snapshots_for_date(latest)
    if not snaps:
        return None

    if tickers is not None:
        ticker_set_upper = frozenset(t.upper() for t in tickers)
        snaps = [s for s in snaps if s.ticker in ticker_set_upper]
        if not snaps:
            return None

    pullback_states_str = {"pullback_to_8ema", "pullback_to_21ema"}
    uptrend_states_str = {"strong_uptrend", "uptrend"}

    trend_counts: dict[str, int] = {}
    eligible: list[ConvictionSnapshot] = []
    pullback_all: list[ConvictionSnapshot] = []
    pullback_eligible: list[ConvictionSnapshot] = []
    uptrend_eligible: list[ConvictionSnapshot] = []

    for s in snaps:
        trend_counts[s.trend_state] = trend_counts.get(s.trend_state, 0) + 1
        if s.csp_eligible:
            eligible.append(s)
            if s.trend_state in pullback_states_str:
                pullback_eligible.append(s)
            elif s.trend_state in uptrend_states_str:
                uptrend_eligible.append(s)
        if s.trend_state in pullback_states_str:
            pullback_all.append(s)

    conviction_order = {"high": 0, "medium": 1, "low": 2, "none": 3}

    def _snap_sort_key(s: ConvictionSnapshot) -> tuple:
        return (
            conviction_order.get(s.conviction_level, 99),
            -(s.prior_streak or 0),
            -(s.days_above_both_emas or 0),
        )

    pullback_not_eligible = [s for s in pullback_all if not s.csp_eligible]
    pb_sorted = sorted(pullback_eligible, key=_snap_sort_key)[:limit_per_path]
    up_sorted = sorted(uptrend_eligible, key=_snap_sort_key)[:limit_per_path]
    forming_sorted = sorted(pullback_not_eligible, key=_snap_sort_key)[:limit_per_path]
    display_snaps = pb_sorted + up_sorted + forming_sorted

    display_tickers = [s.ticker for s in display_snaps]
    market_caps = meta_store.get_market_caps(display_tickers) if meta_store.exists else {}
    inst_persisted = meta_store.get_institutional_pcts(display_tickers) if meta_store.exists else {}
    inst_cached = get_cached_ownership_batch(display_tickers)
    inst_ownership = {**inst_persisted, **inst_cached}
    sectors = meta_store.get_sectors(display_tickers) if meta_store.exists else {}

    signals_resp = [
        _snapshot_to_signal_response(
            s,
            is_watchlist=s.ticker in watchlist_set,
            market_cap=market_caps.get(s.ticker),
            institutional_pct=inst_ownership.get(s.ticker),
            sector=sectors.get(s.ticker),
        )
        for s in display_snaps
    ]

    response = ConvictionScanResponse(
        scan_id=str(uuid.uuid4()),
        scanned_at=datetime.now(timezone.utc).isoformat(),
        total_screened=len(snaps),
        eligible_count=len(eligible),
        uptrend_eligible=len(uptrend_eligible),
        pullback_eligible=len(pullback_eligible),
        pullback_count=len(pullback_all),
        trend_summary=TrendSummary(**{
            k: trend_counts.get(k, 0) for k in TrendSummary.model_fields
        }),
        signals=signals_resp,
    )

    dur_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "conviction_scan_served_from_db",
        snapshots=len(snaps),
        eligible=len(eligible),
        display=len(display_snaps),
        duration_ms=round(dur_ms, 2),
    )
    return response


async def _live_compute_scan(
    *,
    tickers: list[str],
    symbols: str | None,
    limit_per_path: int,
    watchlist_set: frozenset[str],
    engine: ConvictionEngine,
    store: OHLCVStore,
    meta_store: TickerMetaStore,
) -> ConvictionScanResponse:
    """Compute conviction live via analyze_batch (original behavior)."""
    t0 = time.perf_counter()
    ticker_data = store.read_tickers(tickers)

    signals = engine.analyze_batch(
        ticker_data,
        requested_tickers=tickers if symbols else None,
    )

    if not signals:
        raise HTTPException(
            status_code=404,
            detail="No data found for the requested symbols.",
        )

    trend_counts: dict[str, int] = {}
    for s in signals:
        trend_counts[s.trend_state.value] = trend_counts.get(s.trend_state.value, 0) + 1

    pullback_states = (_TS.PULLBACK_TO_8EMA, _TS.PULLBACK_TO_21EMA)
    uptrend_states = (_TS.STRONG_UPTREND, _TS.UPTREND)

    eligible = [s for s in signals if s.csp_eligible]
    pullback_all = [s for s in signals if s.trend_state in pullback_states]
    pullback_eligible_list = [s for s in eligible if s.trend_state in pullback_states]
    uptrend_eligible_list = [s for s in eligible if s.trend_state in uptrend_states]

    conviction_order = {"high": 0, "medium": 1, "low": 2, "none": 3}

    def _sort_key(s: ConvictionSignal) -> tuple:
        return (
            conviction_order.get(s.conviction_level, 99),
            -s.prior_streak,
            -s.days_above_both_emas,
        )

    if symbols:
        display_signals = signals
    else:
        pullback_not_eligible = [s for s in pullback_all if not s.csp_eligible]
        pb_eligible_sorted = sorted(pullback_eligible_list, key=_sort_key)[:limit_per_path]
        up_eligible_sorted = sorted(uptrend_eligible_list, key=_sort_key)[:limit_per_path]
        pb_forming_sorted = sorted(pullback_not_eligible, key=_sort_key)[:limit_per_path]
        display_signals = pb_eligible_sorted + up_eligible_sorted + pb_forming_sorted

    display_tickers = [s.ticker for s in display_signals]
    market_caps = meta_store.get_market_caps(display_tickers) if meta_store.exists else {}
    inst_persisted = meta_store.get_institutional_pcts(display_tickers) if meta_store.exists else {}
    inst_cached = get_cached_ownership_batch(display_tickers)
    inst_ownership = {**inst_persisted, **inst_cached}
    sectors = meta_store.get_sectors(display_tickers) if meta_store.exists else {}

    response = ConvictionScanResponse(
        scan_id=str(uuid.uuid4()),
        scanned_at=datetime.now(timezone.utc).isoformat(),
        total_screened=len(tickers),
        eligible_count=len(eligible),
        uptrend_eligible=len(uptrend_eligible_list),
        pullback_eligible=len(pullback_eligible_list),
        pullback_count=len(pullback_all),
        trend_summary=TrendSummary(**{
            k: trend_counts.get(k, 0) for k in TrendSummary.model_fields
        }),
        signals=[
            _signal_to_response(
                s,
                is_watchlist=s.ticker in watchlist_set,
                market_cap=market_caps.get(s.ticker),
                institutional_pct=inst_ownership.get(s.ticker),
                sector=sectors.get(s.ticker),
            )
            for s in display_signals
        ],
    )

    _scan_cache["live"] = response
    dur_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "conviction_scan_live_computed",
        tickers=len(tickers),
        eligible=len(eligible),
        engine_cache_size=engine.cache_size,
        duration_ms=round(dur_ms, 2),
    )
    return response


@router.get("/signal/{ticker}", response_model=ConvictionSignalResponse)
async def get_ticker_conviction(
    ticker: str,
    engine: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionSignalResponse:
    """Get the conviction signal for a single ticker."""
    if use_artifact_read_path(settings):
        from tyche.persistence.published_routes import get_options_conviction_scan

        loaded = get_options_conviction_scan(
            settings=settings,
            specific_tickers=frozenset({ticker.upper()}),
            limit_per_path=1,
        )
        if loaded is not None:
            scan, _layer = loaded
            if scan.signals:
                return scan.signals[0]
        if not settings.api_allow_local_db_fallback:
            raise HTTPException(
                status_code=404,
                detail=f"No published conviction signal for {ticker.upper()}",
            )

    if not store.exists:
        raise HTTPException(
            status_code=400,
            detail="No OHLCV data. Run POST /conviction/bootstrap first.",
        )

    t = ticker.upper()
    df = store.read_ticker(t)
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for {t}",
        )

    signal = engine.analyze(t, df)
    cap = meta_store.get_market_caps([t]).get(t) if meta_store.exists else None
    inst_pct = (meta_store.get_institutional_pcts([t]) if meta_store.exists else {})
    inst_cached = get_cached_ownership_batch([t])
    inst = {**inst_pct, **inst_cached}.get(t)
    sec = meta_store.get_sectors([t]).get(t) if meta_store.exists else None

    return _signal_to_response(signal, market_cap=cap, institutional_pct=inst, sector=sec)


def _signal_to_response(
    s: ConvictionSignal,
    *,
    is_watchlist: bool = False,
    market_cap: float | None = None,
    institutional_pct: float | None = None,
    sector: str | None = None,
) -> ConvictionSignalResponse:
    return ConvictionSignalResponse(
        ticker=s.ticker,
        trend_state=s.trend_state.value,
        conviction_level=s.conviction_level,
        csp_eligible=s.csp_eligible,
        is_watchlist=is_watchlist,
        last_close=round(s.last_close, 2),
        ema_8=round(s.ema_8, 4),
        ema_21=round(s.ema_21, 4),
        ema_8_slope=round(s.ema_8_slope, 6),
        ema_21_slope=round(s.ema_21_slope, 6),
        price_to_8ema_pct=round(s.price_to_8ema_pct, 2),
        price_to_21ema_pct=round(s.price_to_21ema_pct, 2),
        volume_declining_on_pullback=s.volume_declining_on_pullback,
        avg_volume_20d=s.avg_volume_20d,
        latest_volume=s.latest_volume,
        days_above_both_emas=s.days_above_both_emas,
        prior_streak=s.prior_streak,
        as_of_date=s.as_of_date.isoformat() if s.as_of_date else None,
        ema_50=round(s.ema_50, 4),
        ema_50_slope=round(s.ema_50_slope, 6),
        rsi_14=round(s.rsi_14, 2),
        iv_rank=round(s.iv_rank, 1) if s.iv_rank is not None else None,
        iv_percentile=round(s.iv_percentile, 1) if s.iv_percentile is not None else None,
        atm_iv=round(s.atm_iv, 4) if s.atm_iv is not None else None,
        vrp=round(s.vrp, 4) if s.vrp is not None else None,
        conviction_score=round(s.conviction_score, 3),
        csp_safety_prob=round(s.csp_safety_prob, 4) if s.csp_safety_prob is not None else None,
        market_cap=market_cap if market_cap and market_cap > 0 else None,
        institutional_pct=round(institutional_pct, 4) if institutional_pct is not None else None,
        sector=sector,
        gate_results=[
            GateResultResponse(
                gate=g.gate,
                passed=g.passed,
                actual=g.actual,
                threshold=g.threshold,
                reason=g.reason,
            )
            for g in (s.gate_results or [])
        ],
    )


def _snapshot_to_signal_response(
    snap: ConvictionSnapshot,
    *,
    is_watchlist: bool = False,
    market_cap: float | None = None,
    institutional_pct: float | None = None,
    sector: str | None = None,
) -> ConvictionSignalResponse:
    """Convert a persisted ConvictionSnapshot to a ConvictionSignalResponse."""
    gate_results: list[GateResultResponse] = []
    if snap.gate_results_json:
        try:
            raw_gates = json.loads(snap.gate_results_json)
            gate_results = [
                GateResultResponse(
                    gate=g.get("gate", ""),
                    passed=g.get("passed", False),
                    actual=g.get("actual", ""),
                    threshold=g.get("threshold", ""),
                    reason=g.get("reason", ""),
                )
                for g in raw_gates
            ]
        except (json.JSONDecodeError, TypeError):
            pass

    return ConvictionSignalResponse(
        ticker=snap.ticker,
        trend_state=snap.trend_state,
        conviction_level=snap.conviction_level,
        csp_eligible=snap.csp_eligible,
        is_watchlist=is_watchlist,
        last_close=round(snap.last_close, 2),
        ema_8=round(snap.ema_8, 4),
        ema_21=round(snap.ema_21, 4),
        ema_8_slope=round(snap.ema_8_slope, 6),
        ema_21_slope=round(snap.ema_21_slope, 6),
        price_to_8ema_pct=round(snap.price_to_8ema_pct, 2),
        price_to_21ema_pct=round(snap.price_to_21ema_pct, 2),
        volume_declining_on_pullback=snap.volume_declining,
        avg_volume_20d=snap.avg_volume_20d,
        latest_volume=snap.latest_volume,
        days_above_both_emas=snap.days_above_both_emas,
        prior_streak=snap.prior_streak or 0,
        as_of_date=snap.as_of_date.isoformat() if snap.as_of_date else None,
        ema_50=round(snap.ema_50 or 0.0, 4),
        ema_50_slope=round(snap.ema_50_slope or 0.0, 6),
        rsi_14=round(snap.rsi_14 or 0.0, 2),
        iv_rank=round(snap.iv_rank, 1) if snap.iv_rank is not None else None,
        iv_percentile=round(snap.iv_percentile, 1) if snap.iv_percentile is not None else None,
        atm_iv=round(snap.atm_iv, 4) if snap.atm_iv is not None else None,
        vrp=round(snap.vrp, 4) if snap.vrp is not None else None,
        conviction_score=round(snap.conviction_score or 0.0, 3),
        csp_safety_prob=round(snap.csp_safety_prob, 4) if snap.csp_safety_prob is not None else None,
        market_cap=market_cap if market_cap and market_cap > 0 else None,
        institutional_pct=round(institutional_pct, 4) if institutional_pct is not None else None,
        sector=sector,
        gate_results=gate_results,
    )
