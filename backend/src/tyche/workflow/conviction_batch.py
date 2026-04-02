"""Conviction batch job — compute EMA conviction for the filtered universe.

Pre-filters tickers by market cap / price / volume, runs the conviction engine,
persists snapshots to conviction.db, and detects state transitions.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

import structlog

from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.models.conviction import ConvictionTransition
from tyche.persistence.conviction_repository import (
    cleanup_old_snapshots,
    detect_and_record_transitions,
    upsert_snapshots,
)

logger = structlog.get_logger()


@dataclass
class ConvictionBatchResult:
    """Summary of a conviction batch run."""

    as_of_date: date
    total_tickers_in_store: int = 0
    tickers_after_market_cap_filter: int = 0
    tickers_after_price_volume_filter: int = 0
    signals_computed: int = 0
    snapshots_upserted: int = 0
    transitions_detected: int = 0
    new_pullback_transitions: int = 0
    transitions: list[ConvictionTransition] = field(default_factory=list)
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "total_tickers_in_store": self.total_tickers_in_store,
            "tickers_after_market_cap_filter": self.tickers_after_market_cap_filter,
            "tickers_after_price_volume_filter": self.tickers_after_price_volume_filter,
            "signals_computed": self.signals_computed,
            "snapshots_upserted": self.snapshots_upserted,
            "transitions_detected": self.transitions_detected,
            "new_pullback_transitions": self.new_pullback_transitions,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
        }


async def run_conviction_batch(
    data_store: OHLCVStore,
    conviction_engine: ConvictionEngine,
    ticker_meta_store: TickerMetaStore | None = None,
    min_market_cap: float = 500_000_000.0,
    min_price: float = 5.0,
    min_avg_volume: int = 500_000,
    retention_days: int = 90,
) -> ConvictionBatchResult:
    """Run conviction analysis across the filtered universe and persist results.

    Steps:
        1. List all tickers in the OHLCV store
        2. Pre-filter by market cap (using TickerMetaStore)
        3. Load OHLCV data and filter by price / volume
        4. Run ConvictionEngine.analyze_batch()
        5. Upsert snapshots to conviction.db
        6. Detect state transitions vs. previous day
        7. Cleanup old data beyond retention period

    Args:
        data_store: OHLCV data store (Parquet).
        conviction_engine: Configured ConvictionEngine instance.
        ticker_meta_store: Optional TickerMetaStore for market cap filtering.
        min_market_cap: Minimum market cap in dollars (tickers without data pass).
        min_price: Minimum last close price.
        min_avg_volume: Minimum 20-day average volume.
        retention_days: Days of snapshot history to retain.

    Returns:
        ConvictionBatchResult with counts and detected transitions.
    """
    t0 = time.perf_counter()
    result = ConvictionBatchResult(as_of_date=date.today())

    if not data_store.exists:
        result.errors.append("OHLCV data store does not exist")
        return result

    # 1. All tickers in store
    all_tickers = data_store.get_all_tickers()
    result.total_tickers_in_store = len(all_tickers)

    if not all_tickers:
        result.errors.append("No tickers in OHLCV store")
        return result

    logger.info("conviction_batch_start", total_tickers=len(all_tickers))

    # 2a. Filter to common stocks only (exclude ETFs, ETNs, warrants, etc.)
    if ticker_meta_store and ticker_meta_store.exists:
        equity_tickers = ticker_meta_store.filter_equity_only(all_tickers)
        logger.info(
            "conviction_batch_equity_filtered",
            before=len(all_tickers),
            after=len(equity_tickers),
            removed=len(all_tickers) - len(equity_tickers),
        )
    else:
        equity_tickers = all_tickers

    # 2b. Pre-filter by market cap
    filtered_tickers = _filter_by_market_cap(
        equity_tickers, ticker_meta_store, min_market_cap
    )
    result.tickers_after_market_cap_filter = len(filtered_tickers)

    logger.info(
        "conviction_batch_market_cap_filtered",
        before=len(equity_tickers),
        after=len(filtered_tickers),
    )

    # 3. Load OHLCV data
    ticker_data = data_store.read_tickers(filtered_tickers)

    # 4. Filter by price and volume from the DataFrames
    qualified_data = _filter_by_price_volume(ticker_data, min_price, min_avg_volume)
    result.tickers_after_price_volume_filter = len(qualified_data)

    logger.info(
        "conviction_batch_price_volume_filtered",
        before=len(ticker_data),
        after=len(qualified_data),
    )

    if not qualified_data:
        result.errors.append("No tickers passed price/volume filters")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    # 5. Run conviction engine
    signals = conviction_engine.analyze_batch(qualified_data)
    result.signals_computed = len(signals)

    # Determine as_of_date from the most recent signal
    for sig in signals:
        if sig.as_of_date:
            result.as_of_date = sig.as_of_date
            break

    # 6. Upsert snapshots
    try:
        upserted = await upsert_snapshots(signals, result.as_of_date)
        result.snapshots_upserted = upserted
    except Exception:
        logger.error("conviction_batch_upsert_failed", exc_info=True)
        result.errors.append("Snapshot upsert failed")

    # 7. Detect transitions
    try:
        transitions = await detect_and_record_transitions(result.as_of_date)
        result.transitions = transitions
        result.transitions_detected = len(transitions)
        result.new_pullback_transitions = sum(
            1 for t in transitions
            if t.to_state in ("pullback_to_8ema", "pullback_to_21ema")
        )
    except Exception:
        logger.error("conviction_batch_transition_detection_failed", exc_info=True)
        result.errors.append("Transition detection failed")

    # 8. Cleanup old data
    try:
        await cleanup_old_snapshots(retention_days)
    except Exception:
        logger.warning("conviction_batch_cleanup_failed", exc_info=True)

    result.duration_ms = (time.perf_counter() - t0) * 1000

    logger.info(
        "conviction_batch_complete",
        as_of_date=str(result.as_of_date),
        signals=result.signals_computed,
        upserted=result.snapshots_upserted,
        transitions=result.transitions_detected,
        new_pullbacks=result.new_pullback_transitions,
        duration_ms=round(result.duration_ms, 2),
        errors=result.errors,
    )

    return result


def _filter_by_market_cap(
    tickers: list[str],
    meta_store: TickerMetaStore | None,
    min_market_cap: float,
) -> list[str]:
    """Filter tickers by market cap. Tickers with no metadata pass through."""
    if meta_store is None or not meta_store.exists or min_market_cap <= 0:
        return tickers

    caps = meta_store.get_market_caps(tickers)
    passed = []
    for t in tickers:
        cap = caps.get(t)
        if cap is None or cap == 0:
            passed.append(t)
        elif cap >= min_market_cap:
            passed.append(t)
    return passed


def _filter_by_price_volume(
    ticker_data: dict[str, "pd.DataFrame"],
    min_price: float,
    min_avg_volume: int,
) -> dict[str, "pd.DataFrame"]:
    """Filter loaded DataFrames by last close price and 20-day average volume."""
    qualified = {}
    for ticker, df in ticker_data.items():
        if df.empty or len(df) < 20:
            continue

        last_close = float(df["close"].iloc[-1])
        if last_close < min_price:
            continue

        avg_vol = float(df["volume"].iloc[-20:].mean())
        if avg_vol < min_avg_volume:
            continue

        qualified[ticker] = df
    return qualified
