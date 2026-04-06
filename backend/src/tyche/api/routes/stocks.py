"""API routes for the Stocks module — pullbacks, recommendations, conviction history, transitions."""

from __future__ import annotations

from datetime import date, timedelta

import structlog
from fastapi import APIRouter, Depends, Query

from tyche.api.deps import (
    get_conviction_engine,
    get_data_store,
    get_ticker_meta_store,
)
from tyche.config import TycheSettings, get_settings
from tyche.conviction.alerts import detect_pullback_alerts
from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.institutional import (
    filter_by_institutional_ownership,
    get_cached_ownership_batch,
)
from tyche.persistence.conviction_repository import (
    get_active_pullbacks,
    get_latest_snapshot_date,
    get_snapshots_for_date,
    get_ticker_history,
    get_transitions,
)
from tyche.schemas.alerts import (
    CSPFallbackAlertResponse,
    ExitCheckResponse,
    ExitSignalResponse,
    ExpiredCSPResponse,
    HistoricalBounceStats,
    PullbackAlertResponse,
    RecordCSPExpiryRequest,
    StockBuyRecommendationResponse,
    StockPositionRequest,
    StockPositionResponse,
)
from tyche.schemas.stocks import (
    ActivePullbacksResponse,
    ConvictionBatchStatusResponse,
    ConvictionHistoryResponse,
    ConvictionSnapshotResponse,
    ConvictionTransitionResponse,
    StockRecommendationsResponse,
    TransitionsListResponse,
)
from tyche.workflow.expiry_tracker import ExpiryTracker
from tyche.workflow.stock_recommender import (
    generate_recommendations_from_snapshots,
    generate_stock_recommendations,
)

logger = structlog.get_logger()
router = APIRouter(prefix="/stocks", tags=["stocks"])

_expiry_tracker: ExpiryTracker | None = None


def _get_expiry_tracker(
    settings: TycheSettings = Depends(get_settings),
) -> ExpiryTracker:
    global _expiry_tracker
    if _expiry_tracker is None:
        _expiry_tracker = ExpiryTracker(db_dir=settings.db_dir)
    return _expiry_tracker


def _snapshot_to_response(snap) -> ConvictionSnapshotResponse:
    d = snap.to_dict()
    return ConvictionSnapshotResponse(**d)


def _transition_to_response(t) -> ConvictionTransitionResponse:
    d = t.to_dict()
    return ConvictionTransitionResponse(**d)


def _market_cap_label(market_cap: float | None) -> str:
    """Classify market cap into human-readable label."""
    if market_cap is None:
        return ""
    if market_cap >= 200_000_000_000:
        return "Mega Cap"
    if market_cap >= 10_000_000_000:
        return "Large Cap"
    if market_cap >= 2_000_000_000:
        return "Mid Cap"
    if market_cap >= 300_000_000:
        return "Small Cap"
    return "Micro Cap"


def _build_action_text(
    is_21ema: bool,
    conviction_level: str,
    volume_declining: bool,
) -> str:
    """Build suggested_action text that respects the actual conviction_level."""
    level = conviction_level or "none"
    is_high = level == "high"

    if is_21ema and volume_declining and is_high:
        return (
            "High-conviction entry zone — institutional defense at 21-EMA "
            "with declining volume. Consider larger position."
        )
    if is_21ema and volume_declining:
        return (
            f"Pullback to 21-EMA with declining volume. Conviction: {level}. "
            "Monitor for confirmation before sizing up."
        )
    if is_21ema:
        return (
            f"Pullback to 21-EMA — institutional defense zone. Conviction: {level}. "
            "Volume not yet declining; watch for confirmation."
        )
    if volume_declining and is_high:
        return (
            "Pullback to 8-EMA with declining volume — "
            "high conviction, consider standard position entry."
        )
    if volume_declining:
        return (
            f"Pullback to 8-EMA with declining volume. Conviction: {level}. "
            "Consider standard position entry."
        )
    return (
        f"Pullback to 8-EMA — conviction: {level}. "
        "Wait for volume confirmation if cautious."
    )


