"""Stock Deep Dive nightly precompute batch — mirrors ``stocks_derived_batch``.

Computes the full per-ticker deep-dive payload (multi-timeframe RSI, EMA
stack, MACD, Bollinger Bands, fundamentals, estimates, catalysts) for the
equity universe ≥ a market-cap floor and persists one Parquet file per
ticker via :class:`DeepDiveStore`. The route then serves precomputed
payloads via a read-through cache instead of recomputing on every request.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date

import structlog

from tyche.analysis.ticker_deep_dive import TickerDeepDiveEngine
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.deep_dive_store import DEEP_DIVE_REL, DeepDiveStore
from tyche.ops.job_progress import log_job_phase, log_job_progress
from tyche.schemas.deep_dive import TickerDeepDiveResponse, to_response
from tyche.storage.paths import StorageContext
from tyche.workflow.history_summary import select_history_universe

logger = structlog.get_logger()

_PROGRESS_EVERY = 250
_JOB_NAME = "stocks-deep-dive-batch"


@dataclass
class DeepDiveBatchResult:
    """Summary of a Stock Deep Dive precompute batch run."""

    as_of_date: date
    universe_size: int = 0
    tickers_computed: int = 0
    tickers_skipped: int = 0
    tickers_written: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "universe_size": self.universe_size,
            "tickers_computed": self.tickers_computed,
            "tickers_skipped": self.tickers_skipped,
            "tickers_written": self.tickers_written,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "output_paths": [f"{DEEP_DIVE_REL}/"],
        }


def _analyze_one(
    engine: TickerDeepDiveEngine, ticker: str
) -> TickerDeepDiveResponse | None:
    """Compute + serialize a single ticker; returns ``None`` when it should be skipped."""
    result = engine.analyze(ticker)
    if result.last_close == 0.0:
        return None
    return to_response(result)


async def run_deep_dive_batch(
    *,
    ohlcv_store: OHLCVStore,
    meta_store: TickerMetaStore,
    fundamentals_store=None,
    estimates_store=None,
    catalyst_store=None,
    min_market_cap_millions: float,
    ctx: StorageContext | None = None,
    concurrency: int = 8,
) -> DeepDiveBatchResult:
    """Precompute deep-dive payloads for the filtered universe and persist them."""
    t0 = time.perf_counter()
    as_of = date.today()
    result = DeepDiveBatchResult(as_of_date=as_of)

    if not ohlcv_store.exists:
        result.errors.append("OHLCV data store does not exist")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    min_cap = min_market_cap_millions * 1_000_000
    universe = select_history_universe(
        ohlcv_store,
        meta_store,
        min_market_cap=min_cap,
    )
    result.universe_size = len(universe)

    if not universe:
        result.errors.append("No tickers in the filtered universe")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    log_job_phase(_JOB_NAME, "analyze", universe=len(universe))

    engine = TickerDeepDiveEngine(
        ohlcv_store=ohlcv_store,
        meta_store=meta_store,
        fundamentals_store=fundamentals_store,
        estimates_store=estimates_store,
        catalyst_store=catalyst_store,
    )

    semaphore = asyncio.Semaphore(max(1, concurrency))
    payloads: dict[str, TickerDeepDiveResponse] = {}
    done = 0
    start_time = time.monotonic()
    lock = asyncio.Lock()

    async def _worker(ticker: str) -> None:
        nonlocal done
        async with semaphore:
            try:
                payload = await asyncio.to_thread(_analyze_one, engine, ticker)
            except Exception:
                logger.error("deep_dive_batch_ticker_failed", ticker=ticker, exc_info=True)
                payload = None
        async with lock:
            done += 1
            if payload is None:
                result.tickers_skipped += 1
            else:
                payloads[ticker] = payload
                result.tickers_computed += 1
            if done % _PROGRESS_EVERY == 0:
                log_job_progress(
                    _JOB_NAME,
                    "analyze",
                    done=done,
                    total=len(universe),
                    start_time=start_time,
                )

    await asyncio.gather(*(_worker(t) for t in universe))

    log_job_phase(
        _JOB_NAME,
        "analyze",
        status="complete",
        computed=result.tickers_computed,
        skipped=result.tickers_skipped,
    )

    if not ctx:
        result.errors.append("No StorageContext provided; skipping write")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    store = DeepDiveStore(ctx=ctx)
    log_job_phase(_JOB_NAME, "write", tickers=len(payloads))
    result.tickers_written = store.write_batch(payloads)
    log_job_phase(_JOB_NAME, "write", status="complete", written=result.tickers_written)

    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("deep_dive_batch_complete", **result.to_dict())
    return result
