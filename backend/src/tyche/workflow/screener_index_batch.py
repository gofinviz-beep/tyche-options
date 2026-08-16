"""Stock Screener nightly precompute batch — the v3 "Diamond Finder".

Extracts compact scalar signals (multi-timeframe RSI, EMA stack, returns,
market cap / institutional / sector metadata) for the whole equity universe
into a single queryable index (``ScreenerIndexStore``). Prefers the v2
``DeepDiveStore`` (already precomputed by ``stocks-deep-dive-batch``) and
falls back to the inline ``TickerDeepDiveEngine`` when a ticker's payload is
absent — this makes the batch (and, by extension,
``GET /stocks/screener``) work correctly even before the deep-dive batch has
ever run.

The ``setup_score`` / ``setup_label`` formulas here are the strategy's
calibration (see ``.cursor/rules/strategy-philosophy.mdc`` and
``docs/deep_dive_sonnet_prompt3``) — implemented verbatim, not to be
"improved" without a dedicated backtest-calibration pass (Prompt 3b).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from tyche.analysis.ticker_deep_dive import TickerDeepDiveEngine
from tyche.market_data.screener_index_store import SCREENER_INDEX_REL, ScreenerIndexStore
from tyche.ops.job_progress import log_job_phase, log_job_progress
from tyche.storage.paths import StorageContext
from tyche.workflow.history_summary import select_history_universe

logger = structlog.get_logger()

_PROGRESS_EVERY = 250
_JOB_NAME = "stocks-screener-index-batch"


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    """Clamp *x* to ``[lo, hi]``."""
    return max(lo, min(hi, x))


# ── Diamond Finder: setup_score components ─────────────────────────────


def _structural_trend_score(row: dict[str, Any]) -> float:
    """Component A (0-40): is this a real high-timeframe uptrend?"""
    rsi_quarterly = row.get("rsi_quarterly") or 0.0
    rsi_monthly = row.get("rsi_monthly") or 0.0
    last_close = row.get("last_close") or 0.0
    sma_200 = row.get("sma_200") or 0.0
    slope_ema_21 = row.get("slope_ema_21") or 0.0

    quarterly_pts = clamp((rsi_quarterly - 45) / (65 - 45)) * 20
    monthly_pts = clamp((rsi_monthly - 45) / (60 - 45)) * 8
    above_200_pts = 7.0 if (sma_200 > 0 and last_close > sma_200) else 0.0
    slope_pts = clamp(slope_ema_21 / 0.5) * 5
    return quarterly_pts + monthly_pts + above_200_pts + slope_pts


def _daily_rsi_component(rsi_daily: float) -> float:
    """Daily-RSI sweet spot (0-18)."""
    if 35 <= rsi_daily <= 50:
        return 18.0
    if 25 <= rsi_daily < 35:
        return (rsi_daily - 25) / (35 - 25) * 18.0
    if 50 < rsi_daily <= 65:
        return (65 - rsi_daily) / (65 - 50) * 18.0
    return 0.0


def _proximity_component(pct_vs_ema_8: float, pct_vs_ema_21: float) -> float:
    """Proximity to support (0-12), based on distance to the 8-EMA."""
    if -3 <= pct_vs_ema_8 <= 5:
        return 12.0
    if pct_vs_ema_8 < -3:
        # Below the 8-EMA but still above the 21-EMA = a deeper healthy pullback.
        return 10.0 if pct_vs_ema_21 > 0 else 0.0
    if 5 < pct_vs_ema_8 <= 12:
        return (12 - pct_vs_ema_8) / (12 - 5) * 12.0
    return 0.0


def _entry_timing_score(row: dict[str, Any]) -> float:
    """Component B (0-30): pulling back to a buy zone, not extended/falling knife."""
    rsi_daily = row.get("rsi_daily") or 0.0
    pct_vs_ema_8 = row.get("pct_vs_ema_8") or 0.0
    pct_vs_ema_21 = row.get("pct_vs_ema_21") or 0.0
    return _daily_rsi_component(rsi_daily) + _proximity_component(pct_vs_ema_8, pct_vs_ema_21)


def _market_cap_component(market_cap: float | None) -> float:
    if not market_cap or market_cap <= 0:
        return 1.0
    if market_cap >= 10e9:
        return 8.0
    if market_cap >= 4e9:
        return 6.0
    if market_cap >= 1e9:
        return 4.0
    return 2.0


def _quality_score(row: dict[str, Any]) -> float:
    """Component C (0-20): is it institutional-grade?"""
    market_cap = row.get("market_cap")
    institutional_pct = row.get("institutional_pct") or 0.0
    last_close = row.get("last_close") or 0.0
    ema_50 = row.get("ema_50") or 0.0

    cap_pts = _market_cap_component(market_cap)
    # institutional_pct is a 0-1 fraction (TickerMetaStore.get_institutional_pcts);
    # the calibration is expressed on the 0-100 scale and tops out at 60% held.
    inst_pts = clamp(institutional_pct * 100 / 60) * 7
    above_50_pts = 5.0 if (ema_50 > 0 and last_close > ema_50) else 0.0
    return cap_pts + inst_pts + above_50_pts


def _ret_3m_component(ret_3m: float | None) -> float:
    if ret_3m is None:
        return 0.0
    if 0 < ret_3m <= 40:
        return 5.0
    if ret_3m > 40:
        return 2.0
    return 0.0


def _momentum_score(row: dict[str, Any]) -> float:
    """Component D (0-10): momentum confirmation."""
    rsi_weekly = row.get("rsi_weekly") or 0.0
    ret_3m = row.get("ret_3m")

    weekly_pts = 5.0 if rsi_weekly >= 50 else 0.0
    ret_pts = _ret_3m_component(ret_3m)
    return weekly_pts + ret_pts


def compute_setup_score(row: dict[str, Any]) -> float:
    """Compute the composite 0-100 ``setup_score`` for one screener row.

    Sums the four Diamond Finder components then applies the anti-chase
    haircut (0.6x) when daily RSI >= 70 and the ticker is >10% above its
    8-EMA. Result is clamped to [0, 100] and rounded to 1 decimal place.
    """
    total = (
        _structural_trend_score(row)
        + _entry_timing_score(row)
        + _quality_score(row)
        + _momentum_score(row)
    )

    rsi_daily = row.get("rsi_daily") or 0.0
    pct_vs_ema_8 = row.get("pct_vs_ema_8") or 0.0
    if rsi_daily >= 70 and pct_vs_ema_8 > 10:
        total *= 0.6

    return round(clamp(total, 0.0, 100.0), 1)


def compute_setup_label(row: dict[str, Any], setup_score: float) -> str:
    """Evaluate the ordered ``setup_label`` table — first match wins."""
    rsi_quarterly = row.get("rsi_quarterly") or 0.0
    rsi_monthly = row.get("rsi_monthly") or 0.0
    rsi_daily = row.get("rsi_daily") or 0.0
    last_close = row.get("last_close") or 0.0
    sma_200 = row.get("sma_200") or 0.0
    above_200 = sma_200 > 0 and last_close > sma_200

    if (
        setup_score >= 70
        and rsi_quarterly >= 58
        and 35 <= rsi_daily <= 52
        and above_200
    ):
        return "Prime Pullback"
    if setup_score >= 60 and rsi_quarterly >= 55 and above_200:
        return "Structural Uptrend"
    if 50 <= rsi_quarterly < 60 and 40 <= rsi_daily <= 60 and above_200:
        return "Emerging Breakout"
    if rsi_daily >= 70 and rsi_quarterly < 55:
        return "Overextended"
    if rsi_quarterly < 40 and rsi_monthly < 45:
        return "Weak Structure"
    return "Watch / Base Building"


def build_screener_row(ticker: str, deep_dive: Any) -> dict[str, Any] | None:
    """Extract scalar screener columns from a deep-dive result.

    Accepts either a ``TickerDeepDiveResponse`` (from ``DeepDiveStore``) or a
    ``TickerDeepDive`` dataclass (from the inline engine fallback) — both
    expose the same attribute shape. Returns ``None`` when there's no price
    data (``last_close == 0.0``).
    """
    if deep_dive is None or not getattr(deep_dive, "last_close", 0.0):
        return None

    returns = deep_dive.returns or {}
    ema_stack = deep_dive.ema_stack
    rsi = deep_dive.rsi
    macd = deep_dive.macd
    last_close = deep_dive.last_close
    sma_200 = ema_stack.sma_200

    row: dict[str, Any] = {
        "ticker": ticker,
        "name": deep_dive.name or "",
        "sector": deep_dive.sector or "",
        "as_of_date": deep_dive.as_of_date or "",
        "last_close": last_close,
        "market_cap": deep_dive.market_cap,
        "institutional_pct": deep_dive.institutional_pct,
        "pct_off_52w_high": deep_dive.pct_off_52w_high,
        "rsi_daily": rsi.daily,
        "rsi_weekly": rsi.weekly,
        "rsi_monthly": rsi.monthly,
        "rsi_quarterly": rsi.quarterly,
        "ema_8": ema_stack.ema_8,
        "ema_21": ema_stack.ema_21,
        "ema_50": ema_stack.ema_50,
        "sma_200": sma_200,
        "pct_vs_ema_8": ema_stack.pct_vs_ema_8,
        "pct_vs_ema_21": ema_stack.pct_vs_ema_21,
        "pct_vs_sma_200": ema_stack.pct_vs_sma_200,
        "slope_ema_8": ema_stack.slope_ema_8,
        "slope_ema_21": ema_stack.slope_ema_21,
        "slope_ema_50": ema_stack.slope_ema_50,
        "days_above_ema_8": ema_stack.days_above_ema_8,
        "days_above_ema_21": ema_stack.days_above_ema_21,
        "stack_score": ema_stack.stack_score,
        "above_sma_200": bool(sma_200 > 0 and last_close > sma_200),
        "ret_1m": returns.get("1M"),
        "ret_3m": returns.get("3M"),
        "ret_6m": returns.get("6M"),
        "ret_1y": returns.get("1Y"),
        "macd_histogram": macd.histogram,
    }
    score = compute_setup_score(row)
    row["setup_score"] = score
    row["setup_label"] = compute_setup_label(row, score)
    return row


@dataclass
class ScreenerIndexResult:
    """Summary of a Stock Screener index precompute batch run."""

    as_of_date: date
    universe_size: int = 0
    tickers_indexed: int = 0
    tickers_skipped: int = 0
    tickers_written: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "universe_size": self.universe_size,
            "tickers_indexed": self.tickers_indexed,
            "tickers_skipped": self.tickers_skipped,
            "tickers_written": self.tickers_written,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "output_paths": [SCREENER_INDEX_REL],
        }


def _process_one(ticker: str, deep_dive_store: Any, engine: Any) -> dict[str, Any] | None:
    """Resolve one ticker's screener row: precomputed store, else inline engine."""
    stored = deep_dive_store.read_ticker(ticker) if deep_dive_store is not None else None
    if stored is not None:
        deep_dive, _as_of = stored
    else:
        deep_dive = engine.analyze(ticker)
    return build_screener_row(ticker, deep_dive)