def _profile_to_bounce_stats(profile) -> HistoricalBounceStats:
    """Convert a TickerPullbackProfile into HistoricalBounceStats."""
    return HistoricalBounceStats(
        pullback_type=profile.pullback_type,
        event_count=profile.event_count,
        median_peak_gain_pct=round(profile.median_peak_gain_pct, 2),
        mean_peak_gain_pct=round(profile.mean_peak_gain_pct, 2),
        p25_peak_gain_pct=round(profile.p25_peak_gain_pct, 2),
        p75_peak_gain_pct=round(profile.p75_peak_gain_pct, 2),
        median_exit_gain_pct=round(profile.median_exit_gain_pct, 2),
        win_rate_5pct=round(profile.win_rate_5pct, 4),
        win_rate_10pct=round(profile.win_rate_10pct, 4),
        median_days_to_peak=profile.median_days_to_peak,
        median_days_to_exit=profile.median_days_to_exit,
        avg_max_drawdown_pct=round(profile.avg_max_drawdown_pct, 2),
        suggested_exit_pct=round(profile.p75_peak_gain_pct, 2),
    )


def _snapshot_to_pullback_alert(
    snap,
    inst_pct: float | None = None,
    meta: dict | None = None,
    bounce_profiles: dict[str, dict] | None = None,
) -> PullbackAlertResponse:
    """Convert a ConvictionSnapshot row into a PullbackAlertResponse."""
    from tyche.conviction.alerts import _compute_stop_loss, _institutional_label

    meta = meta or {}
    is_21ema = snap.trend_state == "pullback_to_21ema"
    alert_type = "pullback_21ema" if is_21ema else "pullback_8ema"
    severity = "high" if is_21ema else "info"
    position_size_hint = "large" if is_21ema else "standard"

    raw_conv = getattr(snap, "raw_conviction", None)
    if not raw_conv or raw_conv == "none":
        raw_conv = snap.conviction_level
    action = _build_action_text(is_21ema, raw_conv, snap.volume_declining)
    raw_cap = meta.get("market_cap")

    historical_bounce: HistoricalBounceStats | None = None
    if bounce_profiles:
        ticker_profiles = bounce_profiles.get(snap.ticker, {})
        ptype = "21ema" if is_21ema else "8ema"
        profile = ticker_profiles.get(ptype)
        if profile:
            historical_bounce = _profile_to_bounce_stats(profile)

    return PullbackAlertResponse(
        ticker=snap.ticker,
        alert_type=alert_type,
        severity=severity,
        trend_state=snap.trend_state,
        conviction_level=raw_conv,
        raw_conviction=raw_conv,
        last_close=round(snap.last_close, 2),
        ema_8=round(snap.ema_8, 4),
        ema_21=round(snap.ema_21, 4),
        ema_8_slope=round(snap.ema_8_slope, 6),
        ema_21_slope=round(snap.ema_21_slope, 6),
        volume_declining=snap.volume_declining,
        institutional_pct=round(inst_pct, 4) if inst_pct is not None else None,
        institutional_label=_institutional_label(inst_pct),
        suggested_action=action,
        position_size_hint=position_size_hint,
        stop_loss_level=_compute_stop_loss(alert_type, snap.ema_21),
        detected_at=snap.computed_at.isoformat() if snap.computed_at else "",
        market_cap=round(raw_cap, 2) if raw_cap is not None else None,
        market_cap_label=_market_cap_label(raw_cap),
        exchange=meta.get("exchange", ""),
        name=meta.get("name", ""),
        days_above_both_emas=snap.days_above_both_emas or 0,
        avg_volume_20d=snap.avg_volume_20d or 0,
        price_to_8ema_pct=round(snap.price_to_8ema_pct or 0, 4),
        price_to_21ema_pct=round(snap.price_to_21ema_pct or 0, 4),
        historical_bounce=historical_bounce,
    )


# ── Active pullbacks (from persisted snapshots) ───────────────────────


