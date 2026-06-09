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
from tyche.conviction.engine import ConvictionEngine, TrendState
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.institutional import get_cached_ownership_batch
from tyche.persistence.conviction_repository import (
    get_active_pullbacks,
    get_latest_snapshot_date,
    get_snapshots_for_date,
    get_ticker_history,
    get_transitions,
)
from tyche.schemas.alerts import (
    BulkPositionRequest,
    BulkPositionResponse,
    CSPFallbackAlertResponse,
    DeepDipAlertResponse,
    DeepDipScanResponse,
    DipClassificationResponse,
    ExitCheckResponse,
    ExitSignalResponse,
    ExpiredCSPResponse,
    HistoricalBounceStats,
    MarketContextResponse,
    PullbackAlertResponse,
    RecordCSPExpiryRequest,
    RecoverySignalResponse,
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

_deep_dip_cache: dict[str, DeepDipScanResponse] = {}


def invalidate_deep_dip_cache() -> None:
    """Clear the deep dip route-level cache."""
    _deep_dip_cache.clear()
    logger.info("deep_dip_cache_invalidated")


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
        ema_50=round(getattr(snap, "ema_50", 0.0) or 0.0, 4),
        ema_50_slope=round(getattr(snap, "ema_50_slope", 0.0) or 0.0, 6),
        rsi_14=round(getattr(snap, "rsi_14", 0.0) or 0.0, 2),
        iv_rank=round(snap.iv_rank, 1) if getattr(snap, "iv_rank", None) is not None else None,
        iv_percentile=round(snap.iv_percentile, 1) if getattr(snap, "iv_percentile", None) is not None else None,
        atm_iv=round(snap.atm_iv, 4) if getattr(snap, "atm_iv", None) is not None else None,
        vrp=round(snap.vrp, 4) if getattr(snap, "vrp", None) is not None else None,
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
        sector=meta.get("sector"),
        days_above_both_emas=snap.days_above_both_emas or 0,
        avg_volume_20d=snap.avg_volume_20d or 0,
        price_to_8ema_pct=round(snap.price_to_8ema_pct or 0, 4),
        price_to_21ema_pct=round(snap.price_to_21ema_pct or 0, 4),
        csp_safety_prob=round(snap.csp_safety_prob, 4) if getattr(snap, "csp_safety_prob", None) is not None else None,
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
    if ticker_meta_store.exists:
        inst_map = ticker_meta_store.get_institutional_pcts(inst_tickers)
    inst_cached = get_cached_ownership_batch(inst_tickers)
    inst_map = {**inst_map, **inst_cached}

    meta_map: dict[str, dict] = {}
    if ticker_meta_store.exists:
        try:
            meta_map = ticker_meta_store.get_meta_batch(inst_tickers)
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
    ticker_meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
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
    if ticker_meta_store.exists:
        inst_map = ticker_meta_store.get_institutional_pcts(tickers)
    inst_cached = get_cached_ownership_batch(tickers)
    inst_map = {**inst_map, **inst_cached}

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
    settings: TycheSettings = Depends(get_settings),
    meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
) -> list[ConvictionSnapshotResponse]:
    """Get all conviction snapshots for a given date from published JSON or DB."""
    from tyche.persistence.published_routes import get_stocks_conviction_rows

    if as_of_date is None:
        published = get_stocks_conviction_rows(settings=settings)
        if published is not None:
            rows, _layer = published
            return rows

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
    sectors = meta_store.get_sectors(tickers) if meta_store.exists else {}

    results = []
    for s in snaps:
        resp = _snapshot_to_response(s)
        resp.market_cap = market_caps.get(s.ticker)
        resp.institutional_pct = inst_ownership.get(s.ticker)
        resp.sector = sectors.get(s.ticker)
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
    from tyche.api.routes.conviction import invalidate_conviction_cache
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

    invalidate_conviction_cache(clear_engine=False)

    return ConvictionBatchStatusResponse(**result.to_dict())


# ── Deep dip oversold scan ─────────────────────────────────────────────


