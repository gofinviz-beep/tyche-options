"""Cloud Parquet store for metadata-first candidate universes."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import structlog

from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()

OPTIONS_CANDIDATES_REL = "signals/universe/options_candidates.parquet"
STOCKS_CANDIDATES_REL = "signals/universe/stocks_candidates.parquet"
CSP_SCAN_TICKERS_REL = "signals/universe/csp_scan_tickers.parquet"


def write_candidates_parquet(
    rows: list[dict[str, Any]],
    *,
    rel_path: str,
    ctx: StorageContext,
    as_of_date: date,
    run_id: str | None = None,
) -> int:
    """Persist a ranked candidate universe snapshot."""
    computed_at = datetime.now(timezone.utc).isoformat()
    if not rows:
        meta_row = {
            "ticker": "__meta__",
            "as_of_date": as_of_date.isoformat(),
            "computed_at": computed_at,
            "source_run_id": run_id,
            "rank": 0,
            "priority_score": 0.0,
        }
        df = pd.DataFrame([meta_row])
        write_parquet(df, rel_path, atomic=True, ctx=ctx)
        logger.warning("candidate_universe_empty", rel=rel_path)
        return 0

    for row in rows:
        row.setdefault("as_of_date", as_of_date.isoformat())
        row.setdefault("computed_at", computed_at)
        row.setdefault("source_run_id", run_id)

    df = pd.DataFrame(rows)
    write_parquet(df, rel_path, atomic=True, ctx=ctx)
    logger.info(
        "candidate_universe_written",
        rel=rel_path,
        rows=len(df),
        as_of=as_of_date.isoformat(),
    )
    return len(df)


def load_candidates_parquet(
    *,
    rel_path: str,
    ctx: StorageContext,
    row_limit: int | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Load candidate rows from Parquet, excluding the ``__meta__`` placeholder."""
    if not storage_exists(rel_path, ctx=ctx):
        return [], None

    df = read_parquet(rel_path, ctx=ctx)
    if df is None or df.empty:
        return [], None

    as_of = (
        str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else None
    )
    if "ticker" in df.columns:
        df = df[df["ticker"] != "__meta__"]

    records = df.to_dict(orient="records")
    if row_limit is not None:
        records = records[:row_limit]
    return records, as_of


def load_csp_scan_tickers(
    *,
    ctx: StorageContext,
    row_limit: int | None = None,
) -> tuple[list[str], str | None]:
    """Load ranked CSP-eligible tickers from ``csp_scan_tickers.parquet``."""
    records, as_of = load_candidates_parquet(
        rel_path=CSP_SCAN_TICKERS_REL,
        ctx=ctx,
        row_limit=row_limit,
    )
    tickers = [str(row["ticker"]) for row in records if row.get("ticker")]
    return tickers, as_of
