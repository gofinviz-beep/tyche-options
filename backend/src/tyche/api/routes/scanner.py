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
    get_economic_calendar,
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
from tyche.persistence.scan_repository import (
    cleanup_old_scans,
    load_history,
    load_latest,
    load_scan,
    save_scan,
)
from tyche.strategy.allocator import PortfolioAllocator
from tyche.strategy.engine import StrategyEngine
from tyche.workflow.morning_scan import MorningScanResult, run_morning_scan

logger = structlog.get_logger()
router = APIRouter(prefix="/scanner", tags=["scanner"])


@router.post("/cache/clear")
async def clear_broker_cache(
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, Any]:
    """Clear the broker's market data cache (quotes, expirations, chains).

    Use this to force a hard refresh of all Tradier data on the next scan.
    """
    if hasattr(broker, "clear_cache"):
        stats = broker.clear_cache()
        return {"cleared": True, **stats}
    return {"cleared": False, "detail": "Broker does not support caching"}


@router.get("/cache/stats")
async def get_broker_cache_stats(
    broker: BrokerClient = Depends(get_broker),
) -> dict[str, Any]:
    """Return current broker cache statistics."""
    if hasattr(broker, "cache_stats"):
        return {"cached": True, **broker.cache_stats}
    return {"cached": False}


