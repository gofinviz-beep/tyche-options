"""API routes for the Covered Call Recommender."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query

from tyche.api.deps import get_broker, get_data_store, get_derived_store
from tyche.broker.base import BrokerClient
from tyche.config import TycheSettings, get_settings
from tyche.market_data.data_store import OHLCVStore
from tyche.market_data.derived_store import DerivedMetricsStore
from tyche.schemas.cc_schemas import (
    CCBatchRequest,
    CCDeepDiveResponse,
    CCPortfolioResponse,
    CCSignalResponse,
)

logger = structlog.get_logger()

router = APIRouter(prefix="/covered-calls", tags=["covered-calls"])


def _get_engine(settings: TycheSettings):
    """Lazily build the CCAnalysisEngine with available stores."""
    from tyche.analysis.cc_analyzer import CCAnalysisEngine
    from tyche.market_data.options_history_store import OptionsHistoryStore

    ohlcv = get_data_store(settings)
    derived = get_derived_store(settings)

    try:
        opts_hist = OptionsHistoryStore(data_dir=settings.data_dir)
    except Exception:
        opts_hist = None

    return CCAnalysisEngine(
        ohlcv_store=ohlcv,
        derived_store=derived,
        options_history_store=opts_hist,
    )


def _is_live_broker(broker: BrokerClient) -> bool:
    """Check if the broker is a real Tradier client (not mock)."""
    return type(broker).__name__ != "MockBroker"


def _signal_to_response(sig) -> CCSignalResponse:
    return CCSignalResponse(
        ticker=sig.ticker,
        signal=sig.signal,
        signal_reason=sig.signal_reason,
        last_close=sig.last_close,
        ema_8=sig.ema_8,
        ema_21=sig.ema_21,
        ema_50=sig.ema_50,
        extension_pct_8=sig.extension_pct_8,
        extension_pct_21=sig.extension_pct_21,
        rsi_14=sig.rsi_14,
        iv_rank=sig.iv_rank,
        vrp=sig.vrp,
        rv_20d=sig.rv_20d,
        suggested_strike=sig.suggested_strike,
        suggested_otm_pct=sig.suggested_otm_pct,
        suggested_expiry_dte=sig.suggested_expiry_dte,
        suggested_premium_est=sig.suggested_premium_est,
        optimal_entry_day=sig.optimal_entry_day,
        assignment_prob_1w=sig.assignment_prob_1w,
        assignment_prob_2w=sig.assignment_prob_2w,
        estimated_next_earnings=sig.estimated_next_earnings,
        earnings_in_window=sig.earnings_in_window,
    )


def _deep_dive_to_response(dd) -> CCDeepDiveResponse:
    return CCDeepDiveResponse(
        signal=_signal_to_response(dd.signal),
        total_episodes=dd.total_episodes,
        episode_table=dd.episode_table,
        days_to_8ema=dd.days_to_8ema,
        days_to_21ema=dd.days_to_21ema,
        days_to_50ema=dd.days_to_50ema,
        drawdown_at_8ema=dd.drawdown_at_8ema,
        drawdown_at_21ema=dd.drawdown_at_21ema,
        forward_returns=dd.forward_returns,
        dow_analysis=dd.dow_analysis,
        rally_peak_day_distribution=dd.rally_peak_day_distribution,
        call_candidates=dd.call_candidates,
        pnl_scenarios=dd.pnl_scenarios,
        recommended_action=dd.recommended_action,
    )


@router.post("/analyze", response_model=CCPortfolioResponse)
async def analyze_batch(
    body: CCBatchRequest,
    settings: TycheSettings = Depends(get_settings),
    broker: BrokerClient = Depends(get_broker),
) -> CCPortfolioResponse:
    """Analyze multiple positions for CC opportunities.

    When a live Tradier broker is configured, fetches real-time bid/ask
    for the recommended strike/expiration.  Falls back to historical
    estimates otherwise.
    """
    engine = _get_engine(settings)

    positions = [
        {
            "ticker": p.ticker.upper(),
            "shares": p.shares,
            "cost_basis": p.cost_basis,
        }
        for p in body.positions
    ]

    logger.info(
        "cc_analyze_batch",
        tickers=[p["ticker"] for p in positions],
        target_dte=body.target_dte,
        live_broker=_is_live_broker(broker),
    )

    live = broker if _is_live_broker(broker) else None
    result = await engine.analyze_batch_with_live_chain(
        positions, target_dte=body.target_dte, broker=live,
    )

    return CCPortfolioResponse(
        analyses=[_deep_dive_to_response(a) for a in result.analyses],
        portfolio_summary=result.portfolio_summary,
    )


@router.get("/analyze/{ticker}", response_model=CCDeepDiveResponse)
async def analyze_ticker(
    ticker: str,
    shares: int = Query(default=100, ge=1),
    cost_basis: float = Query(default=0.0, ge=0),
    target_dte: int = Query(default=8, ge=1, le=60),
    settings: TycheSettings = Depends(get_settings),
    broker: BrokerClient = Depends(get_broker),
) -> CCDeepDiveResponse:
    """Analyze a single ticker for CC opportunities."""
    engine = _get_engine(settings)

    logger.info(
        "cc_analyze_single",
        ticker=ticker.upper(),
        shares=shares,
        target_dte=target_dte,
        live_broker=_is_live_broker(broker),
    )

    live = broker if _is_live_broker(broker) else None
    result = await engine.analyze_with_live_chain(
        ticker=ticker.upper(),
        shares=shares,
        cost_basis=cost_basis,
        target_dte=target_dte,
        broker=live,
    )

    return _deep_dive_to_response(result)