@router.get("/pullbacks/active", response_model=ActivePullbacksResponse)
async def get_active_pullbacks_endpoint(
    settings: TycheSettings = Depends(get_settings),
    ticker_meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
) -> ActivePullbacksResponse:
    """Get active pullbacks from persisted conviction snapshots.

    Returns two sections: watchlist pullbacks (highlighted) and universe pullbacks.
    """
    today = date.today()
    snapshots = await get_active_pullbacks(today)

    if not snapshots:
        yesterday = today - timedelta(days=1)
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)
        snapshots = await get_active_pullbacks(yesterday)
        today = yesterday

    watchlist_symbols = set(s.upper() for s in settings.watchlist_symbols)

    inst_tickers = [s.ticker for s in snapshots]
    inst_map: dict[str, float] = {}
    if inst_tickers and len(inst_tickers) <= 200:
        try:
            _, inst_map = await filter_by_institutional_ownership(
                inst_tickers, min_pct=0.0
            )
        except Exception:
            logger.warning("pullbacks_institutional_failed", exc_info=True)

    meta_map: dict[str, dict] = {}
    if ticker_meta_store.exists:
        try:
            meta_df = ticker_meta_store.read_meta()
            for _, row in meta_df.iterrows():
                meta_map[row["ticker"]] = {
                    "market_cap": row.get("market_cap"),
                    "exchange": row.get("exchange", ""),
                    "name": row.get("name", ""),
                }
        except Exception:
            logger.warning("pullbacks_meta_load_failed", exc_info=True)

    bounce_profiles: dict[str, dict] = {}
    try:
        from tyche.persistence.backtest_repository import get_profiles_map

        bounce_profiles = await get_profiles_map()
    except Exception:
        logger.debug("backtest_profiles_not_available")

    watchlist_alerts: list[PullbackAlertResponse] = []
    universe_alerts: list[PullbackAlertResponse] = []

    for snap in snapshots:
        inst_pct = inst_map.get(snap.ticker)
        meta = meta_map.get(snap.ticker, {})
        alert = _snapshot_to_pullback_alert(snap, inst_pct, meta, bounce_profiles)

        if snap.ticker in watchlist_symbols:
            watchlist_alerts.append(alert)
        else:
            universe_alerts.append(alert)

    today_transitions = await get_transitions(
        from_date=today, to_date=today
    )

    return ActivePullbacksResponse(
        watchlist=watchlist_alerts,
        universe=universe_alerts,
        transitions_today=[_transition_to_response(t) for t in today_transitions],
        as_of_date=today.isoformat(),
    )


# ── Stock buy recommendations (from DB snapshots) ─────────────────────


@router.get("/recommendations", response_model=StockRecommendationsResponse)
async def get_stock_recommendations_endpoint(
    settings: TycheSettings = Depends(get_settings),
) -> StockRecommendationsResponse:
    """Get stock buy recommendations from persisted pullback snapshots."""
    today = date.today()
    snapshots = await get_active_pullbacks(today)

    if not snapshots:
        yesterday = today - timedelta(days=1)
        while yesterday.weekday() >= 5:
            yesterday -= timedelta(days=1)
        snapshots = await get_active_pullbacks(yesterday)
        today = yesterday

    if not snapshots:
        return StockRecommendationsResponse(
            recommendations=[], as_of_date=today.isoformat()
        )

    tickers = [s.ticker for s in snapshots]
    inst_map: dict[str, float] = {}
    if len(tickers) <= 200:
        try:
            _, inst_map = await filter_by_institutional_ownership(
                tickers, min_pct=0.0
            )
        except Exception:
            logger.warning("recommendations_institutional_failed", exc_info=True)

    filtered = []
    for snap in snapshots:
        inst_pct = inst_map.get(snap.ticker)
        if inst_pct is not None and inst_pct < settings.min_institutional_pct_stock_buy:
            continue
        filtered.append(snap)

    if not filtered:
        return StockRecommendationsResponse(
            recommendations=[], as_of_date=today.isoformat()
        )

    recs = generate_recommendations_from_snapshots(
        filtered, institutional_map=inst_map,
    )

    return StockRecommendationsResponse(
        recommendations=[StockBuyRecommendationResponse(**r.to_dict()) for r in recs],
        as_of_date=today.isoformat(),
    )


# ── All conviction snapshots for a date ───────────────────────────────


@router.get(
    "/conviction/snapshots",
    response_model=list[ConvictionSnapshotResponse],
)
async def get_conviction_snapshots_endpoint(
    as_of_date: str | None = Query(None, description="Date in YYYY-MM-DD format (default: latest trading day)"),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
) -> list[ConvictionSnapshotResponse]:
    """Get all conviction snapshots for a given date from the DB."""
    if as_of_date:
        target = date.fromisoformat(as_of_date)
    else:
        target = date.today()

    snaps = await get_snapshots_for_date(target)
    if not snaps:
        latest = await get_latest_snapshot_date()
        if latest and latest < target:
            snaps = await get_snapshots_for_date(latest)

    tickers = [s.ticker for s in snaps]
    market_caps = meta_store.get_market_caps(tickers) if meta_store.exists else {}
    inst_persisted = meta_store.get_institutional_pcts(tickers) if meta_store.exists else {}
    inst_cached = get_cached_ownership_batch(tickers)
    inst_ownership = {**inst_persisted, **inst_cached}

    results = []
    for s in snaps:
        resp = _snapshot_to_response(s)
        resp.market_cap = market_caps.get(s.ticker)
        resp.institutional_pct = inst_ownership.get(s.ticker)
        results.append(resp)

    return results


