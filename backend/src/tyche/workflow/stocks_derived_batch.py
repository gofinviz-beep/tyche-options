"""Stocks derived signals batch — deep dips + history summaries for cloud serving."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, timedelta

import structlog

from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.stocks_deep_dips_store import (
    STOCKS_DEEP_DIPS_REL,
    write_deep_dips_parquet,
)
from tyche.market_data.stocks_history_store import (
    STOCKS_TRANSITIONS_REL,
    write_history_summary_parquet,
    write_transitions_parquet,
)
from tyche.persistence.conviction_repository import get_transitions
from tyche.schemas.stocks import ConvictionTransitionResponse
from tyche.storage.paths import StorageContext
from tyche.workflow.deep_dip_scan import run_deep_dip_scan
from tyche.workflow.history_summary import (
    STOCKS_HISTORY_SUMMARY_REL,
    build_history_summary_rows,
    select_history_universe,
)

logger = structlog.get_logger()


@dataclass
class StocksDerivedBatchResult:
    as_of_date: date
    deep_dip_alerts: int = 0
    history_rows: int = 0
    transition_rows: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "deep_dip_alerts": self.deep_dip_alerts,
            "history_rows": self.history_rows,
            "transition_rows": self.transition_rows,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "output_paths": [
                STOCKS_DEEP_DIPS_REL,
                STOCKS_HISTORY_SUMMARY_REL,
                STOCKS_TRANSITIONS_REL,
            ],
        }


async def _export_transitions(
    *,
    settings: TycheSettings,
    ctx: StorageContext,
    as_of: date,
) -> int:
    """Export recent transitions when SQLite is available (local dev)."""
    if not settings.api_allow_local_db_fallback:
        return 0
    try:
        from_date = as_of - timedelta(days=7)
        transitions = await get_transitions(from_date=from_date, to_date=as_of)
    except Exception:
        logger.warning("derived_batch_transitions_unavailable", exc_info=True)
        return 0

    if not transitions:
        return 0

    rows = [
        ConvictionTransitionResponse(**t.to_dict()).model_dump(mode="json")
        for t in transitions
    ]
    return write_transitions_parquet(rows, ctx=ctx)


async def run_stocks_derived_batch(
    *,
    settings: TycheSettings,
    data_store: OHLCVStore,
    ticker_meta_store: TickerMetaStore,
    conviction_engine: ConvictionEngine | None = None,
    ctx: StorageContext,
    run_id: str | None = None,
    as_of_date: date | None = None,
) -> StocksDerivedBatchResult:
    """Compute deep dip scan + history summaries and export signal Parquet."""
    t0 = time.perf_counter()
    as_of = as_of_date or date.today()
    result = StocksDerivedBatchResult(as_of_date=as_of)

    if not data_store.exists:
        result.errors.append("OHLCV data store does not exist")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    min_cap = settings.min_market_cap_millions * 1_000_000
    universe = select_history_universe(
        data_store,
        ticker_meta_store,
        min_market_cap=min_cap,
    )

    try:
        scan = await run_deep_dip_scan(
            settings=settings,
            data_store=data_store,
            ticker_meta_store=ticker_meta_store,
            conviction_engine=conviction_engine,
            ctx=ctx,
            as_of_date=as_of,
        )
        result.deep_dip_alerts = write_deep_dips_parquet(scan, ctx=ctx)
    except Exception:
        logger.error("derived_batch_deep_dips_failed", exc_info=True)
        result.errors.append("Deep dip export failed")

    try:
        history_rows = build_history_summary_rows(
            data_store=data_store,
            ticker_meta_store=ticker_meta_store,
            tickers=universe,
            ctx=ctx,
            as_of=as_of,
            run_id=run_id,
        )
        result.history_rows = write_history_summary_parquet(history_rows, ctx=ctx)
    except Exception:
        logger.error("derived_batch_history_failed", exc_info=True)
        result.errors.append("History summary export failed")

    try:
        result.transition_rows = await _export_transitions(
            settings=settings,
            ctx=ctx,
            as_of=as_of,
        )
    except Exception:
        logger.error("derived_batch_transitions_failed", exc_info=True)
        result.errors.append("Transition export failed")

    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("stocks_derived_batch_complete", **result.to_dict())
    return result