@router.get("/deep-dips", response_model=DeepDipScanResponse)
async def get_deep_dip_candidates(
    force: bool = Query(default=False, description="Bypass cache"),
    settings: TycheSettings = Depends(get_settings),
    ticker_meta_store: TickerMetaStore = Depends(get_ticker_meta_store),
) -> DeepDipScanResponse:
    """Scan for oversold deep dip candidates suitable for buy + covered call.

    Results are cached by as_of_date. Cache is invalidated when the
    conviction batch runs (new OHLCV data arrives).
    Heavy deps (ConvictionEngine, OHLCVStore) resolved lazily after cache check.
    """
    from tyche.conviction.dip_classifier import DipCatalystClassifier
    from tyche.market_data.filing_signals import get_all_filing_signals
    from tyche.market_data.news_signals import get_all_signals

    today = date.today()
    cache_key = today.isoformat()

    if not force and cache_key in _deep_dip_cache:
        logger.info("deep_dip_cache_hit", as_of_date=cache_key)
        return _deep_dip_cache[cache_key]

    conviction_engine = get_conviction_engine(settings)
    data_store = get_data_store(settings)

    if not data_store.exists:
        return DeepDipScanResponse(alerts=[], total_analyzed=0, as_of_date=today.isoformat())

    all_tickers = data_store.get_all_tickers()
    equity_tickers = ticker_meta_store.filter_equity_only(all_tickers) if ticker_meta_store.exists else all_tickers

    min_cap = settings.min_market_cap_millions * 1_000_000
    market_caps = ticker_meta_store.get_market_caps(equity_tickers) if ticker_meta_store.exists else {}
    sectors = ticker_meta_store.get_sectors(equity_tickers) if ticker_meta_store.exists else {}
    names = ticker_meta_store.get_names(equity_tickers) if ticker_meta_store.exists else {}

    filtered_tickers = [
        t for t in equity_tickers
        if market_caps.get(t, 0) >= min_cap or market_caps.get(t, 0) == 0
    ]

    ticker_data = data_store.read_tickers(filtered_tickers)
    signals_list = conviction_engine.analyze_batch(ticker_data, requested_tickers=filtered_tickers)
    signal_map = {s.ticker: s for s in signals_list}
    total_analyzed = len(signal_map)

    oversold_signals = {
        t: s for t, s in signal_map.items()
        if s.trend_state in (TrendState.OVERSOLD_21EMA, TrendState.OVERSOLD_50EMA)
    }
    total_oversold = len(oversold_signals)

    market_ctx = _compute_market_context(
        all_signals=signal_map,
        oversold_count=total_oversold,
        total_count=total_analyzed,
        data_store=data_store,
    )

    if not oversold_signals:
        return DeepDipScanResponse(
            alerts=[], total_analyzed=total_analyzed, total_oversold=0,
            total_actionable=0, market_context=market_ctx,
            as_of_date=today.isoformat(),
        )

    news_map: dict[str, dict] = {}
    filing_map: dict[str, dict] = {}
    try:
        raw_news = await get_all_signals()
        news_map = {s["ticker"]: s for s in raw_news if "ticker" in s}
    except Exception:
        logger.warning("deep_dip_news_signals_failed", exc_info=True)
    try:
        raw_filings = await get_all_filing_signals()
        filing_map = {s["ticker"]: s for s in raw_filings if "ticker" in s}
    except Exception:
        logger.warning("deep_dip_filing_signals_failed", exc_info=True)

    classifier = DipCatalystClassifier()
    inst_tickers = list(oversold_signals.keys())
    inst_map: dict[str, float] = {}
    if ticker_meta_store.exists:
        inst_map = ticker_meta_store.get_institutional_pcts(inst_tickers)
    inst_cached = get_cached_ownership_batch(inst_tickers)
    inst_map = {**inst_map, **inst_cached}

    alerts = detect_pullback_alerts(
        oversold_signals,
        institutional_map=inst_map,
        min_institutional_pct=0.0,
        dip_classifier=classifier,
        news_signals=news_map,
        filing_signals=filing_map,
    )

    response_alerts: list[DeepDipAlertResponse] = []
    for alert in alerts:
        dip_class_resp = None
        if alert.dip_classification:
            dc = alert.dip_classification
            dip_class_resp = DipClassificationResponse(
                catalyst=dc.catalyst.value,
                risk_level=dc.risk_level.value,
                reasons=dc.reasons,
                actionable=dc.actionable,
                news_impact_score=dc.news_impact_score,
                negative_news_count=dc.negative_news_count,
                insider_cluster_sell=dc.insider_cluster_sell,
                last_8k_impact=dc.last_8k_impact,
            )

        cap = market_caps.get(alert.ticker)
        cap_b = (cap / 1e9) if cap else 0

        recovery_sig = _assess_recovery_signal(
            alert=alert,
            market_ctx=market_ctx,
            market_cap_b=cap_b,
        )

        response_alerts.append(DeepDipAlertResponse(
            ticker=alert.ticker,
            alert_type=alert.alert_type,
            severity=alert.severity,
            trend_state=alert.trend_state.value if hasattr(alert.trend_state, "value") else str(alert.trend_state),
            conviction_level=alert.conviction_level,
            last_close=alert.last_close,
            ema_8=alert.ema_8,
            ema_21=alert.ema_21,
            ema_50=alert.ema_50,
            ema_8_slope=alert.ema_8_slope,
            ema_21_slope=alert.ema_21_slope,
            ema_50_slope=alert.ema_50_slope,
            rsi_14=alert.rsi_14,
            prior_streak=getattr(alert, "prior_streak", 0),
            dip_pct=alert.dip_classification.dip_pct if alert.dip_classification else 0.0,
            price_to_21ema_pct=round(((alert.last_close - alert.ema_21) / alert.ema_21 * 100) if alert.ema_21 else 0, 2),
            price_to_50ema_pct=round(((alert.last_close - alert.ema_50) / alert.ema_50 * 100) if alert.ema_50 else 0, 2),
            iv_rank=alert.iv_rank,
            vrp=alert.vrp,
            conviction_score=alert.conviction_score,
            volume_declining=alert.volume_declining,
            institutional_pct=inst_map.get(alert.ticker),
            suggested_action=alert.suggested_action,
            position_size_hint=alert.position_size_hint,
            stop_loss_level=alert.stop_loss_level,
            market_cap=cap,
            market_cap_label=_market_cap_label(cap),
            sector=sectors.get(alert.ticker),
            name=names.get(alert.ticker, ""),
            dip_classification=dip_class_resp,
            recovery_signal=recovery_sig,
            detected_at=alert.detected_at.isoformat(),
        ))

    actionable_count = sum(1 for a in response_alerts if a.recovery_signal and a.recovery_signal.actionable)

    response_alerts.sort(key=lambda a: (
        0 if (a.recovery_signal and a.recovery_signal.actionable) else 1,
        0 if a.severity == "high" else 1,
        -(a.prior_streak or 0),
        -(a.dip_pct or 0),
    ))

    result = DeepDipScanResponse(
        alerts=response_alerts,
        total_analyzed=total_analyzed,
        total_oversold=total_oversold,
        total_actionable=actionable_count,
        market_context=market_ctx,
        as_of_date=today.isoformat(),
    )
    _deep_dip_cache[cache_key] = result
    logger.info("deep_dip_cached", as_of_date=cache_key, alerts=len(response_alerts))
    return result


