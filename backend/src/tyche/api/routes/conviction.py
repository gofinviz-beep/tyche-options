"""Conviction engine routes — 8/21 EMA analysis and data store management.

Conviction scans are cached server-side by (latest_ohlcv_date, ticker_set).
Since OHLCV data only changes once per day, this avoids re-computing EMAs
for the entire universe on every page visit.  The cache is invalidated
when new OHLCV data arrives (bootstrap or daily update).
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import get_conviction_engine, get_data_store, get_polygon, get_settings, get_ticker_meta_store
from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore, bootstrap_ohlcv
from tyche.market_data.institutional import get_cached_ownership_batch
from tyche.market_data.polygon import PolygonClient
from tyche.conviction.engine import TrendState as _TS
from tyche.schemas.conviction import (
    BootstrapRequest,
    BootstrapResponse,
    ConvictionScanResponse,
    ConvictionSignalResponse,
    DataStoreStatusResponse,
    GateResultResponse,
    TrendSummary,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/conviction", tags=["conviction"])

_scan_cache: dict[str, ConvictionScanResponse] = {}
_scan_cache_key: str | None = None


def _make_cache_key(store: OHLCVStore, tickers: list[str]) -> str:
    _, latest = store.get_date_range()
    date_str = latest.isoformat() if latest else "none"
    ticker_hash = hash(frozenset(t.upper() for t in tickers))
    return f"{date_str}:{ticker_hash}"


def invalidate_conviction_cache() -> None:
    """Clear both the response cache and the engine's per-ticker cache."""
    global _scan_cache, _scan_cache_key
    _scan_cache.clear()
    _scan_cache_key = None

    from tyche.api.deps import _conviction_engine
    if _conviction_engine is not None:
        _conviction_engine.invalidate_cache()

    logger.info("conviction_cache_invalidated")


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
    engine: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionScanResponse:
    """Run the conviction engine across the full universe.

    Always scans the full screened universe (not limited to watchlist).
    Watchlist tickers are tagged with ``is_watchlist=true`` for display.
    Results include both CSP-eligible stocks AND pullback-state stocks
    (even if not yet eligible) so the user can see opportunities forming.

    Results are cached by (latest OHLCV date, ticker set).
    Use ``force=true`` to bypass cache.
    """
    if not store.exists:
        raise HTTPException(
            status_code=400,
            detail="No OHLCV data. Run POST /conviction/bootstrap first.",
        )

    if symbols:
        tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    else:
        tickers = store.screen_universe(
            min_avg_volume=settings.min_avg_volume,
            min_price=settings.min_stock_price,
        )
        if meta_store.exists:
            tickers = meta_store.filter_equity_only(tickers)
        logger.info("conviction_dynamic_discovery", candidates=len(tickers))

    if not tickers:
        raise HTTPException(status_code=400, detail="No symbols found after screening.")

    watchlist_set = frozenset(
        s.upper() for s in (settings.watchlist_symbols or [])
    )

    cache_key = _make_cache_key(store, tickers)
    if not force and cache_key in _scan_cache:
        logger.info("conviction_scan_cache_hit", tickers=len(tickers))
        return _scan_cache[cache_key]

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

    # --- Trend breakdown ---
    trend_counts: dict[str, int] = {}
    for s in signals:
        trend_counts[s.trend_state.value] = trend_counts.get(s.trend_state.value, 0) + 1

    pullback_states = (_TS.PULLBACK_TO_8EMA, _TS.PULLBACK_TO_21EMA)
    uptrend_states = (_TS.STRONG_UPTREND, _TS.UPTREND)

    eligible = [s for s in signals if s.csp_eligible]
    pullback_all = [
        s for s in signals if s.trend_state in pullback_states
    ]
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
        pullback_not_eligible = [
            s for s in pullback_all if not s.csp_eligible
        ]

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

    _scan_cache[cache_key] = response
    dur_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "conviction_scan_computed_and_cached",
        tickers=len(tickers),
        eligible=len(eligible),
        pullback_eligible=len(pullback_eligible_list),
        pullback_total=len(pullback_all),
        uptrend_eligible=len(uptrend_eligible_list),
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
) -> ConvictionSignalResponse:
    """Get the conviction signal for a single ticker."""
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
