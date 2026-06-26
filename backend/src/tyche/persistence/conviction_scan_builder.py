"""Build options conviction scan responses from cloud snapshot rows."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from tyche.schemas.conviction import (
    ConvictionScanResponse,
    ConvictionSignalResponse,
    TrendSummary,
)
from tyche.schemas.stocks import ConvictionSnapshotResponse

_PULLBACK_STATES = frozenset({"pullback_to_8ema", "pullback_to_21ema"})
_UPTREND_STATES = frozenset({"strong_uptrend", "uptrend"})
_CONVICTION_ORDER = {"high": 0, "medium": 1, "low": 2, "none": 3}


def snapshot_row_to_signal(
    row: ConvictionSnapshotResponse,
    *,
    is_watchlist: bool = False,
) -> ConvictionSignalResponse:
    """Map a cloud conviction snapshot row to the options API signal shape."""
    return ConvictionSignalResponse(
        ticker=row.ticker,
        trend_state=row.trend_state,
        conviction_level=row.conviction_level,
        csp_eligible=row.csp_eligible,
        is_watchlist=is_watchlist,
        last_close=round(row.last_close, 2),
        ema_8=round(row.ema_8, 4),
        ema_21=round(row.ema_21, 4),
        ema_8_slope=round(row.ema_8_slope, 6),
        ema_21_slope=round(row.ema_21_slope, 6),
        price_to_8ema_pct=round(row.price_to_8ema_pct, 2),
        price_to_21ema_pct=round(row.price_to_21ema_pct, 2),
        volume_declining_on_pullback=row.volume_declining,
        avg_volume_20d=row.avg_volume_20d,
        latest_volume=row.latest_volume,
        days_above_both_emas=row.days_above_both_emas,
        prior_streak=row.prior_streak,
        as_of_date=row.as_of_date,
        ema_50=round(row.ema_50, 4),
        ema_50_slope=round(row.ema_50_slope, 6),
        rsi_14=round(row.rsi_14, 2),
        iv_rank=round(row.iv_rank, 1) if row.iv_rank is not None else None,
        iv_percentile=(
            round(row.iv_percentile, 1) if row.iv_percentile is not None else None
        ),
        atm_iv=round(row.atm_iv, 4) if row.atm_iv is not None else None,
        vrp=round(row.vrp, 4) if row.vrp is not None else None,
        conviction_score=round(row.conviction_score, 3),
        csp_safety_prob=(
            round(row.csp_safety_prob, 4) if row.csp_safety_prob is not None else None
        ),
        market_cap=row.market_cap,
        institutional_pct=row.institutional_pct,
        sector=row.sector,
        gate_results=[],
    )


def build_conviction_scan_response(
    rows: list[ConvictionSnapshotResponse],
    *,
    limit_per_path: int,
    watchlist_set: frozenset[str] | None = None,
    scan_id: str | None = None,
    scanned_at: str | None = None,
    specific_tickers: frozenset[str] | None = None,
) -> ConvictionScanResponse:
    """Aggregate snapshot rows into the options conviction scan envelope."""
    watchlist = watchlist_set or frozenset()
    working = rows
    if specific_tickers:
        working = [r for r in rows if r.ticker in specific_tickers]

    trend_counts: dict[str, int] = {}
    eligible: list[ConvictionSnapshotResponse] = []
    pullback_all: list[ConvictionSnapshotResponse] = []
    pullback_eligible: list[ConvictionSnapshotResponse] = []
    uptrend_eligible: list[ConvictionSnapshotResponse] = []

    for row in working:
        trend_counts[row.trend_state] = trend_counts.get(row.trend_state, 0) + 1
        if row.csp_eligible:
            eligible.append(row)
            if row.trend_state in _PULLBACK_STATES:
                pullback_eligible.append(row)
            elif row.trend_state in _UPTREND_STATES:
                uptrend_eligible.append(row)
        if row.trend_state in _PULLBACK_STATES:
            pullback_all.append(row)

    def _sort_key(row: ConvictionSnapshotResponse) -> tuple:
        return (
            _CONVICTION_ORDER.get(row.conviction_level, 99),
            -(row.prior_streak or 0),
            -(row.days_above_both_emas or 0),
        )

    if specific_tickers:
        display_rows = working
    else:
        pullback_not_eligible = [r for r in pullback_all if not r.csp_eligible]
        display_rows = (
            sorted(pullback_eligible, key=_sort_key)[:limit_per_path]
            + sorted(uptrend_eligible, key=_sort_key)[:limit_per_path]
            + sorted(pullback_not_eligible, key=_sort_key)[:limit_per_path]
        )

    signals = [
        snapshot_row_to_signal(
            row,
            is_watchlist=row.ticker in watchlist,
        )
        for row in display_rows
    ]

    return ConvictionScanResponse(
        scan_id=scan_id or str(uuid.uuid4()),
        scanned_at=scanned_at or datetime.now(timezone.utc).isoformat(),
        total_screened=len(working),
        eligible_count=len(eligible),
        uptrend_eligible=len(uptrend_eligible),
        pullback_eligible=len(pullback_eligible),
        pullback_count=len(pullback_all),
        trend_summary=TrendSummary(**{
            k: trend_counts.get(k, 0) for k in TrendSummary.model_fields
        }),
        signals=signals,
    )
