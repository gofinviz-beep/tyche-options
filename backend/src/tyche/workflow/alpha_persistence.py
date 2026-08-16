"""Directional-Alpha PERSISTENCE compute.

The nightly alpha batch now appends a dated snapshot per session
(``alpha_history/{variant}/{date}.parquet``). This module reads that accumulated
history and derives a per-ticker *persistence* score (0-100) that separates
consistently-ranked names from one-day wonders, overlays each name's upcoming
earnings date, and persists a compact JSON artifact for instant page loads.

Single source of truth: ``scripts/alpha_persistence.py`` and the API route both
call into here. Reads are cheap (no feature rebuild / re-scoring) — the heavy
work already happened in the nightly alpha batch.

Artifact: ``signals/alpha/persistence_{variant}.json`` (GCS or local).
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from datetime import date, datetime, timezone
from typing import Any

import numpy as np
import pandas as pd
import structlog

from tyche.config import TycheSettings
from tyche.exceptions import DataStoreError
from tyche.schemas.alpha import (
    AlphaPersistenceGem,
    AlphaPersistenceResponse,
    AlphaTrendSeries,
)
from tyche.storage import read_json, write_json
from tyche.storage.paths import StorageContext, storage_context_from_settings

logger = structlog.get_logger()

_BUY = {"strong_buy", "buy"}
_HCOLS = ["breakout_prob_swing", "breakout_prob_trend", "breakout_prob_thematic"]
_HKEY = {"swing": _HCOLS[0], "trend": _HCOLS[1], "thematic": _HCOLS[2]}


def artifact_rel_path(variant: str) -> str:
    return f"signals/alpha/persistence_{variant or 'sustained'}.json"


def _norm(x: float | None, lo: float, hi: float) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)) or hi == lo:
        return 0.0
    return float(min(1.0, max(0.0, (x - lo) / (hi - lo))))


def _run_coro(coro: Any) -> Any:
    """Run *coro* to completion whether or not a loop is already running."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
        return ex.submit(lambda: asyncio.run(coro)).result()


def scored_from_history(
    settings: TycheSettings,
    variant: str,
    sessions: int,
    *,
    ctx: StorageContext | None = None,
) -> pd.DataFrame:
    """Read the last *sessions* dated snapshots into a scored daily panel.

    Derives ``move_prob`` (horizon-appropriate breakout probability), ``rank``
    (per-date descending alpha rank), ``demand_net``, ``market_cap`` and
    ``sector`` from the persisted snapshot columns — no re-scoring. Returns an
    empty frame when history has not accumulated yet.
    """
    from tyche.market_data.alpha_store import AlphaSignalStore

    store = AlphaSignalStore(data_dir=settings.data_dir, variant=variant, ctx=ctx)
    panel = store.read_history(sessions=sessions)
    if panel.empty:
        return panel

    panel["date"] = pd.to_datetime(panel["date"]).dt.date
    for col in _HCOLS:
        if col not in panel.columns:
            panel[col] = np.nan

    def _move_prob(row: pd.Series) -> float | None:
        key = _HKEY.get(str(row.get("horizon")))
        val = row.get(key) if key else None
        if val is None or (isinstance(val, float) and np.isnan(val)):
            cand = [row.get(c) for c in _HCOLS]
            cand = [c for c in cand if c is not None and not (isinstance(c, float) and np.isnan(c))]
            val = max(cand) if cand else None
        return None if val is None else float(val)

    panel["move_prob"] = panel.apply(_move_prob, axis=1)
    panel["demand_net"] = panel["ddim_net"] if "ddim_net" in panel.columns else np.nan
    if "market_cap" not in panel.columns:
        panel["market_cap"] = np.nan
    if "sector" not in panel.columns:
        panel["sector"] = None

    keep = ["date", "ticker", "alpha_score", "signal", "horizon", "move_prob",
            "demand_net", "market_cap", "sector"]
    out = panel[keep].copy()
    out["rank"] = out.groupby("date")["alpha_score"].rank(ascending=False, method="min")
    return out.sort_values(["date", "rank"])