# ── On-demand gate computation for a single ticker ────────────────────


@router.get("/conviction/{ticker}/gates")
async def get_ticker_gates(
    ticker: str,
    conviction_engine: ConvictionEngine = Depends(get_conviction_engine),
    data_store: OHLCVStore = Depends(get_data_store),
) -> dict:
    """Compute gate results for a single ticker on demand (~10ms)."""
    if not data_store.exists:
        return {"ticker": ticker.upper(), "gate_results": [], "error": "No OHLCV data"}

    ticker_data = data_store.read_tickers([ticker.upper()])
    if ticker.upper() not in ticker_data:
        return {"ticker": ticker.upper(), "gate_results": [], "error": "Ticker not in store"}

    signal = conviction_engine.analyze(ticker.upper(), ticker_data[ticker.upper()])
    return {
        "ticker": signal.ticker,
        "gate_results": [g.to_dict() for g in signal.gate_results] if signal.gate_results else [],
    }


# ── Conviction history ────────────────────────────────────────────────


@router.get("/conviction/history", response_model=ConvictionHistoryResponse)
async def get_conviction_history_endpoint(
    ticker: str = Query(..., description="Ticker symbol"),
    days: int = Query(30, ge=1, le=365, description="Number of days of history"),
) -> ConvictionHistoryResponse:
    """Get historical conviction snapshots and transitions for a ticker."""
    snapshots = await get_ticker_history(ticker.upper(), days=days)
    transitions_list = await get_transitions(
        from_date=date.today() - timedelta(days=days),
        to_date=date.today(),
        ticker=ticker.upper(),
    )

    return ConvictionHistoryResponse(
        ticker=ticker.upper(),
        snapshots=[_snapshot_to_response(s) for s in snapshots],
        transitions=[_transition_to_response(t) for t in transitions_list],
    )


# ── State transitions ─────────────────────────────────────────────────


@router.get("/transitions", response_model=TransitionsListResponse)
async def get_transitions_endpoint(
    days: int = Query(7, ge=1, le=90, description="Number of days to look back"),
    to_states: str | None = Query(
        None, description="Comma-separated target states to filter (e.g. pullback_to_8ema,pullback_to_21ema)"
    ),
) -> TransitionsListResponse:
    """Get recent conviction state transitions."""
    to_date_val = date.today()
    from_date_val = to_date_val - timedelta(days=days)

    state_filter = None
    if to_states:
        state_filter = [s.strip() for s in to_states.split(",") if s.strip()]

    transitions_list = await get_transitions(
        from_date=from_date_val,
        to_date=to_date_val,
        to_states=state_filter,
    )

    return TransitionsListResponse(
        transitions=[_transition_to_response(t) for t in transitions_list],
        from_date=from_date_val.isoformat(),
        to_date=to_date_val.isoformat(),
    )


# ── Conviction batch refresh ──────────────────────────────────────────


@router.post("/conviction/refresh", response_model=ConvictionBatchStatusResponse)
async def refresh_conviction(
    settings: TycheSettings = Depends(get_settings),
    conviction_engine: ConvictionEngine = Depends(get_conviction_engine),
    data_store: OHLCVStore = Depends(get_data_store),
    ticker_meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
) -> ConvictionBatchStatusResponse:
    """Trigger a full conviction batch recompute on demand."""
    from tyche.workflow.conviction_batch import run_conviction_batch

    result = await run_conviction_batch(
        data_store=data_store,
        conviction_engine=conviction_engine,
        ticker_meta_store=ticker_meta_store,
        min_market_cap=settings.conviction_batch_min_market_cap_millions * 1_000_000,
        min_price=settings.conviction_batch_min_price,
        min_avg_volume=settings.conviction_batch_min_avg_volume,
        retention_days=settings.conviction_snapshot_retention_days,
    )

    return ConvictionBatchStatusResponse(**result.to_dict())


