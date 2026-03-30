"""Scanner routes - trigger and retrieve morning scans, CSP/CC candidates."""

from __future__ import annotations

import asyncio
from typing import Any

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import (
    get_analysis_agent,
    get_broker,
    get_conviction_engine,
    get_data_store,
    get_earnings_client,
    get_portfolio_allocator,
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
from tyche.persistence.scan_repository import (
    cleanup_old_scans,
    load_history,
    load_latest,
    load_scan,
    save_scan,
)
from tyche.strategy.allocator import PortfolioAllocator
from tyche.strategy.engine import StrategyEngine
from tyche.workflow.intent_builder import create_intents_from_scan
from tyche.workflow.morning_scan import MorningScanResult, run_morning_scan

logger = structlog.get_logger()
router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.post("/scan", response_model=dict[str, Any])
async def trigger_scan(
    top_n: int = Query(default=10, ge=1, le=200),
    symbols: str | None = Query(default=None, description="Comma-separated symbols override"),
    broker: BrokerClient = Depends(get_broker),
    strategy: StrategyEngine = Depends(get_strategy_engine),
    analysis: AnalysisAgent | None = Depends(get_analysis_agent),
    earnings: EarningsCalendarClient | None = Depends(get_earnings_client),
    universe: UniverseBuilder = Depends(get_universe_builder),
    conviction: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    allocator: PortfolioAllocator = Depends(get_portfolio_allocator),
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Trigger a full morning scan, persist results, and create trade intents."""
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
        portfolio_allocator=allocator,
        top_n=top_n,
        available_capital_override=settings.available_capital,
        min_institutional_pct=settings.min_institutional_pct,
        min_market_cap=settings.min_market_cap_millions * 1_000_000,
        max_expiration_dates=settings.max_expiration_dates,
        expiration_mode=settings.expiration_mode,
        strike_range_pct=settings.strike_range_pct,
        llm_concurrency=settings.llm_concurrency,
    )

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

    config_snapshot = {
        "top_n": top_n,
        "strike_range_pct": settings.strike_range_pct,
        "max_expiration_dates": settings.max_expiration_dates,
        "expiration_mode": settings.expiration_mode,
        "llm_concurrency": settings.llm_concurrency,
        "min_market_cap_millions": settings.min_market_cap_millions,
        "min_institutional_pct": settings.min_institutional_pct,
    }

    try:
        await save_scan(
            result,
            intents_created=intents_created,
            trigger="manual",
            config_snapshot=config_snapshot,
        )
        asyncio.create_task(
            cleanup_old_scans(settings.scan_retention_count)
        )
    except Exception:
        logger.error("scan_persistence_failed", exc_info=True)

    serialized = _serialize_scan_result(result)
    serialized["intents_created"] = intents_created
    return serialized


@router.get("/latest")
async def get_latest_scan() -> dict[str, Any] | None:
    """Retrieve the most recent scan results from the database."""
    try:
        return await load_latest()
    except RuntimeError:
        return None


@router.get("/history")
async def get_scan_history(
    limit: int = Query(default=5, ge=1, le=20),
) -> list[dict[str, Any]]:
    """Return summary info for the last N scans."""
    try:
        return await load_history(limit=limit)
    except RuntimeError:
        return []


@router.get("/{scan_id}")
async def get_scan_by_id(scan_id: str) -> dict[str, Any]:
    """Load a specific scan by ID."""
    try:
        result = await load_scan(scan_id)
    except RuntimeError:
        raise HTTPException(status_code=503, detail="Scan database not initialized")
    if result is None:
        raise HTTPException(status_code=404, detail="Scan not found")
    return result


def _serialize_scan_result(result: MorningScanResult) -> dict[str, Any]:
    return {
        "scan_id": result.scan_id,
        "scanned_at": result.scanned_at.isoformat(),
        "symbols_scanned": result.symbols_scanned,
        "pipeline_stages": [s.to_dict() for s in result.pipeline_stages],
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
        "allocation": result.allocation.summary if result.allocation else None,
        "allocated_trades": [
            {
                "symbol": t.symbol,
                "option_type": t.option_type,
                "strike": t.strike,
                "expiration": t.expiration.isoformat(),
                "dte": t.dte,
                "contracts": t.contracts,
                "bid": t.bid,
                "total_premium": t.total_premium,
                "collateral": t.collateral,
                "annualized_return_pct": t.annualized_return_pct,
                "conviction": t.conviction,
                "extension_pct": t.extension_pct,
                "strategy": t.strategy,
            }
            for t in (result.allocation.trades if result.allocation else [])
        ],
        "errors": result.errors,
    }