async def run_screener_index_batch(
    *,
    deep_dive_store: Any,
    ohlcv_store: Any,
    meta_store: Any,
    fundamentals_store: Any = None,
    estimates_store: Any = None,
    catalyst_store: Any = None,
    min_market_cap_millions: float,
    ctx: StorageContext | None = None,
) -> ScreenerIndexResult:
    """Precompute the compact screener index across the equity universe."""
    t0 = time.perf_counter()
    as_of = date.today()
    result = ScreenerIndexResult(as_of_date=as_of)

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

    log_job_phase(_JOB_NAME, "index", universe=len(universe))

    engine = TickerDeepDiveEngine(
        ohlcv_store=ohlcv_store,
        meta_store=meta_store,
        fundamentals_store=fundamentals_store,
        estimates_store=estimates_store,
        catalyst_store=catalyst_store,
    )

    semaphore = asyncio.Semaphore(8)
    rows: list[dict[str, Any]] = []
    done = 0
    start_time = time.monotonic()
    lock = asyncio.Lock()

    async def _worker(ticker: str) -> None:
        nonlocal done
        async with semaphore:
            try:
                row = await asyncio.to_thread(
                    _process_one, ticker, deep_dive_store, engine
                )
            except Exception:
                logger.error(
                    "screener_index_ticker_failed", ticker=ticker, exc_info=True
                )
                row = None
        async with lock:
            done += 1
            if row is None:
                result.tickers_skipped += 1
            else:
                rows.append(row)
                result.tickers_indexed += 1
            if done % _PROGRESS_EVERY == 0:
                log_job_progress(
                    _JOB_NAME,
                    "index",
                    done=done,
                    total=len(universe),
                    start_time=start_time,
                )

    await asyncio.gather(*(_worker(t) for t in universe))

    log_job_phase(
        _JOB_NAME,
        "index",
        status="complete",
        indexed=result.tickers_indexed,
        skipped=result.tickers_skipped,
    )

    if not ctx:
        result.errors.append("No StorageContext provided; skipping write")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    store = ScreenerIndexStore(ctx=ctx)
    log_job_phase(_JOB_NAME, "write", rows=len(rows))
    result.tickers_written = store.write(rows, ctx=ctx)
    log_job_phase(_JOB_NAME, "write", status="complete", written=result.tickers_written)

    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("screener_index_batch_complete", **result.to_dict())
    return result