# ── CSP expiry endpoints (migrated from /alerts/) ─────────────────────


@router.get("/csp-fallbacks", response_model=list[CSPFallbackAlertResponse])
async def get_csp_fallbacks(
    settings: TycheSettings = Depends(get_settings),
    conviction_engine: ConvictionEngine = Depends(get_conviction_engine),
    data_store: OHLCVStore = Depends(get_data_store),
    expiry_tracker: ExpiryTracker = Depends(_get_expiry_tracker),
) -> list[CSPFallbackAlertResponse]:
    """Get CSP expiry fallback alerts."""
    watched = expiry_tracker.get_watched_tickers()
    if not watched or not data_store.exists:
        return []

    ticker_data = data_store.read_tickers(watched)
    signals = conviction_engine.analyze_batch(ticker_data, requested_tickers=watched)
    signal_map = {s.ticker: s for s in signals}

    pullback_alerts = detect_pullback_alerts(signal_map, min_institutional_pct=0.0)
    fallbacks = expiry_tracker.generate_fallback_alerts(pullback_alerts)

    return [
        CSPFallbackAlertResponse(
            ticker=f.ticker,
            expired_strike=f.expired_strike,
            expiry_date=f.expiry_date,
            premium_collected=round(f.premium_collected, 2),
            pullback_alert=PullbackAlertResponse(**f.pullback_alert.to_dict()),
            message=f.message,
        )
        for f in fallbacks
    ]


@router.get("/csp-expiries", response_model=list[ExpiredCSPResponse])
async def get_expired_csps(
    expiry_tracker: ExpiryTracker = Depends(_get_expiry_tracker),
) -> list[ExpiredCSPResponse]:
    """List all recorded CSP expirations."""
    records = expiry_tracker.get_all_records()
    return [
        ExpiredCSPResponse(
            ticker=r.ticker,
            expired_strike=r.expired_strike,
            expiry_date=r.expiry_date,
            premium_collected=r.premium_collected,
            recorded_at=r.recorded_at,
        )
        for r in records
    ]


@router.post("/csp-expiries", response_model=ExpiredCSPResponse)
async def record_csp_expiry(
    req: RecordCSPExpiryRequest,
    expiry_tracker: ExpiryTracker = Depends(_get_expiry_tracker),
) -> ExpiredCSPResponse:
    """Record a CSP that expired worthless."""
    expiry_tracker.record_expiry(
        ticker=req.ticker.upper(),
        strike=req.strike,
        expiry_date=req.expiry_date,
        premium_collected=req.premium_collected,
    )
    records = expiry_tracker.get_all_records()
    record = next(
        (r for r in records if r.ticker == req.ticker.upper() and r.expired_strike == req.strike),
        records[-1],
    )
    return ExpiredCSPResponse(
        ticker=record.ticker,
        expired_strike=record.expired_strike,
        expiry_date=record.expiry_date,
        premium_collected=record.premium_collected,
        recorded_at=record.recorded_at,
    )


@router.delete("/csp-expiries/{ticker}")
async def remove_csp_expiry(
    ticker: str,
    expiry_tracker: ExpiryTracker = Depends(_get_expiry_tracker),
) -> dict[str, str | int]:
    """Remove a ticker from the CSP expiry watch list."""
    removed = expiry_tracker.remove_ticker(ticker.upper())
    return {"status": "ok", "ticker": ticker.upper(), "removed": removed}


# ── OHLCV refresh ─────────────────────────────────────────────────────


@router.post("/ohlcv/refresh")
async def refresh_ohlcv(
    data_store: OHLCVStore = Depends(get_data_store),
    include_today: bool = Query(True, description="Include today's bars (use after market close)"),
) -> dict:
    """On-demand OHLCV data refresh from Polygon."""
    from tyche.api.deps import get_polygon, get_ticker_meta_store
    from tyche.market_data.data_store import bootstrap_ohlcv

    settings = get_settings()
    polygon = get_polygon(settings)
    if polygon is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Polygon API key not configured",
        )

    meta_store = get_ticker_meta_store(settings)
    result = await bootstrap_ohlcv(
        polygon, data_store, days=5, meta_store=meta_store,
        include_today=include_today,
    )
    return {
        "status": "ok",
        "include_today": include_today,
        **result,
    }


# ── Backtest profiles ─────────────────────────────────────────────────


