"""Cloud Parquet store for stocks conviction snapshots.

Canonical GCS artifact: ``signals/stocks/conviction.parquet``.
Replaces ``conviction.db`` as the cloud serving contract.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import structlog

from tyche.conviction.engine import ConvictionSignal
from tyche.market_data.data_store import TickerMetaStore
from tyche.schemas.stocks import ConvictionSnapshotResponse
from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.json_io import sanitize_json_records
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()

STOCKS_CONVICTION_REL = "signals/stocks/conviction.parquet"

_REQUIRED_CONVICTION_FIELDS = (
    "trend_state",
    "conviction_level",
    "csp_eligible",
    "volume_declining",
)


def _normalize_conviction_record(rec: dict[str, Any]) -> dict[str, Any] | None:
    """Coerce Parquet row dict to ``ConvictionSnapshotResponse`` shape."""
    row = dict(rec)

    as_of = row.get("as_of_date")
    if isinstance(as_of, date):
        row["as_of_date"] = as_of.isoformat()
    elif isinstance(as_of, datetime):
        row["as_of_date"] = as_of.date().isoformat()
    elif as_of is not None:
        row["as_of_date"] = str(as_of)

    if "volume_declining" not in row and "volume_declining_on_pullback" in row:
        row["volume_declining"] = row.pop("volume_declining_on_pullback")

    computed_at = row.get("computed_at")
    if isinstance(computed_at, datetime):
        row["computed_at"] = computed_at.isoformat()

    if any(field not in row or row[field] is None for field in _REQUIRED_CONVICTION_FIELDS):
        return None

    return row


def _signal_to_row(
    sig: ConvictionSignal,
    *,
    as_of_date: date,
    computed_at: datetime,
    market_cap: float | None = None,
    institutional_pct: float | None = None,
    sector: str | None = None,
    source_run_id: str | None = None,
) -> dict[str, Any]:
    gate_json = None
    if sig.gate_results:
        gate_json = json.dumps([g.to_dict() for g in sig.gate_results], default=str)

    return {
        "ticker": sig.ticker,
        "as_of_date": (sig.as_of_date or as_of_date).isoformat(),
        "trend_state": sig.trend_state.value,
        "conviction_level": sig.conviction_level,
        "raw_conviction": sig.raw_conviction,
        "csp_eligible": sig.csp_eligible,
        "last_close": round(sig.last_close, 2),
        "ema_8": round(sig.ema_8, 4),
        "ema_21": round(sig.ema_21, 4),
        "ema_8_slope": round(sig.ema_8_slope, 6),
        "ema_21_slope": round(sig.ema_21_slope, 6),
        "price_to_8ema_pct": round(sig.price_to_8ema_pct, 2),
        "price_to_21ema_pct": round(sig.price_to_21ema_pct, 2),
        "volume_declining": sig.volume_declining_on_pullback,
        "days_above_both_emas": sig.days_above_both_emas,
        "prior_streak": sig.prior_streak,
        "avg_volume_20d": sig.avg_volume_20d,
        "latest_volume": sig.latest_volume,
        "ema_50": round(sig.ema_50 or 0.0, 4),
        "ema_50_slope": round(sig.ema_50_slope or 0.0, 6),
        "rsi_14": round(sig.rsi_14 or 0.0, 2),
        "iv_rank": round(sig.iv_rank, 1) if sig.iv_rank is not None else None,
        "iv_percentile": round(sig.iv_percentile, 1)
        if sig.iv_percentile is not None
        else None,
        "atm_iv": round(sig.atm_iv, 4) if sig.atm_iv is not None else None,
        "vrp": round(sig.vrp, 4) if sig.vrp is not None else None,
        "conviction_score": round(sig.conviction_score or 0.0, 3),
        "csp_safety_prob": round(sig.csp_safety_prob, 4)
        if sig.csp_safety_prob is not None
        else None,
        "gate_results_json": gate_json,
        "computed_at": computed_at.isoformat(),
        "market_cap": market_cap,
        "institutional_pct": institutional_pct,
        "sector": sector,
        "generated_at": computed_at.isoformat(),
        "source_run_id": source_run_id,
    }


def write_stocks_conviction_parquet(
    signals: list[ConvictionSignal],
    *,
    as_of_date: date,
    meta_store: TickerMetaStore | None,
    ctx: StorageContext,
    run_id: str | None = None,
) -> int:
    """Persist a full-universe conviction snapshot to GCS/local Parquet."""
    if not signals:
        logger.warning("stocks_conviction_parquet_empty")
        return 0

    computed_at = datetime.now(timezone.utc)
    tickers = [s.ticker for s in signals]
    market_caps = meta_store.get_market_caps(tickers) if meta_store and meta_store.exists else {}
    inst_pcts = (
        meta_store.get_institutional_pcts(tickers) if meta_store and meta_store.exists else {}
    )
    sectors = meta_store.get_sectors(tickers) if meta_store and meta_store.exists else {}

    rows = [
        _signal_to_row(
            sig,
            as_of_date=as_of_date,
            computed_at=computed_at,
            market_cap=market_caps.get(sig.ticker),
            institutional_pct=inst_pcts.get(sig.ticker),
            sector=sectors.get(sig.ticker),
            source_run_id=run_id,
        )
        for sig in signals
    ]
    rows.sort(key=lambda r: r.get("conviction_score") or 0.0, reverse=True)

    df = pd.DataFrame(rows)
    write_parquet(df, STOCKS_CONVICTION_REL, atomic=True, ctx=ctx)
    logger.info(
        "stocks_conviction_parquet_written",
        rows=len(df),
        as_of=as_of_date.isoformat(),
        rel=STOCKS_CONVICTION_REL,
    )
    return len(df)


def load_stocks_conviction_parquet(
    *,
    ctx: StorageContext,
    row_limit: int | None = None,
    rel_path: str = STOCKS_CONVICTION_REL,
) -> tuple[list[ConvictionSnapshotResponse], str | None]:
    """Load conviction snapshots from the signal Parquet artifact."""
    if not storage_exists(rel_path, ctx=ctx):
        return [], None

    df = read_parquet(rel_path, ctx=ctx)
    if df is None or df.empty:
        return [], None

    as_of = (
        str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else None
    )
    records = sanitize_json_records(df.to_dict(orient="records"))
    if row_limit is not None:
        records = records[:row_limit]

    rows: list[ConvictionSnapshotResponse] = []
    skipped = 0
    for rec in records:
        rec.pop("generated_at", None)
        rec.pop("source_run_id", None)
        rec.pop("gate_results_json", None)
        normalized = _normalize_conviction_record(rec)
        if normalized is None:
            skipped += 1
            logger.warning(
                "conviction_row_skipped_incomplete",
                ticker=rec.get("ticker"),
                rel=rel_path,
            )
            continue
        rows.append(ConvictionSnapshotResponse.model_validate(normalized))

    if skipped:
        logger.warning(
            "conviction_rows_skipped",
            skipped=skipped,
            loaded=len(rows),
            rel=rel_path,
        )
    return rows, as_of
