"""Cloud Parquet store for deep dip scan results."""

from __future__ import annotations

import json
from typing import Any

import pandas as pd
import structlog

from tyche.schemas.alerts import DeepDipScanResponse
from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.json_io import sanitize_json_records
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()

STOCKS_DEEP_DIPS_REL = "signals/stocks/deep_dips.parquet"


def write_deep_dips_parquet(
    scan: DeepDipScanResponse,
    *,
    ctx: StorageContext,
) -> int:
    """Persist deep dip alerts (+ scan metadata columns) to Parquet."""
    if not scan.alerts:
        meta_row = {
            "ticker": "__meta__",
            "as_of": scan.as_of_date,
            "total_analyzed": scan.total_analyzed,
            "total_oversold": scan.total_oversold,
            "total_actionable": scan.total_actionable,
            "market_context_json": scan.market_context.model_dump(mode="json")
            if scan.market_context
            else None,
        }
        df = pd.DataFrame([meta_row])
        write_parquet(df, STOCKS_DEEP_DIPS_REL, atomic=True, ctx=ctx)
        return 0

    market_ctx_json = (
        json.dumps(scan.market_context.model_dump(mode="json"))
        if scan.market_context
        else None
    )
    rows: list[dict[str, Any]] = []
    for alert in scan.alerts:
        rec = alert.model_dump(mode="json")
        rec["as_of"] = scan.as_of_date
        rec["total_analyzed"] = scan.total_analyzed
        rec["total_oversold"] = scan.total_oversold
        rec["total_actionable"] = scan.total_actionable
        rec["market_context_json"] = market_ctx_json
        if rec.get("dip_classification") is not None:
            rec["dip_classification_json"] = json.dumps(rec.pop("dip_classification"))
        else:
            rec["dip_classification_json"] = None
        if rec.get("recovery_signal") is not None:
            rec["recovery_signal_json"] = json.dumps(rec.pop("recovery_signal"))
        else:
            rec["recovery_signal_json"] = None
        rows.append(rec)

    df = pd.DataFrame(rows)
    write_parquet(df, STOCKS_DEEP_DIPS_REL, atomic=True, ctx=ctx)
    logger.info(
        "deep_dips_parquet_written",
        alerts=len(rows),
        as_of=scan.as_of_date,
    )
    return len(rows)


def load_deep_dips_scan(
    *,
    ctx: StorageContext,
    rel_path: str = STOCKS_DEEP_DIPS_REL,
) -> DeepDipScanResponse | None:
    """Reconstruct ``DeepDipScanResponse`` from signal Parquet."""
    if not storage_exists(rel_path, ctx=ctx):
        return None

    df = read_parquet(rel_path, ctx=ctx)
    if df is None or df.empty:
        return None

    records = sanitize_json_records(df.to_dict(orient="records"))
    if records and records[0].get("ticker") == "__meta__":
        meta = records[0]
        from tyche.schemas.alerts import MarketContextResponse

        ctx_raw = meta.get("market_context_json")
        market_ctx = (
            MarketContextResponse.model_validate(json.loads(ctx_raw))
            if ctx_raw
            else None
        )
        return DeepDipScanResponse(
            alerts=[],
            total_analyzed=int(meta.get("total_analyzed") or 0),
            total_oversold=int(meta.get("total_oversold") or 0),
            total_actionable=int(meta.get("total_actionable") or 0),
            market_context=market_ctx,
            as_of_date=str(meta.get("as_of") or ""),
        )

    from tyche.schemas.alerts import (
        DeepDipAlertResponse,
        DipClassificationResponse,
        MarketContextResponse,
        RecoverySignalResponse,
    )

    first = records[0]
    as_of_date = str(first.get("as_of") or "")
    ctx_raw = first.get("market_context_json")
    market_ctx = (
        MarketContextResponse.model_validate(json.loads(ctx_raw)) if ctx_raw else None
    )

    alerts: list[DeepDipAlertResponse] = []
    for rec in records:
        if rec.get("ticker") == "__meta__":
            continue
        dip_raw = rec.pop("dip_classification_json", None)
        rec.pop("market_context_json", None)
        rec.pop("total_analyzed", None)
        rec.pop("total_oversold", None)
        rec.pop("total_actionable", None)
        rec.pop("as_of", None)
        recovery_raw = rec.pop("recovery_signal_json", None)
        if dip_raw:
            rec["dip_classification"] = json.loads(dip_raw)
        if recovery_raw:
            rec["recovery_signal"] = json.loads(recovery_raw)
        alerts.append(DeepDipAlertResponse.model_validate(rec))

    return DeepDipScanResponse(
        alerts=alerts,
        total_analyzed=int(first.get("total_analyzed") or 0),
        total_oversold=int(first.get("total_oversold") or 0),
        total_actionable=int(first.get("total_actionable") or 0),
        market_context=market_ctx,
        as_of_date=as_of_date,
    )
