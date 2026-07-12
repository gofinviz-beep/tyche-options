"""API route for per-ticker deep dive analysis.

Read-through cache: in-memory dict -> precomputed ``DeepDiveStore`` (nightly
batch) -> on-demand ``TickerDeepDiveEngine.analyze()`` fallback + write-back.
No publish-JSON step — the route reads the per-ticker Parquet store directly,
which works identically for local and GCS (``TYCHE_DATA_BACKEND=gcs``).
"""

from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

import numpy as np
import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.cloud_mode import cloud_inline_compute_blocked, require_inline_compute_allowed
from tyche.api.deps import (
    get_catalyst_store,
    get_data_store,
    get_deep_dive_store,
    get_estimates_store,
    get_fundamentals_store,
    get_settings,
    get_ticker_meta_store,
)
from tyche.config import TycheSettings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.schemas.deep_dive import TickerDeepDiveResponse, to_response

if TYPE_CHECKING:
    from tyche.market_data.deep_dive_store import DeepDiveStore

logger = structlog.get_logger()
router = APIRouter(prefix="/stocks", tags=["stocks"])

# In-memory cache keyed by (ticker, latest_session_date_iso) -> response.
# Small — bounded by however many distinct tickers get requested per day.
_cache: dict[tuple[str, str], TickerDeepDiveResponse] = {}


def invalidate_deep_dive_cache() -> None:
    """Clear the in-memory deep-dive response cache (config change / reset_all)."""
    _cache.clear()


def _sessions_stale(as_of_date: str, latest_session: date) -> int:
    """Approximate trading sessions between a stored ``as_of_date`` and the latest session."""
    try:
        as_of = date.fromisoformat(as_of_date)
    except (TypeError, ValueError):
        return 10_000
    if as_of >= latest_session:
        return 0
    return int(np.busday_count(as_of.isoformat(), latest_session.isoformat()))


@router.get("/deep-dive/{ticker}", response_model=TickerDeepDiveResponse)
async def get_ticker_deep_dive(
    ticker: str,
    force: bool = Query(default=False, description="Bypass cache/store and recompute"),
    settings: TycheSettings = Depends(get_settings),
    ohlcv_store: OHLCVStore = Depends(get_data_store),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    deep_dive_store: "DeepDiveStore" = Depends(get_deep_dive_store),
) -> TickerDeepDiveResponse:
    """Full deep-dive analysis for a single ticker.

    Resolution order: in-memory cache -> precomputed ``DeepDiveStore`` (if
    fresh, within ``deep_dive_max_staleness_sessions``) -> on-demand compute
    + write-back. In GCS cloud mode with inline compute blocked, a
    precomputed payload is served even if stale; only 404s when nothing has
    ever been computed for the ticker.
    """
    ticker = ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="Ticker is required")

    latest_session = ohlcv_store.get_latest_date() or date.today()
    cache_key = (ticker, latest_session.isoformat())
    inline_blocked = cloud_inline_compute_blocked(settings)

    if force:
        require_inline_compute_allowed(
            settings,
            operation="deep-dive recompute",
            job_hint="stocks-deep-dive-batch",
        )
    else:
        if cache_key in _cache:
            logger.info("deep_dive_cache_hit", ticker=ticker)
            return _cache[cache_key]

        stored = deep_dive_store.read_ticker(ticker)
        if stored is not None:
            payload, as_of_date = stored
            if inline_blocked:
                _cache[cache_key] = payload
                logger.info(
                    "deep_dive_store_hit_cloud_stale_ok", ticker=ticker, as_of=as_of_date
                )
                return payload
            if _sessions_stale(as_of_date, latest_session) <= settings.deep_dive_max_staleness_sessions:
                _cache[cache_key] = payload
                logger.info("deep_dive_store_hit", ticker=ticker, as_of=as_of_date)
                return payload
        elif inline_blocked:
            raise HTTPException(
                status_code=404,
                detail=f"No precomputed deep-dive data available for {ticker}",
            )

    from tyche.analysis.ticker_deep_dive import TickerDeepDiveEngine

    fundamentals_store = get_fundamentals_store(settings)
    estimates_store = get_estimates_store(settings)
    catalyst_store = get_catalyst_store(settings)

    engine = TickerDeepDiveEngine(
        ohlcv_store=ohlcv_store,
        meta_store=meta_store,
        fundamentals_store=fundamentals_store,
        estimates_store=estimates_store,
        catalyst_store=catalyst_store,
    )

    result = engine.analyze(ticker)

    if result.last_close == 0.0:
        raise HTTPException(
            status_code=404,
            detail=f"No OHLCV data available for {ticker}",
        )

    logger.info("deep_dive_computed", ticker=ticker, as_of=result.as_of_date)
    response = to_response(result)
    _cache[cache_key] = response

    try:
        deep_dive_store.write_ticker(ticker, response)
    except Exception:
        logger.error("deep_dive_write_back_failed", ticker=ticker, exc_info=True)

    return response
