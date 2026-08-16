"""Directional Alpha engine routes.

Primary read path: the pre-computed snapshot from ``alpha_signals.parquet``
(written by the nightly alpha batch). ``POST /alpha/recompute`` triggers a
fresh batch in the background. ``GET /alpha/signal/{ticker}`` computes a
single name on demand.

Fully additive — no existing route or behavior is modified.
"""

from __future__ import annotations

import asyncio
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
from tyche.persistence.published_routes import (
    alpha_needs_signals_fallback,
    apply_alpha_scan_filters,
    get_stock_alpha_scan,
    load_published_route,
)
from tyche.schemas.alpha import (
    AlphaBatchResponse,
    AlphaDemandDimensions,
    AlphaFactorScores,
    AlphaPersistenceResponse,
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
    """Return ranked directional alpha signals from published JSON or signal Parquet."""
    published_env = (
        load_published_route("stocks_alpha", settings=settings)
        if settings.api_prefer_published_signals
        else None
    )
    prefer_published = (
        settings.api_prefer_published_signals
        and not alpha_needs_signals_fallback(
            limit=limit,
            published_row_count=published_env.row_count if published_env else None,
        )
    )

    loaded = await asyncio.to_thread(
        get_stock_alpha_scan,
        settings=settings,
        variant=variant,
        prefer_published=prefer_published,
    )
    if loaded is None:
        served_variant = variant if variant in _VALID_VARIANTS else "peak"
        return AlphaScanResponse(
            scanned_at=datetime.now(timezone.utc).isoformat(),
            ml_available=False,
            variant=served_variant,
            total=0,
            signals=[],
        )

    scan, layer = loaded
    if layer == "signals" and min_market_cap_millions is not None:
        scan = _refilter_alpha_scan_cap(
            scan,
            min_market_cap_millions=min_market_cap_millions,
            meta_store=meta_store,
            settings=settings,
        )

    return apply_alpha_scan_filters(
        scan,
        signal=signal,
        horizon=horizon,
        min_score=min_score,
        min_market_cap_millions=min_market_cap_millions,
        limit=limit,
    )


def _refilter_alpha_scan_cap(
    scan: AlphaScanResponse,
    *,
    min_market_cap_millions: float,
    meta_store: TickerMetaStore,
    settings: TycheSettings,
) -> AlphaScanResponse:
    """Re-apply a custom market-cap floor when serving from the signal store."""
    if min_market_cap_millions == settings.alpha_min_market_cap_millions:
        return scan
    if not meta_store.exists:
        return scan

    floor = min_market_cap_millions * 1_000_000
    tickers = [s.ticker for s in scan.signals]
    eligible = set(meta_store.filter_equity_only(tickers))
    caps = meta_store.get_market_caps(tickers)
    kept = [
        s
        for s in scan.signals
        if s.ticker in eligible and (caps.get(s.ticker) or 0) >= floor
    ]
    strong_buy = sum(1 for s in kept if s.signal == "strong_buy")
    buy = sum(1 for s in kept if s.signal == "buy")
    return AlphaScanResponse(
        scanned_at=scan.scanned_at,
        as_of_date=scan.as_of_date,
        computed_at=scan.computed_at,
        ml_available=scan.ml_available,
        variant=scan.variant,
        total=len(kept),
        strong_buy_count=strong_buy,
        buy_count=buy,
        signals=kept,
    )


@router.get("/persistence", response_model=AlphaPersistenceResponse)
async def alpha_persistence(
    variant: str = Query(
        default="sustained",
        description="Model variant: 'sustained' (default) or 'peak'.",
    ),
    sessions: int | None = Query(
        default=None, ge=2, le=120,
        description="Recent sessions used for persistence metrics (default: config).",
    ),
    top: int | None = Query(
        default=None, ge=1, le=500,
        description="Max gems to return (default: config).",
    ),
    min_persistence: float = Query(default=0.0, ge=0.0, le=100.0),
    signal: str | None = Query(
        default=None, description="Filter last_signal: strong_buy|buy|watch|avoid"
    ),
    force: bool = Query(
        default=False,
        description="Recompute from history instead of serving the published artifact.",
    ),
    settings: TycheSettings = Depends(get_settings),
) -> AlphaPersistenceResponse:
    """Day-over-day directional-alpha persistence (which names stay strong).

    Serves ``signals/alpha/persistence_{variant}.json`` (written after the nightly
    alpha batch). Falls back to an on-demand compute from the accumulated dated
    snapshots when the artifact is absent or ``force=true``.
    """
    import asyncio

    from tyche.workflow.alpha_persistence import compute_persistence, load_persisted

    if variant not in _VALID_VARIANTS:
        raise HTTPException(status_code=400, detail=f"Invalid variant: {variant}")

    sess = sessions or settings.alpha_persistence_sessions
    top_n = top or settings.alpha_persistence_top

    resp: AlphaPersistenceResponse | None = None
    if not force:
        resp = await asyncio.to_thread(load_persisted, settings, variant)
    if resp is None:
        resp = await asyncio.to_thread(
            compute_persistence,
            settings,
            variant=variant,
            sessions=sess,
            top=top_n,
        )
    if resp is None:
        return AlphaPersistenceResponse(
            generated_at=datetime.now(timezone.utc).isoformat(),
            variant=variant,
        )

    gems = resp.gems
    if min_persistence > 0:
        gems = [g for g in gems if g.persistence >= min_persistence]
    if signal and signal in _VALID_SIGNALS:
        gems = [g for g in gems if g.last_signal == signal]
    if top is not None:
        gems = gems[:top_n]

    return resp.model_copy(update={"gems": gems, "total": len(gems)})


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
