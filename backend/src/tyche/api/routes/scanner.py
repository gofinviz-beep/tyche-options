"""Scanner routes - trigger and retrieve morning scans, CSP/CC candidates."""

from __future__ import annotations

from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import (
    get_analysis_agent,
    get_broker,
    get_conviction_engine,
    get_data_store,
    get_earnings_client,
    get_settings,
    get_strategy_engine,
    get_ticker_meta_store,
    get_universe_builder,
)
from tyche.analysis.agent import AnalysisAgent
from tyche.broker.base import BrokerClient
from tyche.config import TycheSettings
from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.earnings import EarningsCalendarClient
from tyche.market_data.universe import UniverseBuilder
from tyche.persistence.database import get_session
from tyche.strategy.engine import StrategyEngine
from tyche.workflow.intent_builder import create_intents_from_scan
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
    conviction: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Trigger a full morning scan and persist intents from LLM analyses."""
    global _latest_scan

    if symbols is not None:
        watchlist = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if not watchlist:
            raise HTTPException(
                status_code=400,
                detail="Empty symbols parameter. Omit ?symbols= for dynamic discovery.",
            )
    else:
        watchlist = settings.watchlist_symbols

    result = await run_morning_scan(
        broker=broker,
        strategy_engine=strategy,
        analysis_agent=analysis,
        earnings_client=earnings,
        universe_builder=universe,
        watchlist=watchlist,
        conviction_engine=conviction,
        data_store=store,
        ticker_meta_store=meta_store,
        top_n=top_n,
        available_capital_override=settings.available_capital,
        min_institutional_pct=settings.min_institutional_pct,
        min_market_cap=settings.min_market_cap_millions * 1_000_000,
    )
    _latest_scan = result

    intents_created = 0
    if result.csp_analyses:
        try:
            async with get_session() as session:
                intents = await create_intents_from_scan(
                    session=session,
                    scan_id=result.scan_id,
                    csp_analyses=result.csp_analyses,
                    csp_candidates=result.csp_candidates,
                    conviction_signals=result.conviction_signals,
                )
                intents_created = len(intents)
        except Exception:
            logger.error("intent_creation_failed", exc_info=True)
            result.errors.append("Failed to persist trade intents")

    serialized = _serialize_scan_result(result)
    serialized["intents_created"] = intents_created
    return serialized


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
        "conviction_signals": {
            ticker: sig.to_dict()
            for ticker, sig in result.conviction_signals.items()
        },
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
        "institutional_ownership": {
            ticker: round(pct * 100, 1)
            for ticker, pct in result.institutional_ownership.items()
        },
        "errors": result.errors,
    }
