"""Morning scan workflow — the primary daily CSP + CC screening pipeline.

Enhanced with conviction engine (8/21 EMA) and order intent generation.
All stages are timed and reported via OpenTelemetry metrics.
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient
from tyche.conviction.alerts import PullbackAlert, detect_pullback_alerts
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.institutional import (
    filter_by_institutional_ownership,
    filter_by_institutional_ownership_batched,
)
from tyche.market_data.universe import UniverseBuilder
from tyche.risk.engine import RiskEngine
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.allocator import AllocatedTrade, AllocationResult, PortfolioAllocator
from tyche.strategy.engine import StrategyEngine
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.telemetry import (
    llm_call_duration,
    scanner_errors,
    scanner_stage_duration,
    scanner_total_duration,
)
from tyche.persistence.conviction_repository import (
    upsert_snapshots as _upsert_conviction_snapshots,
    detect_and_record_transitions as _detect_conviction_transitions,
)
from tyche.workflow.expiry_tracker import CSPFallbackAlert, ExpiryTracker
from tyche.workflow.stock_recommender import StockBuyRecommendation, generate_stock_recommendations

logger = structlog.get_logger()


class PipelineStage:
    """A single filter stage in the scan pipeline."""

    def __init__(
        self,
        name: str,
        input_count: int,
        output_count: int,
        detail: str = "",
        duration_ms: float = 0.0,
    ) -> None:
        self.name = name
        self.input_count = input_count
        self.output_count = output_count
        self.dropped = input_count - output_count
        self.detail = detail
        self.duration_ms = duration_ms

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "input": self.input_count,
            "output": self.output_count,
            "dropped": self.dropped,
            "detail": self.detail,
            "duration_ms": round(self.duration_ms, 2),
        }


def _record_stage(name: str, duration_s: float) -> None:
    """Record a pipeline stage duration as an OTel histogram observation."""
    scanner_stage_duration.record(duration_s, {"stage": name})


class MorningScanResult:
    """Results of a morning scan run."""

    def __init__(self) -> None:
        self.scan_id: str = str(uuid.uuid4())
        self.scanned_at: datetime = datetime.now(timezone.utc)
        self.symbols_scanned: int = 0
        self.csp_candidates: list[ScoredCandidate] = []
        self.cc_candidates: list[ScoredCandidate] = []
        self.csp_analyses: list[CSPAnalysis] = []
        self.conviction_signals: dict[str, ConvictionSignal] = {}
        self.earnings_context: dict[str, Any] = {}
        self.institutional_ownership: dict[str, float] = {}
        self.allocation: AllocationResult | None = None
        self.errors: list[str] = []
        self.pipeline_stages: list[PipelineStage] = []
        self.total_duration_ms: float = 0.0
        self.pullback_alerts: list[PullbackAlert] = []
        self.stock_recommendations: list[StockBuyRecommendation] = []
        self.csp_fallback_alerts: list[CSPFallbackAlert] = []


async def run_morning_scan(
    broker: BrokerClient,
    strategy_engine: StrategyEngine,
    analysis_agent: AnalysisAgent | None,
    earnings_client: EarningsCalendarClient | None,
    universe_builder: UniverseBuilder,
    watchlist: list[str],
    conviction_engine: ConvictionEngine | None = None,
    data_store: OHLCVStore | None = None,
    ticker_meta_store: TickerMetaStore | None = None,
    portfolio_allocator: PortfolioAllocator | None = None,
    top_n: int = 5,
    available_capital_override: float = 0.0,
    min_institutional_pct: float = 0.40,
    min_market_cap: float = 500_000_000.0,
    max_expiration_dates: int = 2,
    expiration_mode: str = "friday_target",
    strike_range_pct: float = 15.0,
    llm_concurrency: int = 5,
    csp_strike_preference: str = "legacy",
    pullback_strike_offset_pct: float = 5.0,
    pullback_strike_ceiling_pct: float = 1.0,
    earliest_expiration_only: bool = False,
    min_scan_dte: int = 5,
    target_dte_sweet_spot: int = 14,
    csp_min_bid: float = 0.50,
    csp_min_premium_pct: float = 0.5,
    csp_min_volume: int = 10,
    csp_min_oi: int = 50,
    min_institutional_pct_stock_buy: float = 0.50,
    notification_dispatcher: Any | None = None,
    allow_missing_market_cap: bool = True,
    institutional_batch_size: int = 20,
    institutional_max_retries: int = 2,
    pre_allocator_pool_size: int = 0,
    economic_calendar: Any | None = None,
    target_expiration: str | None = None,
) -> MorningScanResult:
    """Execute the full morning scan pipeline.

    Steps:
    1. Load account state (balances, positions, open orders)
    2. Screen watchlist through fundamental gates
    3. Run conviction engine (8/21 EMA) if data store available
    4. Fetch earnings dates
    5. Scan for CSP candidates (deterministic) - only CSP-eligible stocks
    6. Scan for CC candidates on held shares
    7. Send top candidates to LLM with conviction context (if available)
    """
    scan_start = time.perf_counter()
    result = MorningScanResult()

    # ── 1. Load account state ─────────────────────────────────────────
    t0 = time.perf_counter()
    try:
        balance = await broker.get_account_balances()
        positions = await broker.get_positions()
        open_orders = await broker.get_open_orders()
    except Exception as exc:
        dur = (time.perf_counter() - t0) * 1000
        result.errors.append(f"Failed to load account state: {exc}")
        logger.error("morning_scan_account_failed", duration_ms=round(dur, 2), exc_info=True)
        scanner_errors.add(1, {"stage": "account_load", "error_type": type(exc).__name__})
        _record_stage("account_load", time.perf_counter() - t0)
        result.total_duration_ms = (time.perf_counter() - scan_start) * 1000
        return result

    acct_dur = time.perf_counter() - t0
    _record_stage("account_load", acct_dur)

    effective_buying_power = balance.buying_power
    if effective_buying_power <= 0 and available_capital_override > 0:
        effective_buying_power = available_capital_override
        logger.info(
            "capital_override_applied",
            broker_buying_power=balance.buying_power,
            override=available_capital_override,
        )

    logger.info(
        "morning_scan_account_loaded",
        cash=balance.cash,
        buying_power=effective_buying_power,
        positions=len(positions),
        open_orders=len(open_orders),
        duration_ms=round(acct_dur * 1000, 2),
    )

    # ── 2. Build the screening universe ───────────────────────────────
    t0 = time.perf_counter()
    input_symbols = watchlist[:] if watchlist else []
    if watchlist:
        screened = universe_builder.screen_watchlist(watchlist)
        screened_symbols = [s.symbol for s in screened]
        logger.info("scan_using_watchlist", symbols=len(screened_symbols))
    elif data_store and data_store.exists:
        screened_symbols = data_store.screen_universe(
            min_avg_volume=universe_builder._min_vol,
            min_price=universe_builder._min_price,
        )
        input_symbols = screened_symbols[:]
        logger.info("scan_using_dynamic_universe", symbols=len(screened_symbols))
    else:
        result.errors.append(
            "No watchlist configured and data store not bootstrapped. "
            "Either set TYCHE_WATCHLIST_SYMBOLS or bootstrap the data store first."
        )
        result.total_duration_ms = (time.perf_counter() - scan_start) * 1000
        return result

    screen_dur = time.perf_counter() - t0
    _record_stage("fundamental_screen", screen_dur)

    result.symbols_scanned = len(input_symbols)
    result.pipeline_stages.append(
        PipelineStage(
            "Fundamental Screen",
            len(input_symbols),
            len(screened_symbols),
            detail="Price/volume gates via UniverseBuilder",
            duration_ms=screen_dur * 1000,
        )
    )

    if not screened_symbols:
        result.errors.append("No symbols passed fundamental screening")
        result.total_duration_ms = (time.perf_counter() - scan_start) * 1000
        return result

    # ── 2b. Filter by market cap (equity only — excludes ETFs) ──────
    market_caps: dict[str, float] = {}
    if ticker_meta_store and ticker_meta_store.exists and min_market_cap > 0:
        t0 = time.perf_counter()
        before_equity = len(screened_symbols)
        screened_symbols = ticker_meta_store.filter_equity_only(screened_symbols)
        equity_removed = before_equity - len(screened_symbols)

        market_caps = ticker_meta_store.get_market_caps(screened_symbols)
        before = len(screened_symbols)
        passed = []
        no_data = []
        dropped_below = []
        for sym in screened_symbols:
            cap = market_caps.get(sym)
            if cap is None or cap == 0:
                if allow_missing_market_cap:
                    passed.append(sym)
                no_data.append(sym)
            elif cap >= min_market_cap:
                passed.append(sym)
            else:
                dropped_below.append(sym)
        screened_symbols = passed
        cap_dur = time.perf_counter() - t0
        _record_stage("market_cap", cap_dur)

        detail = f"Min ${min_market_cap/1e6:.0f}M"
        if equity_removed > 0:
            detail += f" ({equity_removed} non-equity filtered)"
        if no_data:
            action = "passed" if allow_missing_market_cap else "dropped"
            detail += f" ({len(no_data)} no data: {action})"
        if dropped_below:
            detail += f" ({len(dropped_below)} below threshold)"
        result.pipeline_stages.append(
            PipelineStage("Market Cap", before_equity, len(screened_symbols), detail=detail, duration_ms=cap_dur * 1000)
        )
        logger.info(
            "market_cap_filter_applied",
            before=before_equity,
            after=len(screened_symbols),
            equity_removed=equity_removed,
            no_data_count=len(no_data),
            no_data_action="pass" if allow_missing_market_cap else "drop",
            dropped_below_cap=len(dropped_below),
            min_market_cap=min_market_cap,
            allow_missing=allow_missing_market_cap,
            duration_ms=round(cap_dur * 1000, 2),
        )

    if not screened_symbols:
        result.errors.append("No symbols passed market cap screening")
        result.total_duration_ms = (time.perf_counter() - scan_start) * 1000
        return result

    # ── 3. Run conviction engine ──────────────────────────────────────
    if conviction_engine and data_store and data_store.exists:
        t0 = time.perf_counter()
        before_conviction = len(screened_symbols)
        try:
            ticker_data = data_store.read_tickers(screened_symbols)
            if ticker_data:
                signals = conviction_engine.analyze_batch(
                    ticker_data, requested_tickers=screened_symbols
                )
                for sig in signals:
                    result.conviction_signals[sig.ticker] = sig

                eligible_symbols = [
                    sig.ticker for sig in signals if sig.csp_eligible
                ]
                uptrend_count = sum(
                    1 for sig in signals
                    if sig.csp_eligible and sig.trend_state.value.startswith("pullback") is False
                    and sig.trend_state.value in ("strong_uptrend", "uptrend")
                )
                pullback_count = sum(
                    1 for sig in signals
                    if sig.csp_eligible and sig.trend_state.value.startswith("pullback")
                )
                if eligible_symbols:
                    screened_symbols = eligible_symbols
                    logger.info(
                        "conviction_filter_applied",
                        total=len(signals),
                        eligible=len(eligible_symbols),
                        uptrend=uptrend_count,
                        pullback=pullback_count,
                    )
                else:
                    logger.warning("conviction_no_eligible", total=len(signals))

                conv_dur = time.perf_counter() - t0
                _record_stage("conviction_engine", conv_dur)
                result.pipeline_stages.append(
                    PipelineStage(
                        "EMA Conviction",
                        before_conviction,
                        len(screened_symbols),
                        detail=f"8/21 EMA: {uptrend_count} uptrend + {pullback_count} pullback CSP eligible",
                        duration_ms=conv_dur * 1000,
                    )
                )
        except Exception:
            conv_dur = time.perf_counter() - t0
            _record_stage("conviction_engine", conv_dur)
            scanner_errors.add(1, {"stage": "conviction_engine", "error_type": "exception"})
            logger.warning("morning_scan_conviction_failed", duration_ms=round(conv_dur * 1000, 2), exc_info=True)

    # ── 3b. Filter by institutional ownership (always-on, batched) ───
    if min_institutional_pct > 0:
        t0 = time.perf_counter()
        before_inst = len(screened_symbols)
        try:
            screened_symbols, inst_map, inst_stats = await filter_by_institutional_ownership_batched(
                screened_symbols,
                min_pct=min_institutional_pct,
                batch_size=institutional_batch_size,
                max_retries=institutional_max_retries,
            )
            result.institutional_ownership = inst_map
            if inst_map and ticker_meta_store and ticker_meta_store.exists:
                ticker_meta_store.update_institutional_pcts(inst_map)
            inst_dur = time.perf_counter() - t0
            _record_stage("institutional_ownership", inst_dur)

            stats_detail = (
                f"Min {min_institutional_pct*100:.0f}% inst. ownership | "
                f"batches={inst_stats.batches_run}, "
                f"failed={inst_stats.batches_failed}, "
                f"no_data={inst_stats.tickers_no_data}"
            )
            result.pipeline_stages.append(
                PipelineStage(
                    "Institutional Ownership",
                    before_inst,
                    len(screened_symbols),
                    detail=stats_detail,
                    duration_ms=inst_dur * 1000,
                )
            )
            logger.info(
                "institutional_filter_applied",
                passed=len(screened_symbols),
                ownership_data=len(inst_map),
                persisted=len(inst_map) if inst_map else 0,
                stats=inst_stats.to_dict(),
                duration_ms=round(inst_dur * 1000, 2),
            )
        except Exception:
            inst_dur = time.perf_counter() - t0
            _record_stage("institutional_ownership", inst_dur)
            scanner_errors.add(1, {"stage": "institutional_ownership", "error_type": "exception"})
            logger.warning("institutional_filter_failed", duration_ms=round(inst_dur * 1000, 2), exc_info=True)

    # ── 4. Fetch earnings dates ───────────────────────────────────────
    t0 = time.perf_counter()
    earnings_dates: dict[str, Any] = {}
    if earnings_client:
        try:
            raw_earnings = await earnings_client.get_upcoming_earnings(
                screened_symbols
            )
            for symbol, info in raw_earnings.items():
                earnings_dates[symbol] = info.get("earnings_date")
                result.earnings_context[symbol] = info
        except Exception:
            scanner_errors.add(1, {"stage": "earnings_fetch", "error_type": "exception"})
            logger.warning("morning_scan_earnings_failed", exc_info=True)
    earn_dur = time.perf_counter() - t0
    _record_stage("earnings_fetch", earn_dur)

    # ── 5. Scan for CSP candidates ────────────────────────────────────
    t0 = time.perf_counter()
    csp_diagnostics: dict[str, int] = {}
    csp_pool: list[ScoredCandidate] = []
    try:
        if target_expiration:
            logger.info(
                "target_expiration_override",
                target_expiration=target_expiration,
                bypassing_dte_filters=True,
            )

        csp_pool, csp_diagnostics = await strategy_engine.scan_csp_candidates(
            broker=broker,
            watchlist=screened_symbols,
            available_cash=effective_buying_power,
            earnings_dates=earnings_dates,
            conviction_signals=result.conviction_signals,
            min_oi=csp_min_oi,
            min_volume=csp_min_volume,
            min_bid=csp_min_bid,
            min_premium_pct=csp_min_premium_pct,
            top_n=top_n,
            max_expirations=max_expiration_dates,
            strike_range_pct=strike_range_pct,
            expiration_mode=expiration_mode,
            csp_strike_preference=csp_strike_preference,
            pullback_strike_offset_pct=pullback_strike_offset_pct,
            pullback_strike_ceiling_pct=pullback_strike_ceiling_pct,
            earliest_expiration_only=earliest_expiration_only,
            min_scan_dte=min_scan_dte,
            target_dte_sweet_spot=target_dte_sweet_spot,
            pre_allocator_pool_size=pre_allocator_pool_size,
            economic_calendar=economic_calendar,
            target_expiration=target_expiration,
        )
        result.csp_candidates = csp_pool[:top_n]
    except Exception as exc:
        result.errors.append(f"CSP scan failed: {exc}")
        scanner_errors.add(1, {"stage": "csp_scan", "error_type": type(exc).__name__})
        logger.error("morning_scan_csp_failed", exc_info=True)
    csp_dur = time.perf_counter() - t0
    _record_stage("csp_scan", csp_dur)

    pool_detail = ""
    if pre_allocator_pool_size > 0 and len(csp_pool) > len(result.csp_candidates):
        pool_detail = f" | pool={len(csp_pool)}, display={len(result.csp_candidates)}"
    diag_parts = [f"{k}: {v}" for k, v in csp_diagnostics.items() if v > 0]
    result.pipeline_stages.append(
        PipelineStage(
            "CSP Options Scan",
            len(screened_symbols),
            len(result.csp_candidates),
            detail=(
                f"{len(result.csp_candidates)} candidates from "
                f"{csp_diagnostics.get('symbols_with_candidates', 0)} tickers"
                + pool_detail
                + (f" | drops: {', '.join(diag_parts)}" if diag_parts else "")
            ),
            duration_ms=csp_dur * 1000,
        )
    )

    # ── 6. Scan for CC candidates on held shares ──────────────────────
    t0 = time.perf_counter()
    try:
        cc_candidates = await strategy_engine.scan_cc_candidates(
            broker=broker,
            positions=positions,
            top_n=top_n,
        )
        result.cc_candidates = cc_candidates
    except Exception as exc:
        result.errors.append(f"CC scan failed: {exc}")
        scanner_errors.add(1, {"stage": "cc_scan", "error_type": type(exc).__name__})
        logger.error("morning_scan_cc_failed", exc_info=True)
    cc_dur = time.perf_counter() - t0
    _record_stage("cc_scan", cc_dur)

    # ── 6b. Portfolio allocator (MILP optimizer) ──────────────────────
    allocator_csp_input = csp_pool if csp_pool else result.csp_candidates
    if portfolio_allocator and (allocator_csp_input or result.cc_candidates):
        t0 = time.perf_counter()
        try:
            held_shares: dict[str, int] = {}
            for pos in positions:
                if pos.option_symbol is None and pos.quantity >= 100:
                    held_shares[pos.symbol] = int(pos.quantity)

            conviction_data_for_alloc = {
                ticker: sig
                for ticker, sig in result.conviction_signals.items()
            }

            result.allocation = portfolio_allocator.optimize(
                csp_candidates=allocator_csp_input,
                cc_candidates=result.cc_candidates,
                available_capital=effective_buying_power,
                conviction_signals=conviction_data_for_alloc,
                held_shares=held_shares,
                market_caps=market_caps,
            )
            alloc_dur = time.perf_counter() - t0
            _record_stage("portfolio_allocation", alloc_dur)
            logger.info(
                "portfolio_allocation_complete",
                trades=result.allocation.positions_used,
                total_premium=result.allocation.total_premium,
                utilization=result.allocation.capital_utilization_pct,
                solver=result.allocation.solver_status,
                duration_ms=round(alloc_dur * 1000, 2),
            )
        except Exception:
            alloc_dur = time.perf_counter() - t0
            _record_stage("portfolio_allocation", alloc_dur)
            scanner_errors.add(1, {"stage": "portfolio_allocation", "error_type": "exception"})
            logger.warning("portfolio_allocation_failed", duration_ms=round(alloc_dur * 1000, 2), exc_info=True)

    # ── 7. LLM analysis — per-ticker, parallel with semaphore ─────────
    if analysis_agent and result.csp_candidates:
        t0 = time.perf_counter()
        ticker_candidates: dict[str, list[ScoredCandidate]] = {}
        for c in result.csp_candidates:
            ticker_candidates.setdefault(c.symbol, []).append(c)

        tickers_to_analyze = list(ticker_candidates.keys())
        logger.info(
            "llm_parallel_start",
            tickers=len(tickers_to_analyze),
            total_candidates=len(result.csp_candidates),
            concurrency=llm_concurrency,
        )

        semaphore = asyncio.Semaphore(llm_concurrency)

        async def _analyze_ticker(ticker: str) -> list[CSPAnalysis]:
            async with semaphore:
                cands = ticker_candidates[ticker]
                sig = result.conviction_signals.get(ticker)
                conviction_data = {ticker: sig.to_dict()} if sig else {}
                earnings_for_ticker = {
                    k: v for k, v in result.earnings_context.items() if k == ticker
                }
                ticker_t0 = time.perf_counter()
                try:
                    analyses = await analysis_agent.analyze_csp_candidates(
                        candidates=cands,
                        balance=balance,
                        positions=positions,
                        earnings_context=earnings_for_ticker,
                        conviction_signals=conviction_data or None,
                        effective_buying_power=effective_buying_power,
                    )
                    ticker_dur = time.perf_counter() - ticker_t0
                    llm_call_duration.record(ticker_dur, {"ticker": ticker, "model": "gemini"})
                    return analyses
                except Exception:
                    ticker_dur = time.perf_counter() - ticker_t0
                    llm_call_duration.record(ticker_dur, {"ticker": ticker, "model": "gemini"})
                    scanner_errors.add(1, {"stage": "llm_analysis", "error_type": "exception"})
                    logger.warning(
                        "llm_ticker_failed",
                        ticker=ticker,
                        duration_ms=round(ticker_dur * 1000, 2),
                        exc_info=True,
                    )
                    return []

        tasks = [_analyze_ticker(t) for t in tickers_to_analyze]
        all_analyses = await asyncio.gather(*tasks)

        for analyses in all_analyses:
            result.csp_analyses.extend(analyses)

        llm_dur = time.perf_counter() - t0
        _record_stage("llm_analysis", llm_dur)
        logger.info(
            "llm_parallel_complete",
            tickers_analyzed=len(tickers_to_analyze),
            analyses_returned=len(result.csp_analyses),
            duration_ms=round(llm_dur * 1000, 2),
        )

    # ── 7b. Persist conviction snapshots + detect transitions ──────────
    conviction_transitions: list = []
    if result.conviction_signals:
        t0 = time.perf_counter()
        try:
            from datetime import date as _date

            sigs = list(result.conviction_signals.values())
            as_of = sigs[0].as_of_date or _date.today() if sigs else _date.today()
            await _upsert_conviction_snapshots(sigs, as_of)
            conviction_transitions = await _detect_conviction_transitions(as_of)

            persist_dur = time.perf_counter() - t0
            _record_stage("conviction_persistence", persist_dur)
            result.pipeline_stages.append(
                PipelineStage(
                    "Conviction Persistence",
                    len(sigs),
                    len(sigs),
                    detail=(
                        f"{len(sigs)} snapshots persisted, "
                        f"{len(conviction_transitions)} transition(s) detected"
                    ),
                    duration_ms=persist_dur * 1000,
                )
            )
        except Exception:
            persist_dur = time.perf_counter() - t0
            _record_stage("conviction_persistence", persist_dur)
            scanner_errors.add(
                1, {"stage": "conviction_persistence", "error_type": "exception"}
            )
            logger.warning("conviction_persistence_failed", exc_info=True)

    # ── 8. Pullback detection + stock recommendations ──────────────────
    if result.conviction_signals:
        t0 = time.perf_counter()
        try:
            pullback_alerts = detect_pullback_alerts(
                result.conviction_signals,
                institutional_map=result.institutional_ownership,
                min_institutional_pct=min_institutional_pct_stock_buy,
            )
            result.pullback_alerts = pullback_alerts

            if pullback_alerts:
                result.stock_recommendations = generate_stock_recommendations(
                    alerts=pullback_alerts,
                    conviction_signals=result.conviction_signals,
                )

            pullback_dur = time.perf_counter() - t0
            _record_stage("pullback_detection", pullback_dur)
            result.pipeline_stages.append(
                PipelineStage(
                    "Pullback Detection",
                    len(result.conviction_signals),
                    len(pullback_alerts),
                    detail=f"{len(pullback_alerts)} pullback alert(s) detected",
                    duration_ms=pullback_dur * 1000,
                )
            )

            if pullback_alerts and notification_dispatcher:
                try:
                    await notification_dispatcher.dispatch_pullback_alerts(
                        pullback_alerts,
                        context={"scan_id": result.scan_id},
                    )
                except Exception:
                    logger.warning("pullback_notification_failed", exc_info=True)

        except Exception:
            pullback_dur = time.perf_counter() - t0
            _record_stage("pullback_detection", pullback_dur)
            scanner_errors.add(1, {"stage": "pullback_detection", "error_type": "exception"})
            logger.warning("pullback_detection_failed", exc_info=True)

    # ── 9. CSP expiry fallback check ──────────────────────────────────
    if result.pullback_alerts:
        t0 = time.perf_counter()
        try:
            from tyche.config import get_settings as _get_settings
            _settings = _get_settings()
            expiry_tracker = ExpiryTracker(db_dir=_settings.db_dir)
            fallbacks = expiry_tracker.generate_fallback_alerts(result.pullback_alerts)
            result.csp_fallback_alerts = fallbacks
            expiry_dur = time.perf_counter() - t0
            _record_stage("csp_expiry_fallback", expiry_dur)
            if fallbacks:
                result.pipeline_stages.append(
                    PipelineStage(
                        "CSP Expiry Fallback",
                        len(result.pullback_alerts),
                        len(fallbacks),
                        detail=f"{len(fallbacks)} fallback alert(s) for expired CSPs",
                        duration_ms=expiry_dur * 1000,
                    )
                )
        except Exception:
            expiry_dur = time.perf_counter() - t0
            _record_stage("csp_expiry_fallback", expiry_dur)
            logger.warning("csp_expiry_fallback_failed", exc_info=True)

    # ── Summary ───────────────────────────────────────────────────────
    total_dur = time.perf_counter() - scan_start
    result.total_duration_ms = total_dur * 1000
    scanner_total_duration.record(total_dur)

    logger.info(
        "morning_scan_complete",
        scan_id=result.scan_id,
        csp_candidates=len(result.csp_candidates),
        cc_candidates=len(result.cc_candidates),
        llm_analyses=len(result.csp_analyses),
        conviction_signals=len(result.conviction_signals),
        pullback_alerts=len(result.pullback_alerts),
        stock_recommendations=len(result.stock_recommendations),
        csp_fallback_alerts=len(result.csp_fallback_alerts),
        errors=len(result.errors),
        duration_ms=round(result.total_duration_ms, 2),
    )
    return result
