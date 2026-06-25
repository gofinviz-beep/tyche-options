"""Metadata-first candidate universe builder for cloud options/stocks pipelines."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import pandas as pd
import structlog

from tyche.config import TycheSettings
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.stocks_conviction_store import STOCKS_CONVICTION_REL
from tyche.market_data.universe_candidates_store import (
    OPTIONS_CANDIDATES_REL,
    STOCKS_CANDIDATES_REL,
    write_candidates_parquet,
)
from tyche.storage import exists as storage_exists, read_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.conviction_batch import (
    _filter_by_market_cap,
    _filter_by_price_volume,
)

logger = structlog.get_logger()

_PULLBACK_TRENDS = frozenset({"pullback_to_8ema", "pullback_to_21ema"})


@dataclass
class CandidateUniverseBatchResult:
    as_of_date: date
    options_candidates: int = 0
    stocks_candidates: int = 0
    meta_filtered: int = 0
    liquidity_filtered: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "options_candidates": self.options_candidates,
            "stocks_candidates": self.stocks_candidates,
            "meta_filtered": self.meta_filtered,
            "liquidity_filtered": self.liquidity_filtered,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "output_paths": [OPTIONS_CANDIDATES_REL, STOCKS_CANDIDATES_REL],
        }


def meta_first_tickers(meta_store: TickerMetaStore) -> list[str]:
    """Return equity tickers from ``ticker_meta.parquet`` (not OHLCV directory scan)."""
    if not meta_store.exists:
        return []
    df = meta_store.read_meta()
    if df.empty or "ticker" not in df.columns:
        return []
    tickers = df["ticker"].dropna().astype(str).unique().tolist()
    return meta_store.filter_equity_only(tickers)


def _load_alpha_by_ticker(
    *,
    data_dir: str,
    ctx: StorageContext,
    variant: str = "sustained",
) -> dict[str, dict[str, Any]]:
    store = AlphaSignalStore(data_dir=data_dir, variant=variant, ctx=ctx)
    signals, _, _ = store.read_latest()
    return {str(s["ticker"]): s for s in signals if s.get("ticker")}


def _load_conviction_by_ticker(*, ctx: StorageContext) -> dict[str, dict[str, Any]]:
    if not storage_exists(STOCKS_CONVICTION_REL, ctx=ctx):
        return {}
    df = read_parquet(STOCKS_CONVICTION_REL, ctx=ctx)
    if df is None or df.empty:
        return {}
    return {
        str(row["ticker"]): row
        for row in df.to_dict(orient="records")
        if row.get("ticker")
    }


def compute_priority_score(
    *,
    conviction: dict[str, Any] | None,
    alpha: dict[str, Any] | None,
) -> float:
    """Rank options candidates: CSP-ready conviction first, alpha as tiebreaker."""
    score = 0.0
    if alpha:
        alpha_score = alpha.get("alpha_score")
        if alpha_score is not None and not pd.isna(alpha_score):
            score += float(alpha_score) * 0.35
        signal = str(alpha.get("signal") or "").lower()
        if signal in {"strong_buy", "buy"}:
            score += 8.0
    if conviction:
        conv_score = conviction.get("conviction_score")
        if conv_score is not None and not pd.isna(conv_score):
            score += float(conv_score) * 35.0
        if conviction.get("csp_eligible"):
            score += 15.0
        trend = str(conviction.get("trend_state") or "").lower()
        if trend in _PULLBACK_TRENDS:
            score += 10.0
        safety = conviction.get("csp_safety_prob")
        if safety is not None and not pd.isna(safety):
            score += float(safety) * 20.0
        iv_rank = conviction.get("iv_rank")
        if iv_rank is not None and not pd.isna(iv_rank) and float(iv_rank) >= 40:
            score += min(10.0, float(iv_rank) / 10.0)
        vrp = conviction.get("vrp")
        if vrp is not None and not pd.isna(vrp) and float(vrp) > 0:
            score += min(5.0, float(vrp) * 10.0)
    return round(score, 4)


def _liquidity_metrics(df: pd.DataFrame) -> tuple[float | None, float | None]:
    if df.empty or len(df) < 20:
        return None, None
    last_close = float(df["close"].iloc[-1])
    avg_vol = float(df["volume"].iloc[-20:].mean())
    return last_close, avg_vol


def _build_candidate_row(
    ticker: str,
    *,
    market_cap: float | None,
    last_close: float | None,
    avg_volume_20d: float | None,
    conviction: dict[str, Any] | None,
    alpha: dict[str, Any] | None,
    priority_score: float,
    rank: int,
    universe: str,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "ticker": ticker,
        "universe": universe,
        "rank": rank,
        "priority_score": priority_score,
        "market_cap": market_cap,
        "last_close": last_close,
        "avg_volume_20d": avg_volume_20d,
    }
    if conviction:
        row.update(
            {
                "trend_state": conviction.get("trend_state"),
                "conviction_level": conviction.get("conviction_level"),
                "csp_eligible": conviction.get("csp_eligible"),
                "conviction_score": conviction.get("conviction_score"),
                "csp_safety_prob": conviction.get("csp_safety_prob"),
                "iv_rank": conviction.get("iv_rank"),
                "vrp": conviction.get("vrp"),
                "sector": conviction.get("sector"),
            }
        )
    if alpha:
        row.update(
            {
                "alpha_score": alpha.get("alpha_score"),
                "alpha_signal": alpha.get("signal"),
                "alpha_horizon": alpha.get("horizon"),
                "move_prob": alpha.get("move_prob"),
            }
        )
    return row


def _filter_optionable(
    tickers: list[str],
    meta_store: TickerMetaStore,
    *,
    require_optionable: bool,
) -> list[str]:
    """Keep only optionable tickers when metadata exposes the flag."""
    if not require_optionable or not meta_store.exists:
        return tickers
    df = meta_store.read_meta()
    if "optionable" not in df.columns:
        return tickers
    optionable = set(
        df.loc[df["optionable"].fillna(False).astype(bool), "ticker"].astype(str)
    )
    return [t for t in tickers if t in optionable]


def run_candidate_universe_batch(
    *,
    settings: TycheSettings,
    data_store: OHLCVStore,
    meta_store: TickerMetaStore,
    ctx: StorageContext,
    run_id: str | None = None,
    as_of_date: date | None = None,
) -> CandidateUniverseBatchResult:
    """Build metadata-first candidate universes and export signal Parquet."""
    t0 = time.perf_counter()
    as_of = as_of_date or date.today()
    result = CandidateUniverseBatchResult(as_of_date=as_of)

    tickers = meta_first_tickers(meta_store)
    if not tickers:
        result.errors.append("ticker_meta_empty")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    options_min_cap = settings.options_snapshot_min_market_cap
    stocks_min_cap = settings.min_market_cap_millions * 1_000_000

    options_meta = _filter_by_market_cap(tickers, meta_store, options_min_cap)
    stocks_meta = _filter_by_market_cap(tickers, meta_store, stocks_min_cap)
    options_meta = _filter_optionable(
        options_meta,
        meta_store,
        require_optionable=settings.require_optionable,
    )
    result.meta_filtered = len(options_meta)

    min_price = settings.conviction_batch_min_price
    min_avg_volume = settings.conviction_batch_min_avg_volume

    options_ohlcv = _filter_by_price_volume(
        data_store.read_tickers(options_meta),
        min_price,
        min_avg_volume,
    )
    stocks_ohlcv = _filter_by_price_volume(
        data_store.read_tickers(stocks_meta),
        min_price,
        min_avg_volume,
    )
    result.liquidity_filtered = len(options_ohlcv)

    alpha_by_ticker = _load_alpha_by_ticker(
        data_dir=settings.data_dir,
        ctx=ctx,
        variant="sustained",
    )
    conviction_by_ticker = _load_conviction_by_ticker(ctx=ctx)
    caps = meta_store.get_market_caps(list(options_ohlcv.keys())) if meta_store.exists else {}

    scored: list[tuple[str, float, float | None, float | None]] = []
    for ticker, df in options_ohlcv.items():
        last_close, avg_vol = _liquidity_metrics(df)
        score = compute_priority_score(
            conviction=conviction_by_ticker.get(ticker),
            alpha=alpha_by_ticker.get(ticker),
        )
        scored.append((ticker, score, last_close, avg_vol))

    scored.sort(key=lambda item: (item[1], caps.get(item[0], 0) or 0), reverse=True)
    max_options = settings.options_candidate_max_tickers
    options_rows: list[dict[str, Any]] = []
    for rank, (ticker, score, last_close, avg_vol) in enumerate(scored[:max_options], start=1):
        options_rows.append(
            _build_candidate_row(
                ticker,
                market_cap=caps.get(ticker),
                last_close=last_close,
                avg_volume_20d=avg_vol,
                conviction=conviction_by_ticker.get(ticker),
                alpha=alpha_by_ticker.get(ticker),
                priority_score=score,
                rank=rank,
                universe="options",
            )
        )

    stocks_caps = meta_store.get_market_caps(list(stocks_ohlcv.keys())) if meta_store.exists else {}
    stocks_ranked = sorted(
        stocks_ohlcv.keys(),
        key=lambda t: stocks_caps.get(t, 0) or 0,
        reverse=True,
    )
    max_stocks = settings.stocks_derived_max_tickers
    stocks_rows: list[dict[str, Any]] = []
    for rank, ticker in enumerate(stocks_ranked[:max_stocks], start=1):
        df = stocks_ohlcv[ticker]
        last_close, avg_vol = _liquidity_metrics(df)
        stocks_rows.append(
            _build_candidate_row(
                ticker,
                market_cap=stocks_caps.get(ticker),
                last_close=last_close,
                avg_volume_20d=avg_vol,
                conviction=conviction_by_ticker.get(ticker),
                alpha=alpha_by_ticker.get(ticker),
                priority_score=stocks_caps.get(ticker, 0) or 0.0,
                rank=rank,
                universe="stocks",
            )
        )

    try:
        result.options_candidates = write_candidates_parquet(
            options_rows,
            rel_path=OPTIONS_CANDIDATES_REL,
            ctx=ctx,
            as_of_date=as_of,
            run_id=run_id,
        )
    except Exception:
        logger.error("candidate_universe_options_export_failed", exc_info=True)
        result.errors.append("options_candidates_export_failed")

    try:
        result.stocks_candidates = write_candidates_parquet(
            stocks_rows,
            rel_path=STOCKS_CANDIDATES_REL,
            ctx=ctx,
            as_of_date=as_of,
            run_id=run_id,
        )
    except Exception:
        logger.error("candidate_universe_stocks_export_failed", exc_info=True)
        result.errors.append("stocks_candidates_export_failed")

    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("candidate_universe_batch_complete", **result.to_dict())
    return result
