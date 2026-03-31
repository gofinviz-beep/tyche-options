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

from tyche.api.deps import get_conviction_engine, get_data_store, get_polygon, get_settings
from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, bootstrap_ohlcv
from tyche.market_data.polygon import PolygonClient
from tyche.schemas.conviction import (
    BootstrapRequest,
    BootstrapResponse,
    ConvictionScanResponse,
    ConvictionSignalResponse,
    DataStoreStatusResponse,
    GateResultResponse,
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
    """Clear the conviction scan cache (called after OHLCV data updates)."""
    global _scan_cache, _scan_cache_key
    _scan_cache.clear()
    _scan_cache_key = None
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
    limit: int = Query(default=50, ge=1, le=500, description="Max results for dynamic discovery"),
    force: bool = Query(default=False, description="Bypass cache and recompute"),
    engine: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
    settings: TycheSettings = Depends(get_settings),
) -> ConvictionScanResponse:
    """Run the conviction engine on specified symbols, watchlist, or dynamic discovery.

    Results are cached by (latest OHLCV date, ticker set).  Since OHLCV
    data only changes once per day, subsequent calls with the same tickers
    return the cached result instantly.  Use ``force=true`` to bypass.
    """
    if not store.exists:
        raise HTTPException(
            status_code=400,
            detail="No OHLCV data. Run POST /conviction/bootstrap first.",
        )

    if symbols:
        tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    elif settings.watchlist_symbols:
        tickers = settings.watchlist_symbols
    else:
        tickers = store.screen_universe(
            min_avg_volume=settings.min_avg_volume,
            min_price=settings.min_stock_price,
        )
        logger.info("conviction_dynamic_discovery", candidates=len(tickers))

    if not tickers:
        raise HTTPException(status_code=400, detail="No symbols found after screening.")

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

    eligible = [s for s in signals if s.csp_eligible]
    eligible.sort(
        key=lambda s: (
            {"high": 0, "medium": 1, "low": 2}.get(s.conviction_level, 3),
            -s.days_above_both_emas,
        )
    )
    display_signals = eligible[:limit] if not symbols else signals

    response = ConvictionScanResponse(
        scan_id=str(uuid.uuid4()),
        scanned_at=datetime.now(timezone.utc).isoformat(),
        total_screened=len(tickers),
        eligible_count=len(eligible),
        signals=[_signal_to_response(s) for s in display_signals],
    )

    _scan_cache[cache_key] = response
    dur_ms = (time.perf_counter() - t0) * 1000
    logger.info(
        "conviction_scan_computed_and_cached",
        tickers=len(tickers),
        eligible=len(eligible),
        duration_ms=round(dur_ms, 2),
    )
    return response


@router.get("/signal/{ticker}", response_model=ConvictionSignalResponse)
async def get_ticker_conviction(
    ticker: str,
    engine: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
) -> ConvictionSignalResponse:
    """Get the conviction signal for a single ticker."""
    if not store.exists:
        raise HTTPException(
            status_code=400,
            detail="No OHLCV data. Run POST /conviction/bootstrap first.",
        )

    df = store.read_ticker(ticker.upper())
    if df.empty:
        raise HTTPException(
            status_code=404,
            detail=f"No data found for {ticker.upper()}",
        )

    signal = engine.analyze(ticker.upper(), df)
    return _signal_to_response(signal)


def _signal_to_response(s: ConvictionSignal) -> ConvictionSignalResponse:
    return ConvictionSignalResponse(
        ticker=s.ticker,
        trend_state=s.trend_state.value,
        conviction_level=s.conviction_level,
        csp_eligible=s.csp_eligible,
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
        as_of_date=s.as_of_date.isoformat() if s.as_of_date else None,
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
