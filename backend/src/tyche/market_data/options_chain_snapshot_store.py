"""Cloud artifacts for options chain prep (Slice 4 — flatfile-sourced)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import structlog

from tyche.storage import exists as storage_exists, read_parquet, write_json, write_parquet
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()

OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL = "signals/options/options_chain_snapshot.parquet"
OPTIONS_CHAIN_CONTRACTS_REL = "signals/options/options_chain_contracts.parquet"
OPTIONS_CHAIN_PREP_REPORT_REL = "reports/options_chain_prep/manifest.json"
OPTIONS_TRADIER_SNAPSHOT_REPORT_REL = "reports/options_snapshot/manifest.json"


def write_prep_summary_parquet(
    rows: list[dict[str, Any]],
    *,
    ctx: StorageContext,
) -> int:
    if not rows:
        logger.warning("options_chain_prep_summary_empty")
        return 0
    write_parquet(
        pd.DataFrame(rows),
        OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
        atomic=True,
        ctx=ctx,
    )
    logger.info(
        "options_chain_prep_summary_written",
        rows=len(rows),
        rel=OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
    )
    return len(rows)


def write_prep_contracts_parquet(
    rows: list[dict[str, Any]],
    *,
    ctx: StorageContext,
) -> int:
    if not rows:
        logger.warning("options_chain_prep_contracts_empty")
        return 0
    write_parquet(
        pd.DataFrame(rows),
        OPTIONS_CHAIN_CONTRACTS_REL,
        atomic=True,
        ctx=ctx,
    )
    logger.info(
        "options_chain_prep_contracts_written",
        rows=len(rows),
        rel=OPTIONS_CHAIN_CONTRACTS_REL,
    )
    return len(rows)


def write_prep_report(
    payload: dict[str, Any],
    *,
    ctx: StorageContext,
) -> str:
    write_json(payload, OPTIONS_CHAIN_PREP_REPORT_REL, atomic=True, ctx=ctx)
    logger.info("options_chain_prep_report_written", rel=OPTIONS_CHAIN_PREP_REPORT_REL)
    return OPTIONS_CHAIN_PREP_REPORT_REL


def load_prep_contracts_parquet(
    *,
    ctx: StorageContext,
    chain_date: date | None = None,
    tickers: list[str] | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Load scanner-ready contract rows from the prep artifact."""
    if not storage_exists(OPTIONS_CHAIN_CONTRACTS_REL, ctx=ctx):
        return [], None

    df = read_parquet(OPTIONS_CHAIN_CONTRACTS_REL, ctx=ctx)
    if df is None or df.empty:
        return [], None

    if chain_date is not None and "chain_date" in df.columns:
        target = chain_date.isoformat()
        df = df[df["chain_date"].astype(str) == target]
    if tickers and "ticker" in df.columns:
        allowed = set(tickers)
        df = df[df["ticker"].astype(str).isin(allowed)]

    as_of = (
        str(df["chain_date"].iloc[0])
        if "chain_date" in df.columns and not df.empty
        else None
    )
    return df.to_dict(orient="records"), as_of


def load_snapshot_summary_parquet(
    *,
    ctx: StorageContext,
    snapshot_date: date | None = None,
) -> tuple[list[dict[str, Any]], str | None]:
    """Backward-compatible alias for per-ticker prep summary rows."""
    if not storage_exists(OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL, ctx=ctx):
        return [], None

    df = read_parquet(OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL, ctx=ctx)
    if df is None or df.empty:
        return [], None

    date_col = "chain_date" if "chain_date" in df.columns else "snapshot_date"
    if snapshot_date is not None and date_col in df.columns:
        target = snapshot_date.isoformat()
        df = df[df[date_col].astype(str) == target]

    as_of = str(df[date_col].iloc[0]) if date_col in df.columns and not df.empty else None
    return df.to_dict(orient="records"), as_of


def build_tradier_summary_rows(
    *,
    tickers: list[str],
    stats: Any,
    snapshot_date: date,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Per-ticker summary rows for the optional live Tradier refresh job."""
    computed_at = datetime.now(timezone.utc).isoformat()
    rows: list[dict[str, Any]] = []
    for ticker in tickers:
        rows.append(
            {
                "ticker": ticker,
                "snapshot_date": snapshot_date.isoformat(),
                "status": stats.ticker_status.get(ticker, "missing"),
                "contract_count": stats.ticker_contracts.get(ticker, 0),
                "rows_added": stats.ticker_rows_added.get(ticker, 0),
                "source": "tradier",
                "computed_at": computed_at,
                "source_run_id": run_id,
            }
        )
    return rows


def write_tradier_snapshot_report(
    payload: dict[str, Any],
    *,
    ctx: StorageContext,
) -> str:
    write_json(payload, OPTIONS_TRADIER_SNAPSHOT_REPORT_REL, atomic=True, ctx=ctx)
    return OPTIONS_TRADIER_SNAPSHOT_REPORT_REL