def persistence_table(scored: pd.DataFrame) -> pd.DataFrame:
    """Per-ticker stability metrics + composite persistence score (0-100)."""
    n_dates = max(int(scored["date"].nunique()), 1)
    rows: list[dict[str, Any]] = []
    for tkr, g in scored.groupby("ticker"):
        g = g.sort_values("date")
        alpha = g["alpha_score"].to_numpy(dtype=float)
        rank = g["rank"].to_numpy(dtype=float)
        mp = g["move_prob"].dropna().to_numpy(dtype=float)
        sigs = g["signal"].tolist()
        churn = sum(1 for a, b in zip(sigs, sigs[1:]) if a != b)
        if len(alpha) >= 3:
            slope = float(np.polyfit(np.arange(len(alpha)), alpha, 1)[0])
        else:
            slope = 0.0
        sector_vals = [s for s in g["sector"].tolist() if s]
        rows.append({
            "ticker": tkr,
            "days": len(g),
            "mean_alpha": float(np.mean(alpha)),
            "std_alpha": float(np.std(alpha)),
            "last_alpha": float(alpha[-1]),
            "mean_move_prob": float(np.mean(mp)) if mp.size else None,
            "pct_buy": float(np.mean([s in _BUY for s in sigs])),
            "pct_top100": float(np.mean(rank <= 100)),
            "mean_rank": float(np.mean(rank)),
            "std_rank": float(np.std(rank)),
            "last_rank": int(rank[-1]),
            "signal_churn": churn,
            "alpha_slope": slope,
            "last_signal": sigs[-1],
            "mean_demand_net": float(np.nanmean(g["demand_net"].to_numpy(dtype=float)))
            if g["demand_net"].notna().any() else None,
            "market_cap": float(g["market_cap"].dropna().iloc[-1])
            if g["market_cap"].notna().any() else None,
            "sector": sector_vals[-1] if sector_vals else None,
        })
    pt = pd.DataFrame(rows)
    if pt.empty:
        return pt
    pt["coverage"] = pt["days"] / n_dates

    level = 0.30 * pt["mean_alpha"].apply(lambda v: _norm(v, 40, 80))
    consistency = 0.25 * pt["pct_buy"] + 0.20 * pt["pct_top100"]
    stability = 0.15 * (1 - pt["std_rank"].apply(lambda v: _norm(v, 0, 400)))
    trend = 0.10 * pt["alpha_slope"].apply(lambda v: _norm(v, -1.0, 1.0))
    churn_pen = pt["signal_churn"].apply(lambda c: min(0.10, 0.02 * c))
    pt["persistence"] = (100 * (level + consistency + stability + trend) - 100 * churn_pen).round(1)
    return pt.sort_values("persistence", ascending=False)


async def _fetch_earnings(tickers: list[str], settings: TycheSettings) -> dict[str, dict]:
    from tyche.market_data.earnings import EarningsCalendarClient

    key = (
        getattr(settings, "alpha_vantage_api_key", None)
        or getattr(settings, "alphavantage_key", None)
        or "demo"
    )
    client = EarningsCalendarClient(alpha_vantage_key=key)
    try:
        info = await client.get_upcoming_earnings(tickers)
    except Exception as exc:  # noqa: BLE001 - overlay is best-effort
        logger.warning("alpha_persistence_earnings_failed", error=str(exc))
        info = {}
    today = date.today()
    out: dict[str, dict] = {}
    for t in tickers:
        rec = info.get(t.upper())
        ed = rec.get("earnings_date") if rec else None
        if isinstance(ed, date):
            dte = (ed - today).days
            out[t] = {
                "earnings_date": ed.isoformat(),
                "days_to_earnings": dte,
                "earnings_in_horizon": dte <= 45,
            }
        else:
            out[t] = {"earnings_date": None, "days_to_earnings": None, "earnings_in_horizon": None}
    return out


def _build_response(
    scored: pd.DataFrame,
    pt: pd.DataFrame,
    *,
    variant: str,
    top: int,
    earnings: dict[str, dict],
    source: str,
) -> AlphaPersistenceResponse:
    gems_df = pt.head(top)
    gems: list[AlphaPersistenceGem] = []
    for _, r in gems_df.iterrows():
        tkr = r["ticker"]
        g = scored[scored["ticker"] == tkr].sort_values("date")
        trend = AlphaTrendSeries(
            dates=[d.isoformat() for d in g["date"]],
            alpha=[round(float(a), 1) for a in g["alpha_score"]],
            rank=[int(x) for x in g["rank"]],
            move_prob=[None if pd.isna(m) else round(float(m), 4) for m in g["move_prob"]],
        )
        e = earnings.get(tkr, {})
        gems.append(AlphaPersistenceGem(
            ticker=tkr,
            persistence=float(r["persistence"]),
            mean_alpha=round(float(r["mean_alpha"]), 1),
            last_alpha=round(float(r["last_alpha"]), 1),
            alpha_slope=round(float(r["alpha_slope"]), 3),
            mean_move_prob=None if r["mean_move_prob"] is None else round(float(r["mean_move_prob"]), 3),
            pct_buy=round(float(r["pct_buy"]), 2),
            pct_top100=round(float(r["pct_top100"]), 2),
            mean_rank=round(float(r["mean_rank"]), 1),
            std_rank=round(float(r["std_rank"]), 1),
            last_rank=int(r["last_rank"]),
            signal_churn=int(r["signal_churn"]),
            last_signal=str(r["last_signal"]),
            mean_demand_net=None if r["mean_demand_net"] is None else round(float(r["mean_demand_net"]), 3),
            market_cap=None if r["market_cap"] is None else float(r["market_cap"]),
            sector=r.get("sector"),
            earnings_date=e.get("earnings_date"),
            days_to_earnings=e.get("days_to_earnings"),
            earnings_in_horizon=e.get("earnings_in_horizon"),
            trend=trend,
        ))
    dates = sorted(scored["date"].unique())
    return AlphaPersistenceResponse(
        generated_at=datetime.now(timezone.utc).isoformat(),
        variant=variant,
        sessions=len(dates),
        date_range=[str(dates[0]), str(dates[-1])] if dates else [],
        universe_size=int(pt.shape[0]),
        total=len(gems),
        source=source,
        gems=gems,
    )


