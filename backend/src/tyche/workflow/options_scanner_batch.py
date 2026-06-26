"""Slice 5 — cloud CSP scanner using pre-built conviction + flatfile chains."""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import structlog

from tyche.broker.artifact_chain import build_artifact_chain_broker
from tyche.broker.base import Quote
from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionSignal, TrendState
from tyche.market_data.data_store import TickerMetaStore
from tyche.market_data.options_chain_snapshot_store import load_prep_contracts_parquet
from tyche.market_data.options_scanner_store import (
    OPTIONS_SCANNER_REL,
    OPTIONS_SCANNER_REPORT_REL,
    scored_candidate_to_row,
    write_scanner_parquet,
    write_scanner_report,
)
from tyche.market_data.stocks_conviction_store import STOCKS_CONVICTION_REL
from tyche.market_data.universe_candidates_store import (
    CSP_SCAN_TICKERS_REL,
    load_candidates_parquet,
    load_csp_scan_tickers,
)
from tyche.storage import read_parquet
from tyche.storage.paths import StorageContext
from tyche.strategy.engine import StrategyEngine
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.workflow.morning_scan import PipelineStage

logger = structlog.get_logger()

_CHAIN_SOURCE_FLATFILE = "flatfile"
_DEFAULT_SCANNER_TOP_N = 10


@dataclass
class OptionsScannerBatchResult:
    as_of_date: date
    scan_id: str
    symbols_requested: int = 0
    symbols_scanned: int = 0
    csp_candidates: int = 0
    chain_source: str = _CHAIN_SOURCE_FLATFILE
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "as_of_date": self.as_of_date.isoformat(),
            "scan_id": self.scan_id,
            "symbols_requested": self.symbols_requested,
            "symbols_scanned": self.symbols_scanned,
            "csp_candidates": self.csp_candidates,
            "chain_source": self.chain_source,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "output_paths": [OPTIONS_SCANNER_REL, OPTIONS_SCANNER_REPORT_REL],
        }


def _trend_state(value: Any) -> TrendState:
    if isinstance(value, TrendState):
        return value
    text = str(value or TrendState.INSUFFICIENT_DATA.value).lower()
    try:
        return TrendState(text)
    except ValueError:
        return TrendState.INSUFFICIENT_DATA


def conviction_row_to_signal(row: dict[str, Any]) -> ConvictionSignal:
    return ConvictionSignal(
        ticker=str(row.get("ticker") or ""),
        trend_state=_trend_state(row.get("trend_state")),
        conviction_level=str(row.get("conviction_level") or "none"),
        raw_conviction=str(
            row.get("raw_conviction") or row.get("conviction_level") or "none"
        ),
        csp_eligible=bool(row.get("csp_eligible")),
        last_close=float(row.get("last_close") or 0.0),
        ema_8=float(row.get("ema_8") or 0.0),
        ema_21=float(row.get("ema_21") or 0.0),
        ema_8_slope=float(row.get("ema_8_slope") or 0.0),
        ema_21_slope=float(row.get("ema_21_slope") or 0.0),
        price_to_8ema_pct=float(row.get("price_to_8ema_pct") or 0.0),
        price_to_21ema_pct=float(row.get("price_to_21ema_pct") or 0.0),
        volume_declining_on_pullback=bool(row.get("volume_declining") or False),
        avg_volume_20d=int(row.get("avg_volume_20d") or 0),
        latest_volume=int(row.get("latest_volume") or 0),
        days_above_both_emas=int(row.get("days_above_both_emas") or 0),
        prior_streak=int(row.get("prior_streak") or 0),
        ema_50=float(row.get("ema_50") or 0.0),
        ema_50_slope=float(row.get("ema_50_slope") or 0.0),
        price_to_50ema_pct=float(row.get("price_to_50ema_pct") or 0.0),
        rsi_14=float(row.get("rsi_14") or 0.0),
        iv_rank=row.get("iv_rank"),
        iv_percentile=row.get("iv_percentile"),
        atm_iv=row.get("atm_iv"),
        vrp=row.get("vrp"),
        conviction_score=float(row.get("conviction_score") or 0.0),
        csp_safety_prob=row.get("csp_safety_prob"),
    )


