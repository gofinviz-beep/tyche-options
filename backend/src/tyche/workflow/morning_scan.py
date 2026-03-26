"""Morning scan workflow — the primary daily CSP + CC screening pipeline.

Enhanced with conviction engine (8/21 EMA) and order intent generation.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.institutional import filter_by_institutional_ownership
from tyche.market_data.universe import UniverseBuilder
from tyche.risk.engine import RiskEngine
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.allocator import AllocatedTrade, AllocationResult, PortfolioAllocator
from tyche.strategy.engine import StrategyEngine
from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()


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
    result = MorningScanResult()

    # 1. Load account state
    try:
        balance = await broker.get_account_balances()
        positions = await broker.get_positions()
        open_orders = await broker.get_open_orders()
    except Exception as exc:
        result.errors.append(f"Failed to load account state: {exc}")
        logger.error("morning_scan_account_failed", exc_info=True)
        return result

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
    )

    # 2. Build the screening universe
    if watchlist:
        screened = universe_builder.screen_watchlist(watchlist)
        screened_symbols = [s.symbol for s in screened]
        logger.info("scan_using_watchlist", symbols=len(screened_symbols))
    elif data_store and data_store.exists:
        screened_symbols = data_store.screen_universe(
            min_avg_volume=universe_builder._min_vol,
            min_price=universe_builder._min_price,
        )
        logger.info("scan_using_dynamic_universe", symbols=len(screened_symbols))
    else:
        result.errors.append(
            "No watchlist configured and data store not bootstrapped. "
            "Either set TYCHE_WATCHLIST_SYMBOLS or bootstrap the data store first."
        )
        return result

    result.symbols_scanned = len(screened_symbols)

    if not screened_symbols:
        result.errors.append("No symbols passed fundamental screening")
        return result

    # 2b. Filter by market cap using persisted ticker metadata
    if ticker_meta_store and ticker_meta_store.exists and min_market_cap > 0:
        market_caps = ticker_meta_store.get_market_caps(screened_symbols)
        before = len(screened_symbols)
        screened_symbols = [
            sym for sym in screened_symbols
            if market_caps.get(sym, 0) >= min_market_cap
        ]
        logger.info(
            "market_cap_filter_applied",
            before=before,
            after=len(screened_symbols),
            min_market_cap=min_market_cap,
        )

    if not screened_symbols:
        result.errors.append("No symbols passed market cap screening")
        return result

    # 3. Run conviction engine on screened symbols
    if conviction_engine and data_store and data_store.exists:
        try:
            ticker_data = data_store.read_tickers(screened_symbols)
            if ticker_data:
                signals = conviction_engine.analyze_batch(ticker_data)
                for sig in signals:
                    result.conviction_signals[sig.ticker] = sig

                eligible_symbols = [
                    sig.ticker for sig in signals if sig.csp_eligible
                ]
                if eligible_symbols:
                    screened_symbols = eligible_symbols
                    logger.info(
                        "conviction_filter_applied",
                        total=len(signals),
                        eligible=len(eligible_symbols),
                    )
                else:
                    logger.warning("conviction_no_eligible", total=len(signals))
        except Exception:
            logger.warning("morning_scan_conviction_failed", exc_info=True)

    # 3b. Filter by institutional ownership (only on conviction survivors)
    if min_institutional_pct > 0 and len(screened_symbols) <= 100:
        try:
            screened_symbols, inst_map = await filter_by_institutional_ownership(
                screened_symbols, min_pct=min_institutional_pct
            )
            result.institutional_ownership = inst_map
            logger.info(
                "institutional_filter_applied",
                passed=len(screened_symbols),
                ownership_data=len(inst_map),
            )
        except Exception:
            logger.warning("institutional_filter_failed", exc_info=True)

    # 4. Fetch earnings dates
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
            logger.warning("morning_scan_earnings_failed", exc_info=True)

    # 5. Scan for CSP candidates
    try:
        csp_candidates = await strategy_engine.scan_csp_candidates(
            broker=broker,
            watchlist=screened_symbols,
            available_cash=effective_buying_power,
            earnings_dates=earnings_dates,
            top_n=top_n,
        )
        result.csp_candidates = csp_candidates
    except Exception as exc:
        result.errors.append(f"CSP scan failed: {exc}")
        logger.error("morning_scan_csp_failed", exc_info=True)

    # 6. Scan for CC candidates on held shares
    try:
        cc_candidates = await strategy_engine.scan_cc_candidates(
            broker=broker,
            positions=positions,
            top_n=top_n,
        )
        result.cc_candidates = cc_candidates
    except Exception as exc:
        result.errors.append(f"CC scan failed: {exc}")
        logger.error("morning_scan_cc_failed", exc_info=True)

    # 6b. Run portfolio allocator (MILP optimizer) on combined candidates
    if portfolio_allocator and (result.csp_candidates or result.cc_candidates):
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
                csp_candidates=result.csp_candidates,
                cc_candidates=result.cc_candidates,
                available_capital=effective_buying_power,
                conviction_signals=conviction_data_for_alloc,
                held_shares=held_shares,
            )
            logger.info(
                "portfolio_allocation_complete",
                trades=result.allocation.positions_used,
                total_premium=result.allocation.total_premium,
                utilization=result.allocation.capital_utilization_pct,
                solver=result.allocation.solver_status,
            )
        except Exception:
            logger.warning("portfolio_allocation_failed", exc_info=True)

    # 7. LLM analysis with conviction context
    if analysis_agent and result.csp_candidates:
        conviction_data = {
            ticker: sig.to_dict()
            for ticker, sig in result.conviction_signals.items()
        }
        try:
            analyses = await analysis_agent.analyze_csp_candidates(
                candidates=result.csp_candidates,
                balance=balance,
                positions=positions,
                earnings_context=result.earnings_context,
                conviction_signals=conviction_data or None,
                effective_buying_power=effective_buying_power,
            )
            result.csp_analyses = analyses
        except Exception as exc:
            result.errors.append(f"LLM analysis failed: {exc}")
            logger.error("morning_scan_llm_failed", exc_info=True)

    logger.info(
        "morning_scan_complete",
        scan_id=result.scan_id,
        csp_candidates=len(result.csp_candidates),
        cc_candidates=len(result.cc_candidates),
        llm_analyses=len(result.csp_analyses),
        conviction_signals=len(result.conviction_signals),
        errors=len(result.errors),
    )
    return result