@router.get("/backtest/profiles")
async def get_backtest_profiles() -> list[dict]:
    """Return all ticker pullback profiles from the backtest."""
    from tyche.persistence.backtest_repository import get_all_profiles

    profiles = await get_all_profiles()
    return [p.to_dict() for p in profiles]


@router.get("/backtest/profile/{ticker}")
async def get_backtest_profile(ticker: str) -> dict:
    """Return per-ticker pullback backtest stats."""
    from tyche.persistence.backtest_repository import (
        get_events_for_ticker,
        get_profile_for_ticker,
    )

    profiles = await get_profile_for_ticker(ticker)
    profile_dicts = [p.to_dict() for p in profiles]

    events = await get_events_for_ticker(ticker, limit=50)
    event_dicts = [e.to_dict() for e in events]

    return {
        "ticker": ticker.upper(),
        "profiles": profile_dicts,
        "recent_events": event_dicts,
    }


# ── Stock Positions ───────────────────────────────────────────────────


@router.post("/positions", response_model=StockPositionResponse)
async def create_position(
    req: StockPositionRequest,
) -> StockPositionResponse:
    """Record a stock purchase with auto-computed exit targets."""
    from tyche.persistence.position_repository import (
        create_position as repo_create,
    )

    purchase_date_val = date.fromisoformat(req.purchase_date)
    position = await repo_create(
        ticker=req.ticker.upper(),
        purchase_price=req.purchase_price,
        quantity=req.quantity,
        purchase_date=purchase_date_val,
        pullback_type=req.pullback_type,
    )
    return StockPositionResponse(**position.to_dict())


@router.get("/positions", response_model=list[StockPositionResponse])
async def list_positions(
    active_only: bool = Query(False, description="Only return active positions"),
) -> list[StockPositionResponse]:
    """List all stock positions."""
    from tyche.persistence.position_repository import (
        get_active_positions,
        get_all_positions,
    )

    positions = await get_active_positions() if active_only else await get_all_positions()
    return [StockPositionResponse(**p.to_dict()) for p in positions]


@router.get("/positions/active", response_model=list[StockPositionResponse])
async def list_active_positions() -> list[StockPositionResponse]:
    """List only active stock positions with latest prices and exit targets."""
    from tyche.persistence.position_repository import get_active_positions

    positions = await get_active_positions()
    return [StockPositionResponse(**p.to_dict()) for p in positions]


@router.post("/positions/{position_id}/exit")
async def exit_position(
    position_id: str,
    exit_price: float = Query(..., description="Price at which the position was sold"),
    exit_reason: str = Query("manual", description="Reason for exit"),
) -> dict:
    """Manually mark a position as exited/sold."""
    from tyche.persistence.position_repository import get_position, mark_exited

    position = await get_position(position_id)
    if not position:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Position not found")

    await mark_exited(position_id, exit_price, exit_reason)
    return {"status": "ok", "position_id": position_id, "exit_reason": exit_reason}


@router.delete("/positions/{position_id}")
async def delete_position_endpoint(position_id: str) -> dict:
    """Delete a stock position record."""
    from tyche.persistence.position_repository import delete_position

    deleted = await delete_position(position_id)
    if not deleted:
        from fastapi import HTTPException

        raise HTTPException(status_code=404, detail="Position not found")
    return {"status": "ok", "position_id": position_id}


@router.post("/positions/check-exits", response_model=ExitCheckResponse)
async def check_exits(
    data_store: OHLCVStore = Depends(get_data_store),
) -> ExitCheckResponse:
    """On-demand trigger of the exit monitor. Returns any triggered signals."""
    from tyche.workflow.exit_monitor import check_exit_signals

    result = await check_exit_signals(data_store)
    return ExitCheckResponse(
        positions_checked=result.positions_checked,
        prices_updated=result.prices_updated,
        profit_targets_hit=result.profit_targets_hit,
        stop_losses_hit=result.stop_losses_hit,
        errors=result.errors,
        signals=[ExitSignalResponse(**s) for s in result.signals],
    )


@router.get("/positions/signals", response_model=list[ExitSignalResponse])
async def list_recent_signals() -> list[ExitSignalResponse]:
    """List recent exit signals across all positions."""
    from tyche.persistence.position_repository import get_recent_signals

    signals = await get_recent_signals()
    return [ExitSignalResponse(**s.to_dict()) for s in signals]