def _load_conviction_by_ticker(*, ctx: StorageContext) -> dict[str, dict[str, Any]]:
    df = read_parquet(STOCKS_CONVICTION_REL, ctx=ctx)
    if df is None or df.empty:
        return {}
    return {
        str(row["ticker"]): row
        for row in df.to_dict(orient="records")
        if row.get("ticker")
    }


def _filter_institutional(
    tickers: list[str],
    meta_store: TickerMetaStore,
    *,
    min_pct: float,
) -> tuple[list[str], dict[str, float]]:
    if min_pct <= 0 or not meta_store.exists:
        return tickers, {}
    inst = meta_store.get_institutional_pcts(tickers)
    passed: list[str] = []
    for ticker in tickers:
        pct = inst.get(ticker)
        if pct is None or pct >= min_pct:
            passed.append(ticker)
    return passed, {
        t: round(v * 100, 1)
        for t, v in inst.items()
        if t in passed and v is not None
    }


async def run_options_scanner_batch(
    *,
    settings: TycheSettings,
    ctx: StorageContext,
    meta_store: TickerMetaStore | None = None,
    run_id: str | None = None,
    as_of_date: date | None = None,
) -> OptionsScannerBatchResult:
    """Run CSP scan over ``csp_scan_tickers`` using flatfile chain artifacts."""
    t0 = time.perf_counter()
    as_of = as_of_date or date.today()
    scan_id = run_id or str(uuid.uuid4())
    result = OptionsScannerBatchResult(as_of_date=as_of, scan_id=scan_id)
    meta_store = meta_store or TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)

    tickers, csp_as_of = load_csp_scan_tickers(ctx=ctx)
    result.symbols_requested = len(tickers)
    if not tickers:
        result.errors.append("csp_scan_tickers_empty")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    conviction_rows = _load_conviction_by_ticker(ctx=ctx)
    conviction_signals = {
        ticker: conviction_row_to_signal(conviction_rows[ticker])
        for ticker in tickers
        if ticker in conviction_rows
    }

    before_inst = len(tickers)
    tickers, inst_map = _filter_institutional(
        tickers,
        meta_store,
        min_pct=settings.min_institutional_pct,
    )
    inst_stage = PipelineStage(
        "Institutional Ownership",
        before_inst,
        len(tickers),
        detail=f"Min {settings.min_institutional_pct * 100:.0f}% inst. ownership",
    )

    contract_rows, chain_as_of = load_prep_contracts_parquet(ctx=ctx, tickers=tickers)
    tickers_with_chains = {
        str(row.get("ticker"))
        for row in contract_rows
        if row.get("ticker")
    }
    tickers = [t for t in tickers if t in tickers_with_chains]
    chain_stage = PipelineStage(
        "Chain Artifacts",
        before_inst,
        len(tickers),
        detail=f"source={_CHAIN_SOURCE_FLATFILE}, chain_date={chain_as_of}",
    )

    if not tickers:
        result.errors.append("no_tickers_with_chain_artifacts")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    quotes: dict[str, Quote] = {}
    csp_records, _ = load_candidates_parquet(rel_path=CSP_SCAN_TICKERS_REL, ctx=ctx)
    close_by_ticker = {
        str(row["ticker"]): float(row.get("last_close") or 0.0)
        for row in csp_records
        if row.get("ticker")
    }
    for ticker in tickers:
        last_close = (
            close_by_ticker.get(ticker) or conviction_signals[ticker].last_close
        )
        quotes[ticker] = Quote(
            symbol=ticker,
            last=last_close,
            bid=last_close,
            ask=last_close,
            high=last_close,
            low=last_close,
            open=last_close,
            close=last_close,
            volume=0,
            change=0.0,
            change_pct=0.0,
        )

    broker = build_artifact_chain_broker(
        contract_rows,
        quotes=quotes,
        available_cash=float(settings.available_capital),
    )
    strategy_engine = StrategyEngine()
    scan_start = time.perf_counter()
    csp_pool, csp_diagnostics = await strategy_engine.scan_csp_candidates(
        broker=broker,
        watchlist=tickers,
        available_cash=float(settings.available_capital),
        conviction_signals=conviction_signals,
        min_oi=0,
        min_volume=settings.csp_min_volume,
        min_bid=settings.csp_min_bid,
        min_premium_pct=settings.csp_min_premium_pct,
        top_n=_DEFAULT_SCANNER_TOP_N,
        max_expirations=settings.max_expiration_dates,
        strike_range_pct=settings.strike_range_pct,
        expiration_mode=settings.expiration_mode,
        csp_strike_preference=settings.csp_strike_preference,
        pullback_strike_offset_pct=settings.pullback_strike_offset_pct,
        pullback_strike_ceiling_pct=settings.pullback_strike_ceiling_pct,
        earliest_expiration_only=settings.earliest_expiration_only,
        min_scan_dte=settings.min_scan_dte,
        target_dte_sweet_spot=settings.target_dte_sweet_spot,
        pre_allocator_pool_size=settings.pre_allocator_pool_size,
    )
    csp_dur = (time.perf_counter() - scan_start) * 1000
    top_candidates: list[ScoredCandidate] = csp_pool[:_DEFAULT_SCANNER_TOP_N]
    csp_stage = PipelineStage(
        "CSP Options Scan",
        len(tickers),
        len(top_candidates),
        detail=(
            f"{len(top_candidates)} candidates from "
            f"{csp_diagnostics.get('symbols_with_candidates', 0)} tickers"
        ),
        duration_ms=csp_dur,
    )

    scanned_at = datetime.now(timezone.utc).isoformat()
    scan_meta = {
        "scan_id": scan_id,
        "as_of_date": (csp_as_of or as_of.isoformat()),
        "scanned_at": scanned_at,
        "chain_source": _CHAIN_SOURCE_FLATFILE,
        "source_run_id": run_id,
    }
    parquet_rows = [
        scored_candidate_to_row(candidate, scan_meta=scan_meta)
        for candidate in top_candidates
    ]

    try:
        result.csp_candidates = write_scanner_parquet(parquet_rows, ctx=ctx)
    except Exception:
        logger.error("options_scanner_parquet_failed", exc_info=True)
        result.errors.append("scanner_parquet_export_failed")

    report = {
        "scan_id": scan_id,
        "as_of_date": csp_as_of or as_of.isoformat(),
        "scanned_at": scanned_at,
        "symbols_scanned": len(tickers),
        "chain_source": _CHAIN_SOURCE_FLATFILE,
        "candidate_source": CSP_SCAN_TICKERS_REL,
        "chain_artifact": "signals/options/options_chain_contracts.parquet",
        "conviction_source": STOCKS_CONVICTION_REL,
        "pipeline_stages": [
            inst_stage.to_dict(),
            chain_stage.to_dict(),
            csp_stage.to_dict(),
        ],
        "institutional_ownership": inst_map,
        "csp_diagnostics": csp_diagnostics,
        "errors": result.errors,
        "allocation": None,
        "allocated_trades": [],
        "earnings_context": {},
        "run_id": run_id,
        **result.to_dict(),
    }
    try:
        write_scanner_report(report, ctx=ctx)
    except Exception:
        logger.error("options_scanner_report_failed", exc_info=True)
        result.errors.append("scanner_report_export_failed")

    result.symbols_scanned = len(tickers)
    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("options_scanner_batch_complete", **result.to_dict())
    return result
