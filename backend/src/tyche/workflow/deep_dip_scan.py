"""Deep dip oversold scan — batch-safe workflow extracted from the API route."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import structlog

from tyche.config import TycheSettings
from tyche.conviction.alerts import detect_pullback_alerts
from tyche.conviction.dip_classifier import DipCatalystClassifier
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal, TrendState
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.institutional import get_cached_ownership_batch
from tyche.market_data.stocks_conviction_store import load_stocks_conviction_parquet
from tyche.schemas.alerts import (
    DeepDipAlertResponse,
    DeepDipScanResponse,
    DipClassificationResponse,
    MarketContextResponse,
    RecoverySignalResponse,
)
from tyche.schemas.stocks import ConvictionSnapshotResponse
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()


def _market_cap_label(market_cap: float | None) -> str:
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


def compute_market_context(
    all_signals: dict[str, ConvictionSignal],
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
            spy_ret_5d = (
                float((close.iloc[-1] / close.iloc[-6] - 1) * 100)
                if len(close) > 5
                else None
            )
            rolling_high = close.rolling(50, min_periods=10).max()
            spy_dd = float(
                (close.iloc[-1] - rolling_high.iloc[-1]) / rolling_high.iloc[-1] * 100
            )

            delta = close.diff()
            gain = delta.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
            loss = (-delta.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
            rs = gain.iloc[-1] / loss.iloc[-1] if loss.iloc[-1] != 0 else np.inf
            spy_rsi = (
                float(100.0 - (100.0 / (1.0 + rs))) if not np.isinf(rs) else 100.0
            )
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


def assess_recovery_signal(
    alert,
    market_ctx: MarketContextResponse,
    market_cap_b: float,
) -> RecoverySignalResponse:
    """Apply backtest-validated thresholds to assess recovery probability."""
    checks: list[str] = []
    rsi = alert.rsi_14
    slope_21 = alert.ema_21_slope
    is_broad = market_ctx.is_broad_selloff

    rsi_ok = 30 <= rsi <= 50
    checks.append(f"{'PASS' if rsi_ok else 'FAIL'}: RSI {rsi:.0f} (need 30-50)")

    slope_ok = slope_21 > -0.5
    checks.append(
        f"{'PASS' if slope_ok else 'FAIL'}: 21-EMA slope {slope_21:.2f} (need > -0.5)"
    )

    checks.append(
        f"{'PASS' if is_broad else 'FAIL'}: Broad selloff "
        f"({market_ctx.concurrent_dips} dips, need 100+)"
    )

    cap_ok = market_cap_b >= 20
    checks.append(
        f"{'PASS' if cap_ok else 'FAIL'}: Market cap ${market_cap_b:.0f}B (need $20B+)"
    )

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
        r20, r40, peak, cc_dte = "~55-58%", "~73-75%", "median 8.5% from dip low within 20d", "14-30 DTE, strike near 21-EMA"
    elif actionable:
        r20, r40, peak, cc_dte = "~45-52%", "~60-70%", "median 6-7% from dip low within 20d", "21-45 DTE, strike conservative (below 21-EMA)"
    else:
        r20, r40, peak, cc_dte = "~42% (baseline — not compelling)", "~58%", "", ""

    return RecoverySignalResponse(
        actionable=actionable,
        recovery_20d_est=r20,
        recovery_40d_est=r40,
        meets_all_thresholds=meets_all,
        threshold_checks=checks,
        suggested_cc_dte=cc_dte,
        peak_recovery_est=peak,
    )


def _snapshot_to_signal(snap: ConvictionSnapshotResponse) -> ConvictionSignal:
    from tyche.conviction.engine import TrendState

    trend = TrendState(snap.trend_state)
    return ConvictionSignal(
        ticker=snap.ticker,
        trend_state=trend,
        conviction_level=snap.conviction_level,
        raw_conviction=snap.raw_conviction,
        csp_eligible=snap.csp_eligible,
        last_close=snap.last_close,
        ema_8=snap.ema_8,
        ema_21=snap.ema_21,
        ema_8_slope=snap.ema_8_slope,
        ema_21_slope=snap.ema_21_slope,
        price_to_8ema_pct=snap.price_to_8ema_pct,
        price_to_21ema_pct=snap.price_to_21ema_pct,
        volume_declining_on_pullback=snap.volume_declining,
        days_above_both_emas=snap.days_above_both_emas,
        prior_streak=getattr(snap, "prior_streak", 0) or 0,
        avg_volume_20d=snap.avg_volume_20d,
        latest_volume=snap.latest_volume,
        ema_50=snap.ema_50,
        ema_50_slope=snap.ema_50_slope,
        rsi_14=snap.rsi_14,
        iv_rank=snap.iv_rank,
        iv_percentile=snap.iv_percentile,
        atm_iv=snap.atm_iv,
        vrp=snap.vrp,
        conviction_score=snap.conviction_score,
        csp_safety_prob=snap.csp_safety_prob,
        as_of_date=date.fromisoformat(snap.as_of_date) if snap.as_of_date else None,
        gate_results=[],
    )


async def _load_news_and_filing_maps() -> tuple[dict[str, dict], dict[str, dict]]:
    from tyche.market_data.filing_signals import get_all_filing_signals
    from tyche.market_data.news_signals import get_all_signals

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
    return news_map, filing_map


def _resolve_universe_tickers(
    data_store: OHLCVStore,
    ticker_meta_store: TickerMetaStore,
    settings: TycheSettings,
) -> list[str]:
    all_tickers = data_store.get_all_tickers()
    equity_tickers = (
        ticker_meta_store.filter_equity_only(all_tickers)
        if ticker_meta_store.exists
        else all_tickers
    )
    min_cap = settings.min_market_cap_millions * 1_000_000
    market_caps = (
        ticker_meta_store.get_market_caps(equity_tickers)
        if ticker_meta_store.exists
        else {}
    )
    return [
        t
        for t in equity_tickers
        if market_caps.get(t, 0) >= min_cap or market_caps.get(t, 0) == 0
    ]


def _load_signal_map(
    *,
    filtered_tickers: list[str],
    conviction_engine: ConvictionEngine | None,
    data_store: OHLCVStore,
    ctx: StorageContext | None,
) -> dict[str, ConvictionSignal]:
    if ctx is not None:
        rows, _as_of = load_stocks_conviction_parquet(ctx=ctx)
        if rows:
            filtered = {r.ticker for r in rows}.intersection(filtered_tickers)
            return {
                r.ticker: _snapshot_to_signal(r)
                for r in rows
                if r.ticker in filtered
            }

    if conviction_engine is None or not data_store.exists:
        return {}

    ticker_data = data_store.read_tickers(filtered_tickers)
    signals_list = conviction_engine.analyze_batch(
        ticker_data,
        requested_tickers=filtered_tickers,
    )
    return {s.ticker: s for s in signals_list}


async def run_deep_dip_scan(
    *,
    settings: TycheSettings,
    data_store: OHLCVStore,
    ticker_meta_store: TickerMetaStore,
    conviction_engine: ConvictionEngine | None = None,
    ctx: StorageContext | None = None,
    as_of_date: date | None = None,
) -> DeepDipScanResponse:
    """Scan for oversold deep dip candidates suitable for buy + covered call."""
    today = as_of_date or date.today()

    if not data_store.exists:
        return DeepDipScanResponse(
            alerts=[], total_analyzed=0, as_of_date=today.isoformat()
        )

    filtered_tickers = _resolve_universe_tickers(
        data_store, ticker_meta_store, settings
    )
    signal_map = _load_signal_map(
        filtered_tickers=filtered_tickers,
        conviction_engine=conviction_engine,
        data_store=data_store,
        ctx=ctx,
    )
    total_analyzed = len(signal_map)

    oversold_signals = {
        t: s
        for t, s in signal_map.items()
        if s.trend_state in (TrendState.OVERSOLD_21EMA, TrendState.OVERSOLD_50EMA)
    }
    total_oversold = len(oversold_signals)

    market_ctx = compute_market_context(
        all_signals=signal_map,
        oversold_count=total_oversold,
        total_count=total_analyzed,
        data_store=data_store,
    )

    if not oversold_signals:
        return DeepDipScanResponse(
            alerts=[],
            total_analyzed=total_analyzed,
            total_oversold=0,
            total_actionable=0,
            market_context=market_ctx,
            as_of_date=today.isoformat(),
        )

    news_map, filing_map = await _load_news_and_filing_maps()
    classifier = DipCatalystClassifier()

    inst_tickers = list(oversold_signals.keys())
    inst_map: dict[str, float] = {}
    if ticker_meta_store.exists:
        inst_map = ticker_meta_store.get_institutional_pcts(inst_tickers)
    inst_cached = get_cached_ownership_batch(inst_tickers)
    inst_map = {**inst_map, **inst_cached}

    market_caps = (
        ticker_meta_store.get_market_caps(inst_tickers)
        if ticker_meta_store.exists
        else {}
    )
    sectors = (
        ticker_meta_store.get_sectors(inst_tickers) if ticker_meta_store.exists else {}
    )
    names = (
        ticker_meta_store.get_names(inst_tickers) if ticker_meta_store.exists else {}
    )

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
        recovery_sig = assess_recovery_signal(
            alert=alert,
            market_ctx=market_ctx,
            market_cap_b=cap_b,
        )

        response_alerts.append(
            DeepDipAlertResponse(
                ticker=alert.ticker,
                alert_type=alert.alert_type,
                severity=alert.severity,
                trend_state=alert.trend_state.value
                if hasattr(alert.trend_state, "value")
                else str(alert.trend_state),
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
                dip_pct=alert.dip_classification.dip_pct
                if alert.dip_classification
                else 0.0,
                price_to_21ema_pct=round(
                    ((alert.last_close - alert.ema_21) / alert.ema_21 * 100)
                    if alert.ema_21
                    else 0,
                    2,
                ),
                price_to_50ema_pct=round(
                    ((alert.last_close - alert.ema_50) / alert.ema_50 * 100)
                    if alert.ema_50
                    else 0,
                    2,
                ),
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
            )
        )

    actionable_count = sum(
        1
        for a in response_alerts
        if a.recovery_signal and a.recovery_signal.actionable
    )

    response_alerts.sort(
        key=lambda a: (
            0 if (a.recovery_signal and a.recovery_signal.actionable) else 1,
            0 if a.severity == "high" else 1,
            -(a.prior_streak or 0),
            -(a.dip_pct or 0),
        )
    )

    return DeepDipScanResponse(
        alerts=response_alerts,
        total_analyzed=total_analyzed,
        total_oversold=total_oversold,
        total_actionable=actionable_count,
        market_context=market_ctx,
        as_of_date=today.isoformat(),
    )