def _compute_market_context(
    all_signals: dict,
    oversold_count: int,
    total_count: int,
    data_store: OHLCVStore,
) -> MarketContextResponse:
    """Compute market-wide context for dip assessment."""
    import numpy as np

    breadth = oversold_count / total_count if total_count > 0 else 0.0
    is_broad = oversold_count >= 100

    spy_ret_5d: float | None = None
    spy_dd: float | None = None
    spy_rsi: float | None = None

    try:
        spy_df = data_store.read_ticker("SPY")
        if spy_df is not None and len(spy_df) >= 50:
            close = spy_df["close"].astype(float)
            spy_ret_5d = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) > 5 else None
            rolling_high = close.rolling(50, min_periods=10).max()
            spy_dd = float((close.iloc[-1] - rolling_high.iloc[-1]) / rolling_high.iloc[-1] * 100)

            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else np.inf
            spy_rsi = float(100.0 - (100.0 / (1.0 + rs))) if not np.isinf(rs) else 100.0
    except Exception:
        logger.warning("deep_dip_spy_context_failed", exc_info=True)

    return MarketContextResponse(
        concurrent_dips=oversold_count,
        total_universe=total_count,
        market_dip_breadth=round(breadth, 4),
        spy_return_5d=round(spy_ret_5d, 2) if spy_ret_5d is not None else None,
        spy_drawdown_from_high=round(spy_dd, 2) if spy_dd is not None else None,
        spy_rsi_14=round(spy_rsi, 1) if spy_rsi is not None else None,
        is_broad_selloff=is_broad,
    )


