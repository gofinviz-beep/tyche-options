"""Directional Alpha engine routes.

Primary read path: the pre-computed snapshot from ``alpha_signals.parquet``
(written by the nightly alpha batch). ``POST /alpha/recompute`` triggers a
fresh batch in the background. ``GET /alpha/signal/{ticker}`` computes a
single name on demand.

Fully additive — no existing route or behavior is modified.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query

from tyche.api.deps import (
    get_alpha_engine,
    get_breakout_predictor,
    get_settings,
    get_ticker_meta_store,
)
from tyche.config import TycheSettings
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.market_data.data_store import TickerMetaStore
from tyche.schemas.alpha import (
    AlphaBatchResponse,
    AlphaDemandDimensions,
    AlphaFactorScores,
    AlphaScanResponse,
    AlphaSignalResponse,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/alpha", tags=["alpha"])

_VALID_SIGNALS = {"strong_buy", "buy", "watch", "avoid"}
_VALID_VARIANTS = {"peak", "sustained"}


@router.get("/scan", response_model=AlphaScanResponse)
async def scan_alpha(
    signal: str | None = Query(default=None, description="Filter: strong_buy|buy|watch|avoid"),
    horizon: str | None = Query(default=None, description="Filter: swing|trend|thematic"),
    min_score: float = Query(default=0.0, ge=0.0, le=100.0),
    min_market_cap_millions: float | None = Query(
        default=None,
        ge=0.0,
        description="Min market cap ($M) floor. Defaults to alpha_min_market_cap_millions config.",
    ),
    variant: str = Query(
        default="sustained",
        description="Model variant: 'sustained' (held-to-horizon, default) or 'peak' (legacy). Falls back to peak when the sustained snapshot is absent.",
    ),
    limit: int = Query(default=200, ge=1, le=1000),
    settings: TycheSettings = Depends(get_settings),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
) -> AlphaScanResponse:
    """Return ranked directional alpha signals from the latest batch snapshot."""
    requested = variant if variant in _VALID_VARIANTS else "peak"
    store = AlphaSignalStore(data_dir=settings.data_dir, variant=requested)
    # Graceful fallback: if the requested variant's snapshot hasn't been
    # produced yet (e.g. sustained disabled or never run), serve peak so the
    # page is never blank, and report which variant is actually shown.
    if not store.exists and requested != "peak":
        store = AlphaSignalStore(data_dir=settings.data_dir, variant="peak")
    served_variant = store.variant
    records, as_of, computed_at = store.read_latest()

    if not records:
        return AlphaScanResponse(
            scanned_at=datetime.now(timezone.utc).isoformat(),
            ml_available=False,
            variant=served_variant,
            total=0,
            signals=[],
        )

    # Universe guard: common-stock only (drops warrants/units/ADRs/ETFs) with a
    # known market cap at or above the alpha floor. Applied at read time so the
    # current snapshot is cleaned immediately and stays clean on every load.
    floor_millions = (
        min_market_cap_millions
        if min_market_cap_millions is not None
        else settings.alpha_min_market_cap_millions
    )
    if meta_store.exists and records:
        all_tickers = [r["ticker"] for r in records]
        eligible = set(meta_store.filter_equity_only(all_tickers))
        caps = meta_store.get_market_caps(all_tickers)
        floor = floor_millions * 1_000_000
        records = [
            r for r in records
            if r["ticker"] in eligible and (caps.get(r["ticker"]) or 0) >= floor
        ]

    if signal and signal in _VALID_SIGNALS:
        records = [r for r in records if r.get("signal") == signal]
    if horizon:
        records = [r for r in records if r.get("horizon") == horizon]
    if min_score > 0:
        records = [r for r in records if (r.get("alpha_score") or 0) >= min_score]

    records.sort(key=lambda r: r.get("alpha_score") or 0, reverse=True)

    strong_buy = sum(1 for r in records if r.get("signal") == "strong_buy")
    buy = sum(1 for r in records if r.get("signal") == "buy")

    display = records[:limit]
    tickers = [r["ticker"] for r in display]
    market_caps = meta_store.get_market_caps(tickers) if meta_store.exists else {}
    sectors = meta_store.get_sectors(tickers) if meta_store.exists else {}
    inst_pcts = meta_store.get_institutional_pcts(tickers) if meta_store.exists else {}
    watchlist = frozenset(s.upper() for s in (settings.watchlist_symbols or []))

    ml_available = any(
        r.get("breakout_prob_swing") is not None for r in display
    )

    signals = [
        _record_to_response(
            r,
            market_cap=market_caps.get(r["ticker"]),
            institutional_pct=inst_pcts.get(r["ticker"]),
            sector=sectors.get(r["ticker"]),
            is_watchlist=r["ticker"] in watchlist,
        )
        for r in display
    ]

    return AlphaScanResponse(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        as_of_date=as_of,
        computed_at=computed_at,
        ml_available=ml_available,
        variant=served_variant,
        total=len(records),
        strong_buy_count=strong_buy,
        buy_count=buy,
        signals=signals,
    )


@router.get("/signal/{ticker}", response_model=AlphaSignalResponse)
async def get_ticker_alpha(
    ticker: str,
    settings: TycheSettings = Depends(get_settings),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
    engine=Depends(get_alpha_engine),
    predictor=Depends(get_breakout_predictor),
) -> AlphaSignalResponse:
    """Compute the directional alpha signal for a single ticker on demand."""
    from tyche.ml.dataset import build_latest_features

    t = ticker.upper()
    features = build_latest_features(data_dir=settings.data_dir, tickers=[t])
    if features.empty:
        raise HTTPException(status_code=404, detail=f"No data found for {t}")

    probs = predictor.predict_proba_batch(features) if predictor is not None else {}
    signals = engine.score_from_features(features, breakout_probs=probs)
    if not signals:
        raise HTTPException(status_code=404, detail=f"Could not score {t}")

    cap = meta_store.get_market_caps([t]).get(t) if meta_store.exists else None
    sec = meta_store.get_sectors([t]).get(t) if meta_store.exists else None
    inst = meta_store.get_institutional_pcts([t]).get(t) if meta_store.exists else None
    watchlist = frozenset(s.upper() for s in (settings.watchlist_symbols or []))

    return _record_to_response(
        signals[0].to_dict(),
        market_cap=cap,
        institutional_pct=inst,
        sector=sec,
        is_watchlist=t in watchlist,
    )


@router.post("/recompute", response_model=AlphaBatchResponse)
async def recompute_alpha(
    background_tasks: BackgroundTasks,
    sync: bool = Query(default=False, description="Run synchronously and wait"),
    max_tickers: int | None = Query(default=None, ge=1),
    settings: TycheSettings = Depends(get_settings),
) -> AlphaBatchResponse:
    """Trigger a full directional alpha batch recompute.

    By default runs in the background and returns immediately. Pass
    ``sync=true`` to wait for completion (useful for small ``max_tickers`` runs).
    """
    from tyche.workflow.alpha_batch import run_alpha_batch

    alpha_floor = settings.alpha_min_market_cap_millions * 1_000_000
    variants = ["peak", "sustained"] if settings.alpha_sustained_enabled else ["peak"]

    if sync:
        result = run_alpha_batch(
            data_dir=settings.data_dir,
            min_market_cap=alpha_floor,
            max_tickers=max_tickers,
            variants=variants,
        )
        return AlphaBatchResponse(**result)

    background_tasks.add_task(
        run_alpha_batch,
        data_dir=settings.data_dir,
        min_market_cap=alpha_floor,
        max_tickers=max_tickers,
        variants=variants,
    )
    return AlphaBatchResponse(status="started")


def _record_to_response(
    r: dict,
    *,
    market_cap: float | None = None,
    institutional_pct: float | None = None,
    sector: str | None = None,
    is_watchlist: bool = False,
) -> AlphaSignalResponse:
    factors = r.get("factors") or {}
    demand = r.get("demand") or {}
    return AlphaSignalResponse(
        ticker=r.get("ticker", ""),
        alpha_score=r.get("alpha_score", 0.0) or 0.0,
        signal=r.get("signal", "avoid"),
        horizon=r.get("horizon", "none"),
        factors=AlphaFactorScores(
            momentum=factors.get("momentum", 0.0) or 0.0,
            relative_strength=factors.get("relative_strength", 0.0) or 0.0,
            trend_quality=factors.get("trend_quality", 0.0) or 0.0,
            breakout=factors.get("breakout", 0.0) or 0.0,
            volume_thrust=factors.get("volume_thrust", 0.0) or 0.0,
        ),
        breakout_prob_swing=r.get("breakout_prob_swing"),
        breakout_prob_trend=r.get("breakout_prob_trend"),
        breakout_prob_thematic=r.get("breakout_prob_thematic"),
        last_close=r.get("last_close", 0.0) or 0.0,
        return_63d=r.get("return_63d"),
        return_126d=r.get("return_126d"),
        return_252d=r.get("return_252d"),
        rs_126d=r.get("rs_126d"),
        pct_off_52w_high=r.get("pct_off_52w_high"),
        ema_stack_score=int(r.get("ema_stack_score", 0) or 0),
        volume_thrust_ratio=r.get("volume_thrust_ratio"),
        as_of_date=r.get("as_of_date"),
        regime=r.get("regime", "narrative") or "narrative",
        demand=AlphaDemandDimensions(
            fund=demand.get("fund"),
            est=demand.get("est"),
            catalyst=demand.get("catalyst"),
            policy=demand.get("policy"),
            squeeze=demand.get("squeeze"),
            net=demand.get("net"),
        )
        if demand
        else None,
        demand_multiplier=r.get("demand_multiplier"),
        overextension_score=r.get("overextension_score"),
        overextension_penalty=r.get("overextension_penalty"),
        market_cap=market_cap if market_cap and market_cap > 0 else None,
        institutional_pct=(
            institutional_pct if institutional_pct and institutional_pct > 0 else None
        ),
        sector=sector,
        is_watchlist=is_watchlist,
    )