def response_from_scored(
    scored: pd.DataFrame,
    *,
    variant: str,
    top: int,
    settings: TycheSettings,
    include_earnings: bool = True,
    source: str = "computed",
) -> AlphaPersistenceResponse | None:
    """Assemble the ranked response from an already-scored daily panel.

    Shared by the cheap history path and the script's feature-rebuild path so
    the persistence math + earnings overlay live in exactly one place.
    """
    if "sector" not in scored.columns:
        scored = scored.assign(sector=None)
    pt = persistence_table(scored)
    if pt.empty:
        return None
    earnings: dict[str, dict] = {}
    if include_earnings:
        earnings = _run_coro(_fetch_earnings(pt.head(top)["ticker"].tolist(), settings))
    return _build_response(
        scored, pt, variant=variant, top=top, earnings=earnings, source=source
    )


def compute_persistence(
    settings: TycheSettings,
    *,
    variant: str = "sustained",
    sessions: int = 30,
    top: int = 100,
    include_earnings: bool = True,
    ctx: StorageContext | None = None,
) -> AlphaPersistenceResponse | None:
    """Compute the persistence read from accumulated alpha history.

    Returns ``None`` when no dated snapshots exist yet (history hasn't
    accumulated). Otherwise returns the ranked gems + trend series.
    """
    ctx = ctx or storage_context_from_settings(settings)
    scored = scored_from_history(settings, variant, sessions, ctx=ctx)
    if scored.empty:
        logger.info("alpha_persistence_no_history", variant=variant)
        return None
    scored = scored[scored["date"].isin(sorted(scored["date"].unique())[-sessions:])].copy()
    return response_from_scored(
        scored,
        variant=variant,
        top=top,
        settings=settings,
        include_earnings=include_earnings,
    )


def persist_response(
    resp: AlphaPersistenceResponse,
    settings: TycheSettings,
    *,
    ctx: StorageContext | None = None,
) -> str:
    """Write the persistence artifact to ``signals/alpha/persistence_{variant}.json``."""
    ctx = ctx or storage_context_from_settings(settings)
    rel = artifact_rel_path(resp.variant)
    write_json(resp.model_dump(mode="json"), rel, atomic=True, ctx=ctx)
    logger.info("alpha_persistence_written", variant=resp.variant, gems=resp.total, rel=rel)
    return rel


def load_persisted(
    settings: TycheSettings,
    variant: str,
    *,
    ctx: StorageContext | None = None,
) -> AlphaPersistenceResponse | None:
    """Read the persisted persistence artifact for *variant*, or None if absent."""
    ctx = ctx or storage_context_from_settings(settings)
    rel = artifact_rel_path(variant)
    try:
        raw = read_json(rel, ctx=ctx)
    except DataStoreError:
        return None
    if not isinstance(raw, dict):
        return None
    resp = AlphaPersistenceResponse.model_validate(raw)
    resp.source = "published"
    return resp


def run_alpha_persistence(
    settings: TycheSettings,
    *,
    variants: list[str] | None = None,
    sessions: int = 30,
    top: int = 100,
) -> dict[str, Any]:
    """Batch entry: compute + persist the persistence artifact for each variant."""
    variants = variants or ["sustained"]
    ctx = storage_context_from_settings(settings)
    out: dict[str, Any] = {"status": "ok", "variants": {}}
    for variant in variants:
        resp = compute_persistence(
            settings, variant=variant, sessions=sessions, top=top, ctx=ctx
        )
        if resp is None:
            out["variants"][variant] = {"status": "no_history"}
            continue
        persist_response(resp, settings, ctx=ctx)
        out["variants"][variant] = {
            "status": "ok",
            "gems": resp.total,
            "sessions": resp.sessions,
            "universe_size": resp.universe_size,
        }
    return out