def _assess_recovery_signal(
    alert,
    market_ctx: MarketContextResponse,
    market_cap_b: float,
) -> RecoverySignalResponse:
    """Apply backtest-validated thresholds to assess recovery probability.

    Validated on 176K deep dip rows (2015-2026, $1B+ cap, 60%+ inst):
      - Broad selloff (100+ concurrent) + RSI 30-45 + slope > -0.5: 55% R20d, 73% R40d
      - Adding $20B+ cap + SPY beta: 58% R20d, 75% R40d
      - Without broad selloff context: ~42% R20d (coin flip — avoid)
    """
    checks: list[str] = []
    rsi = alert.rsi_14
    slope_21 = alert.ema_21_slope
    is_broad = market_ctx.is_broad_selloff

    rsi_ok = 30 <= rsi <= 50
    checks.append(f"{'PASS' if rsi_ok else 'FAIL'}: RSI {rsi:.0f} (need 30-50)")

    slope_ok = slope_21 > -0.5
    checks.append(f"{'PASS' if slope_ok else 'FAIL'}: 21-EMA slope {slope_21:.2f} (need > -0.5)")

    checks.append(f"{'PASS' if is_broad else 'FAIL'}: Broad selloff ({market_ctx.concurrent_dips} dips, need 100+)")

    cap_ok = market_cap_b >= 20
    checks.append(f"{'PASS' if cap_ok else 'FAIL'}: Market cap ${market_cap_b:.0f}B (need $20B+)")

    dip_class_ok = True
    if alert.dip_classification:
        dc = alert.dip_classification
        if hasattr(dc, "actionable"):
            dip_class_ok = dc.actionable
        elif hasattr(dc, "risk_level"):
            rl = dc.risk_level if isinstance(dc.risk_level, str) else dc.risk_level.value
            dip_class_ok = rl in ("low", "medium")
    checks.append(f"{'PASS' if dip_class_ok else 'FAIL'}: Dip classification risk")

    meets_all = rsi_ok and slope_ok and is_broad and cap_ok and dip_class_ok
    actionable = rsi_ok and slope_ok and dip_class_ok and (is_broad or cap_ok)

    if meets_all:
        r20 = "~55-58%"
        r40 = "~73-75%"
        peak = "median 8.5% from dip low within 20d"
        cc_dte = "14-30 DTE, strike near 21-EMA"
    elif actionable:
        r20 = "~45-52%"
        r40 = "~60-70%"
        peak = "median 6-7% from dip low within 20d"
        cc_dte = "21-45 DTE, strike conservative (below 21-EMA)"
    else:
        r20 = "~42% (baseline — not compelling)"
        r40 = "~58%"
        peak = ""
        cc_dte = ""

    return RecoverySignalResponse(
        actionable=actionable,
        recovery_20d_est=r20,
        recovery_40d_est=r40,
        meets_all_thresholds=meets_all,
        threshold_checks=checks,
        suggested_cc_dte=cc_dte,
        peak_recovery_est=peak,
    )


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
    from tyche.api.deps import get_polygon
    from tyche.market_data.data_store import bootstrap_ohlcv

    settings = get_settings()
    polygon = get_polygon(settings)
    if polygon is None:
        from fastapi import HTTPException

        raise HTTPException(
            status_code=503,
            detail="Polygon API key not configured",
        )

    result = await bootstrap_ohlcv(
        polygon, data_store, days=5,
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


@router.post("/positions/bulk")
async def bulk_import_positions(
    req: BulkPositionRequest,
) -> BulkPositionResponse:
    """Import multiple stock positions at once (e.g., from localStorage migration)."""
    from tyche.persistence.position_repository import (
        create_position as repo_create,
        get_active_positions,
    )

    existing = await get_active_positions()
    existing_tickers = {p.ticker for p in existing}

    created = 0
    skipped = 0
    errors: list[str] = []

    for item in req.positions:
        ticker = item.ticker.upper()

        if req.skip_duplicates and ticker in existing_tickers:
            skipped += 1
            continue

        try:
            purchase_date_val = (
                date.fromisoformat(item.purchase_date)
                if item.purchase_date
                else date.today()
            )
            await repo_create(
                ticker=ticker,
                purchase_price=item.purchase_price,
                quantity=item.quantity,
                purchase_date=purchase_date_val,
                pullback_type=item.pullback_type,
            )
            existing_tickers.add(ticker)
            created += 1
        except Exception as exc:
            errors.append(f"{ticker}: {exc}")

    logger.info(
        "bulk_positions_imported",
        created=created,
        skipped=skipped,
        errors=len(errors),
    )
    return BulkPositionResponse(created=created, skipped=skipped, errors=errors)


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
