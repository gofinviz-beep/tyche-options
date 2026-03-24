"""Morning scan workflow — the primary daily CSP + CC screening pipeline."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

import structlog

from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.universe import UniverseBuilder
from tyche.risk.engine import RiskEngine
from tyche.schemas.analysis import CSPAnalysis
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
        self.earnings_context: dict[str, Any] = {}
        self.errors: list[str] = []


async def run_morning_scan(
    broker: BrokerClient,
    strategy_engine: StrategyEngine,
    analysis_agent: AnalysisAgent | None,
    earnings_client: EarningsCalendarClient | None,
    universe_builder: UniverseBuilder,
    watchlist: list[str],
    top_n: int = 5,
) -> MorningScanResult:
    """Execute the full morning scan pipeline.

    Steps:
    1. Load account state (balances, positions, open orders)
    2. Screen watchlist through fundamental gates
    3. Fetch earnings dates
    4. Scan for CSP candidates (deterministic)
    5. Scan for CC candidates on held shares
    6. Send top candidates to LLM for analysis (if available)
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

    logger.info(
        "morning_scan_account_loaded",
        cash=balance.cash,
        buying_power=balance.buying_power,
        positions=len(positions),
        open_orders=len(open_orders),
    )

    # 2. Screen watchlist
    screened = await universe_builder.screen(broker, watchlist)
    screened_symbols = [s.symbol for s in screened]
    result.symbols_scanned = len(screened_symbols)

    if not screened_symbols:
        result.errors.append("No symbols passed fundamental screening")
        return result

    # 3. Fetch earnings dates
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

    # 4. Scan for CSP candidates
    try:
        csp_candidates = await strategy_engine.scan_csp_candidates(
            broker=broker,
            watchlist=screened_symbols,
            available_cash=balance.buying_power,
            earnings_dates=earnings_dates,
            top_n=top_n,
        )
        result.csp_candidates = csp_candidates
    except Exception as exc:
        result.errors.append(f"CSP scan failed: {exc}")
        logger.error("morning_scan_csp_failed", exc_info=True)

    # 5. Scan for CC candidates on held shares
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

    # 6. LLM analysis
    if analysis_agent and result.csp_candidates:
        try:
            analyses = await analysis_agent.analyze_csp_candidates(
                candidates=result.csp_candidates,
                balance=balance,
                positions=positions,
                earnings_context=result.earnings_context,
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
        errors=len(result.errors),
    )
    return result
