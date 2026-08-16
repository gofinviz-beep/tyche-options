"""v3 Stock Screener route — "Diamond Finder".

Pure read over the pre-computed ``screener_index.parquet`` snapshot (nightly
batch) via the published-JSON convention. No inline compute — cloud mode
just serves whatever the index/published artifact currently has.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pandas as pd
import structlog
from fastapi import APIRouter, Depends, Query

from tyche.api.deps import get_settings
from tyche.config import TycheSettings
from tyche.persistence.published_routes import get_stocks_screener_scan
from tyche.schemas.screener import ScreenerResponse, ScreenerRow
from tyche.storage.json_io import sanitize_json_records

logger = structlog.get_logger()
router = APIRouter(prefix="/stocks", tags=["screener"])


@router.get("/screener", response_model=ScreenerResponse)
async def get_screener(
    q_rsi_min: float | None = Query(default=None, description="Quarterly RSI floor"),
    q_rsi_max: float | None = Query(default=None, description="Quarterly RSI ceiling"),
    m_rsi_min: float | None = Query(default=None, description="Monthly RSI floor"),
    m_rsi_max: float | None = Query(default=None, description="Monthly RSI ceiling"),
    w_rsi_min: float | None = Query(default=None, description="Weekly RSI floor"),
    w_rsi_max: float | None = Query(default=None, description="Weekly RSI ceiling"),
    d_rsi_min: float | None = Query(default=None, description="Daily RSI floor"),
    d_rsi_max: float | None = Query(default=None, description="Daily RSI ceiling"),
    above_sma200: bool | None = Query(default=None, description="Filter: price above the 200-SMA"),
    stack_score_min: int | None = Query(default=None, ge=0, le=3),
    ext_max_pct: float | None = Query(
        default=None, description="Max pct_vs_ema_8 (overextension cap)"
    ),
    min_market_cap_millions: float | None = Query(default=None, ge=0.0),
    sector: str | None = Query(default=None, description="Exact sector match"),
    setup_label: str | None = Query(
        default=None, description="Comma-separated setup_label values"
    ),
    setup_score_min: float | None = Query(default=None, ge=0.0, le=100.0),
    sort: str = Query(default="setup_score"),
    desc: bool = Query(default=True),
    limit: int = Query(default=200, ge=1, le=5000),
    settings: TycheSettings = Depends(get_settings),
) -> ScreenerResponse:
    """Filter/sort the universe-wide screener index."""
    loaded = await asyncio.to_thread(get_stocks_screener_scan, settings=settings)
    if loaded is None:
        return ScreenerResponse(
            scanned_at=datetime.now(timezone.utc).isoformat(),
            total=0,
            stale=True,
            rows=[],
        )

    scan, _layer = loaded
    if not scan.rows:
        return scan.model_copy(update={"stale": True})

    df = pd.DataFrame([r.model_dump() for r in scan.rows])

    if q_rsi_min is not None:
        df = df[df["rsi_quarterly"] >= q_rsi_min]
    if q_rsi_max is not None:
        df = df[df["rsi_quarterly"] <= q_rsi_max]
    if m_rsi_min is not None:
        df = df[df["rsi_monthly"] >= m_rsi_min]
    if m_rsi_max is not None:
        df = df[df["rsi_monthly"] <= m_rsi_max]
    if w_rsi_min is not None:
        df = df[df["rsi_weekly"] >= w_rsi_min]
    if w_rsi_max is not None:
        df = df[df["rsi_weekly"] <= w_rsi_max]
    if d_rsi_min is not None:
        df = df[df["rsi_daily"] >= d_rsi_min]
    if d_rsi_max is not None:
        df = df[df["rsi_daily"] <= d_rsi_max]
    if above_sma200 is not None:
        df = df[df["above_sma_200"] == above_sma200]
    if stack_score_min is not None:
        df = df[df["stack_score"] >= stack_score_min]
    if ext_max_pct is not None:
        df = df[df["pct_vs_ema_8"] <= ext_max_pct]
    if min_market_cap_millions is not None and min_market_cap_millions > 0:
        floor = min_market_cap_millions * 1_000_000
        df = df[df["market_cap"].fillna(0) >= floor]
    if sector:
        df = df[df["sector"] == sector]
    if setup_label:
        labels = {s.strip() for s in setup_label.split(",") if s.strip()}
        if labels:
            df = df[df["setup_label"].isin(labels)]
    if setup_score_min is not None:
        df = df[df["setup_score"] >= setup_score_min]

    sort_col = sort if sort in df.columns else "setup_score"
    if not df.empty:
        df = df.sort_values(sort_col, ascending=not desc, na_position="last")

    total = len(df)
    df = df.head(limit)
    records = sanitize_json_records(df.to_dict(orient="records"))
    rows = [ScreenerRow.model_validate(r) for r in records]

    return ScreenerResponse(
        scanned_at=datetime.now(timezone.utc).isoformat(),
        as_of_date=scan.as_of_date,
        computed_at=scan.computed_at,
        total=total,
        stale=False,
        rows=rows,
    )
