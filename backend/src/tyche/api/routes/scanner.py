"""Scanner routes — trigger and retrieve morning scans, CSP/CC candidates."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import (
    get_analysis_agent,
    get_broker,
    get_earnings_client,
    get_settings,
    get_strategy_engine,
    get_universe_builder,
)
from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient
from tyche.config import TycheSettings
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.universe import UniverseBuilder
from tyche.strategy.engine import StrategyEngine
from tyche.workflow.morning_scan import MorningScanResult, run_morning_scan

logger = structlog.get_logger()
router = APIRouter(prefix="/scanner", tags=["scanner"])

_latest_scan: MorningScanResult | None = None


@router.post("/scan", response_model=dict[str, Any])
async def trigger_scan(
    top_n: int = Query(default=5, ge=1, le=20),
    symbols: str | None = Query(default=None, description="Comma-separated symbols override"),
    broker: BrokerClient = Depends(get_broker),
    strategy: StrategyEngine = Depends(get_strategy_engine),
    analysis: AnalysisAgent | None = Depends(get_analysis_agent),
    earnings: EarningsCalendarClient | None = Depends(get_earnings_client),
    universe: UniverseBuilder = Depends(get_universe_builder),
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Trigger a full morning scan (can be run on-demand)."""
    global _latest_scan

    watchlist = (
        [s.strip().upper() for s in symbols.split(",")]
        if symbols
        else settings.watchlist_symbols
    )

    if not watchlist:
        raise HTTPException(
            status_code=400,
            detail="No watchlist symbols configured. Set TYCHE_WATCHLIST_SYMBOLS or pass ?symbols=",
        )

    result = await run_morning_scan(
        broker=broker,
        strategy_engine=strategy,
        analysis_agent=analysis,
        earnings_client=earnings,
        universe_builder=universe,
        watchlist=watchlist,
        top_n=top_n,
    )
    _latest_scan = result

    return _serialize_scan_result(result)


@router.get("/latest", response_model=dict[str, Any])
async def get_latest_scan() -> dict[str, Any]:
    """Retrieve the most recent scan results."""
    if _latest_scan is None:
        raise HTTPException(status_code=404, detail="No scan has been run yet")
    return _serialize_scan_result(_latest_scan)


def _serialize_scan_result(result: MorningScanResult) -> dict[str, Any]:
    return {
        "scan_id": result.scan_id,
        "scanned_at": result.scanned_at.isoformat(),
        "symbols_scanned": result.symbols_scanned,
        "csp_candidates": [
            {
                "symbol": c.symbol,
                "option_symbol": c.option_symbol,
                "strike": c.strike,
                "expiration": c.expiration.isoformat(),
                "dte": c.dte,
                "bid": c.bid,
                "ask": c.ask,
                "premium_per_contract": c.premium_per_contract,
                "collateral_required": c.collateral_required,
                "annualized_return_pct": c.annualized_return_pct,
                "score": c.score,
                "delta": c.delta,
                "theta": c.theta,
                "implied_volatility": c.implied_volatility,
                "volume": c.volume,
                "open_interest": c.open_interest,
                "earnings_within_dte": c.earnings_within_dte,
                "earnings_date": c.earnings_date.isoformat() if c.earnings_date else None,
            }
            for c in result.csp_candidates
        ],
        "cc_candidates": [
            {
                "symbol": c.symbol,
                "option_symbol": c.option_symbol,
                "strike": c.strike,
                "expiration": c.expiration.isoformat(),
                "dte": c.dte,
                "bid": c.bid,
                "ask": c.ask,
                "premium_per_contract": c.premium_per_contract,
                "annualized_return_pct": c.annualized_return_pct,
                "score": c.score,
            }
            for c in result.cc_candidates
        ],
        "llm_analyses": [a.model_dump() for a in result.csp_analyses],
        "earnings_context": {
            k: {**v, "earnings_date": str(v.get("earnings_date", ""))}
            for k, v in result.earnings_context.items()
        },
        "errors": result.errors,
    }