@router.post("/explore", response_model=dict[str, Any])
async def explore_options(
    symbols: str = Query(description="Comma-separated tickers to explore"),
    available_capital: float | None = Query(default=None, description="Override available capital"),
    broker: BrokerClient = Depends(get_broker),
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Lightweight options explorer — bypasses the full Scanner pipeline.

    Fetches quotes + option chains for the given tickers with minimal
    filtering (OTM puts with bid > 0, open interest >= 1).  Uses the
    broker TTL cache so repeated calls are near-instant.
    """
    import time as _time
    from datetime import date as _date

    from tyche.broker.base import Quote as _Quote
    from tyche.strategy.engine import target_expiration_dates
    from tyche.strategy.strategies.cash_secured_put import CashSecuredPutStrategy

    t0 = _time.perf_counter()
    tickers = [s.strip().upper() for s in symbols.split(",") if s.strip()]
    if not tickers:
        raise HTTPException(status_code=400, detail="No valid symbols provided.")

    capital = available_capital or settings.available_capital
    csp = CashSecuredPutStrategy(dte_min=1, dte_max=45)

    results: list[dict[str, Any]] = []
    errors: list[str] = []
    symbols_with_options = 0
    target_exp: str | None = None

    for symbol in tickers:
        try:
            quote = await broker.get_quote(symbol)
            if quote.last <= 0:
                fallback = quote.close if quote.close > 0 else quote.bid
                if fallback > 0:
                    quote = _Quote(
                        symbol=quote.symbol, last=fallback,
                        bid=quote.bid, ask=quote.ask, high=quote.high,
                        low=quote.low, open=quote.open, close=quote.close,
                        volume=quote.volume, change=quote.change,
                        change_pct=quote.change_pct,
                    )

            expirations = await broker.get_options_expirations(symbol)
            target_exps = target_expiration_dates(expirations, max_expirations=1)
            if not target_exps:
                errors.append(f"{symbol}: no valid expiration")
                continue

            exp_str = target_exps[0]
            if target_exp is None:
                target_exp = exp_str

            chain = await broker.get_options_chain(symbol, exp_str)
            if chain.underlying_price == 0:
                chain.underlying_price = quote.last

            raw = csp.identify_candidates(chain, quote, strike_floor=0.0)
            if not raw:
                continue

            filtered = csp.apply_filters(
                raw, min_oi=1, min_volume=0, max_spread_pct=50.0,
                min_bid=0.0, min_premium_pct=0.0,
            )
            scored = csp.score(filtered, capital)

            if scored:
                symbols_with_options += 1
                for sc in scored:
                    collateral_per = sc.strike * 100
                    max_contracts = int(capital // collateral_per) if collateral_per > 0 else 0
                    results.append({
                        "symbol": sc.symbol,
                        "option_symbol": sc.option_symbol,
                        "strike": sc.strike,
                        "expiration": sc.expiration.isoformat(),
                        "dte": sc.dte,
                        "bid": sc.bid,
                        "ask": sc.ask,
                        "mid": sc.mid,
                        "volume": sc.volume,
                        "open_interest": sc.open_interest,
                        "implied_volatility": round(sc.implied_volatility, 4),
                        "delta": sc.delta,
                        "theta": sc.theta,
                        "underlying_price": sc.underlying_price,
                        "premium_per_contract": sc.premium_per_contract,
                        "collateral": collateral_per,
                        "max_contracts": max_contracts,
                        "total_premium": sc.premium_per_contract * max_contracts,
                        "annualized_return_pct": sc.annualized_return_pct,
                        "score": sc.score,
                    })
        except Exception as exc:
            errors.append(f"{symbol}: {exc}")
            logger.warning("explore_symbol_failed", symbol=symbol, exc_info=True)

    results.sort(key=lambda r: r["annualized_return_pct"], reverse=True)

    cache_stats = broker.cache_stats if hasattr(broker, "cache_stats") else {}
    dur_ms = (_time.perf_counter() - t0) * 1000

    logger.info(
        "explore_complete",
        symbols=len(tickers),
        with_options=symbols_with_options,
        contracts=len(results),
        duration_ms=round(dur_ms, 2),
    )

    return {
        "symbols_requested": len(tickers),
        "symbols_with_options": symbols_with_options,
        "expiration": target_exp,
        "total_contracts": len(results),
        "available_capital": capital,
        "duration_ms": round(dur_ms, 2),
        "broker_cache": cache_stats,
        "errors": errors,
        "candidates": results,
    }


@router.post("/scan", response_model=dict[str, Any])
async def trigger_scan(
    top_n: int = Query(default=10, ge=1, le=200),
    symbols: str | None = Query(default=None, description="Comma-separated symbols override"),
    force_refresh: bool = Query(default=False, description="Clear broker cache before scanning"),
    enable_llm: bool | None = Query(default=None, description="Override LLM analysis (null = use config)"),
    target_expiration: str | None = Query(default=None, description="Target expiration date (YYYY-MM-DD). Bypasses min_scan_dte and target_dte_sweet_spot."),
    available_capital: float | None = Query(default=None, gt=0, description="Capital available for CSP collateral (defaults to settings)"),
    broker: BrokerClient = Depends(get_broker),
    strategy: StrategyEngine = Depends(get_strategy_engine),
    analysis: AnalysisAgent | None = Depends(get_analysis_agent),
    earnings: EarningsCalendarClient | None = Depends(get_earnings_client),
    universe: UniverseBuilder = Depends(get_universe_builder),
    conviction: ConvictionEngine = Depends(get_conviction_engine),
    store: OHLCVStore = Depends(get_data_store),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    allocator: PortfolioAllocator = Depends(get_portfolio_allocator),
    econ_calendar: Any = Depends(get_economic_calendar),
    settings: TycheSettings = Depends(get_settings),
) -> dict[str, Any]:
    """Trigger a full morning scan, persist results, and create trade intents.

    Broker API responses (quotes, expirations, chains) are cached for
    ``broker_cache_ttl`` seconds (default 5 min). Re-running within that
    window reuses cached data — only conviction + allocator + LLM re-run.

    Pass ``force_refresh=true`` to clear the cache before scanning.
    """
    if target_expiration is not None:
        from datetime import date as _date
        try:
            exp_date = _date.fromisoformat(target_expiration)
            if exp_date < _date.today():
                raise HTTPException(status_code=400, detail="target_expiration must be today or a future date")
        except ValueError:
            raise HTTPException(status_code=400, detail="target_expiration must be YYYY-MM-DD format")

    if force_refresh and hasattr(broker, "clear_cache"):
        broker.clear_cache()
    if symbols is not None:
        watchlist = [s.strip().upper() for s in symbols.split(",") if s.strip()]
        if not watchlist:
            raise HTTPException(
                status_code=400,
                detail="Empty symbols parameter. Omit ?symbols= for dynamic discovery.",
            )
    else:
        # Full-universe scan when no explicit symbols — settings watchlist is
        # for UI highlighting only (conviction star, stocks dashboard panel).
        watchlist = []

    llm_active = enable_llm if enable_llm is not None else settings.scanner_llm_enabled
    effective_analysis = analysis if llm_active else None
    effective_capital = available_capital if available_capital is not None else settings.available_capital

    notification_dispatcher = None
    if settings.notification_pullback_alert_enabled:
        from tyche.notification.dispatcher import NotificationDispatcher
        notification_dispatcher = NotificationDispatcher.from_settings(settings)

    result = await run_morning_scan(
        broker=broker,
        strategy_engine=strategy,
        analysis_agent=effective_analysis,
        earnings_client=earnings,
        universe_builder=universe,
        watchlist=watchlist,
        conviction_engine=conviction,
        data_store=store,
        ticker_meta_store=meta_store,
        portfolio_allocator=allocator,
        top_n=top_n,
        available_capital_override=effective_capital,
        min_institutional_pct=settings.min_institutional_pct,
        min_market_cap=settings.min_market_cap_millions * 1_000_000,
        max_expiration_dates=settings.max_expiration_dates,
        expiration_mode=settings.expiration_mode,
        strike_range_pct=settings.strike_range_pct,
        llm_concurrency=settings.llm_concurrency,
        csp_strike_preference=settings.csp_strike_preference,
        pullback_strike_offset_pct=settings.pullback_strike_offset_pct,
        pullback_strike_ceiling_pct=settings.pullback_strike_ceiling_pct,
        earliest_expiration_only=settings.earliest_expiration_only,
        min_scan_dte=settings.min_scan_dte,
        target_dte_sweet_spot=settings.target_dte_sweet_spot,
        csp_min_bid=settings.csp_min_bid,
        csp_min_premium_pct=settings.csp_min_premium_pct,
        csp_min_volume=settings.csp_min_volume,
        csp_min_oi=settings.csp_min_oi,
        min_institutional_pct_stock_buy=settings.min_institutional_pct_stock_buy,
        notification_dispatcher=notification_dispatcher,
        economic_calendar=econ_calendar,
        target_expiration=target_expiration,
    )

    config_snapshot = {
        "top_n": top_n,
        "strike_range_pct": settings.strike_range_pct,
        "max_expiration_dates": settings.max_expiration_dates,
        "expiration_mode": settings.expiration_mode,
        "llm_concurrency": settings.llm_concurrency,
        "min_market_cap_millions": settings.min_market_cap_millions,
        "min_institutional_pct": settings.min_institutional_pct,
        "earliest_expiration_only": settings.earliest_expiration_only,
        "min_scan_dte": settings.min_scan_dte,
        "target_dte_sweet_spot": settings.target_dte_sweet_spot,
        "csp_min_bid": settings.csp_min_bid,
        "csp_min_premium_pct": settings.csp_min_premium_pct,
        "csp_min_volume": settings.csp_min_volume,
        "csp_min_oi": settings.csp_min_oi,
        "llm_enabled": llm_active,
        "target_expiration": target_expiration,
        "available_capital": effective_capital,
    }

    try:
        await save_scan(
            result,
            trigger="manual",
            config_snapshot=config_snapshot,
        )
        asyncio.create_task(
            cleanup_old_scans(settings.scan_retention_count)
        )
    except Exception:
        logger.error("scan_persistence_failed", exc_info=True)

    serialized = _serialize_scan_result(result)
    if hasattr(broker, "cache_stats"):
        serialized["broker_cache"] = broker.cache_stats
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
                "mid": c.mid,
                "bid_ask_spread_pct": c.bid_ask_spread_pct,
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
        "total_duration_ms": round(result.total_duration_ms, 2),
        "pullback_alerts": [a.to_dict() for a in result.pullback_alerts],
        "stock_recommendations": [r.to_dict() for r in result.stock_recommendations],
        "csp_fallback_alerts": [f.to_dict() for f in result.csp_fallback_alerts],
    }
