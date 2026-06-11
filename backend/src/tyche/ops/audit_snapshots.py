"""Estimate snapshot cadence audit for the nightly GCP job (GCP-F/G)."""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

import pandas as pd
import structlog

from tyche.config import TycheSettings, get_settings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.market_data.estimate_snapshot_store import EstimateSnapshotStore
from tyche.ops.run_manifest import RunManifest, new_run_id
from tyche.storage import write_json
from tyche.storage.paths import StorageContext, join_uri, storage_context_from_settings

logger = structlog.get_logger()

_HORIZONS = (7, 14, 30, 90)
_DEFAULT_AUDIT_TICKERS = ("MU", "AVGO", "STX", "NVDA", "AMD", "MSFT", "GOOGL")


def _audit_ticker(
    store: EstimateSnapshotStore,
    ticker: str,
    as_of: date,
) -> dict[str, Any]:
    df = store.read_ticker(ticker, as_of=as_of)
    if df.empty:
        return {
            "ticker": ticker,
            "snapshot_dates": 0,
            "root_cause": "metric_missing",
        }

    out: dict[str, Any] = {
        "ticker": ticker,
        "snapshot_dates": int(df["snapshot_date"].nunique()),
        "metrics": sorted(df["metric"].unique().tolist()),
        "periods_with_multiple_snapshots": 0,
        "same_period_prior_at_horizons": {str(h): 0 for h in _HORIZONS},
    }

    for metric in df["metric"].unique():
        mdf = df[df["metric"] == metric]
        for _, grp in mdf.groupby("period"):
            snaps = sorted(grp["snapshot_date"].unique())
            if len(snaps) > 1:
                out["periods_with_multiple_snapshots"] += 1
            latest = grp[grp["snapshot_date"] == snaps[-1]].iloc[0]
            est_now = latest.get("estimate_avg")
            for h in _HORIZONS:
                prior_cut = as_of - timedelta(days=h)
                prior_rows = grp[grp["snapshot_date"] <= prior_cut]
                if prior_rows.empty or est_now is None or pd.isna(est_now):
                    continue
                prior_val = prior_rows.iloc[-1].get("estimate_avg")
                if prior_val is not None and not pd.isna(prior_val):
                    out["same_period_prior_at_horizons"][str(h)] += 1

    if out["snapshot_dates"] < 2:
        out["root_cause"] = "too_few_snapshots_for_revision"
    elif out["periods_with_multiple_snapshots"] == 0:
        out["root_cause"] = "same_period_prior_missing"
    elif any(v > 0 for v in out["same_period_prior_at_horizons"].values()):
        out["root_cause"] = "revision_populated"
    else:
        out["root_cause"] = "same_period_prior_missing"

    return out


def _pick_audit_tickers(
    settings: TycheSettings,
    ctx: StorageContext,
    *,
    sample_size: int = 20,
) -> list[str]:
    """Pick audit tickers without scanning the full OHLCV prefix on GCS."""
    tickers = list(_DEFAULT_AUDIT_TICKERS)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)
    if not meta.exists:
        return tickers

    # On GCS, ohlcv.get_all_tickers() lists 10k+ objects and can OOM the job.
    # ticker_meta.parquet is a single file — safe to read for cap-ranked sampling.
    caps = meta.get_market_caps()
    if ctx.backend == "gcs":
        universe = meta.filter_equity_only(list(caps))
    else:
        ohlcv = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
        if not ohlcv.exists:
            universe = meta.filter_equity_only(list(caps))
        else:
            universe = meta.filter_equity_only(ohlcv.get_all_tickers())
            caps = meta.get_market_caps(universe)
    ranked = sorted(universe, key=lambda t: caps.get(t) or 0, reverse=True)

    for t in ranked[:sample_size]:
        if t not in tickers:
            tickers.append(t)
    return tickers


def _estimate_snapshot_file_count(
    store: EstimateSnapshotStore,
    ctx: StorageContext,
) -> int | None:
    """Return file count without a full prefix walk when expensive."""
    if ctx.backend == "gcs":
        # Avoid fs.find over the entire estimate_snapshots/ tree in Cloud Run.
        return None
    return len(store.get_all_tickers())


def run_audit_snapshots(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
    as_of: date | None = None,
    tickers: list[str] | None = None,
) -> dict[str, Any]:
    """Audit estimate snapshot cadence and write reports + run manifest."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    if as_of is None:
        from tyche.market_data.ingest_dates import ingest_end_date

        as_of = ingest_end_date(settings.ingest_window, job_name="audit-snapshots")

    manifest = RunManifest.start(
        job_name="audit_snapshots",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = ["estimate_snapshots/"]

    store = EstimateSnapshotStore(data_dir=settings.data_dir, ctx=ctx)
    audit_tickers = [
        t.upper()
        for t in (tickers or _pick_audit_tickers(settings, ctx))
    ]
    logger.info(
        "audit_snapshots_start",
        backend=ctx.backend,
        tickers=len(audit_tickers),
        as_of=as_of.isoformat(),
    )

    rows = [_audit_ticker(store, t, as_of) for t in audit_tickers]
    populated = sum(1 for r in rows if r.get("root_cause") == "revision_populated")
    missing = sum(1 for r in rows if r.get("snapshot_dates", 0) == 0)

    cadence_report = {
        "as_of": as_of.isoformat(),
        "tickers_audited": len(audit_tickers),
        "revision_populated": populated,
        "metric_missing": missing,
        "tickers": rows,
    }

    cadence_rel = join_uri(
        "reports",
        "estimate_snapshot_audits",
        f"{as_of.isoformat()}.json",
    )
    write_json(cadence_report, cadence_rel, atomic=True, ctx=ctx)

    health_rel = join_uri("reports", "job_health", f"audit_snapshots_{rid}.json")
    health = {
        "job": "audit_snapshots",
        "run_id": rid,
        "as_of": as_of.isoformat(),
        "estimate_snapshot_files": _estimate_snapshot_file_count(store, ctx),
        "revision_populated_rate": round(populated / max(len(rows), 1), 3),
        "cadence_report": cadence_rel,
    }
    write_json(health, health_rel, atomic=True, ctx=ctx)

    manifest.output_paths = [cadence_rel, health_rel]
    manifest.tickers_requested = len(audit_tickers)
    manifest.tickers_succeeded = len(rows) - missing
    manifest.tickers_failed = missing
    manifest.extra = {
        "revision_populated": populated,
        "metric_missing": missing,
    }
    manifest.finish(status="success")
    manifest_rel = manifest.write(ctx=ctx)

    logger.info(
        "audit_snapshots_complete",
        run_id=rid,
        tickers=len(audit_tickers),
        revision_populated=populated,
        cadence_report=cadence_rel,
    )
    return {
        "status": "success",
        "run_id": rid,
        "run_manifest": manifest_rel,
        "cadence_report": cadence_rel,
        "health_report": health_rel,
        "revision_populated": populated,
        "metric_missing": missing,
    }
