"""Cloud Parquet store for CSP scanner batch results (Slice 5)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pandas as pd
import structlog

from tyche.storage import (
    exists as storage_exists,
    read_json,
    read_parquet,
    write_json,
    write_parquet,
)
from tyche.storage.paths import StorageContext

if TYPE_CHECKING:
    from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()

OPTIONS_SCANNER_REL = "signals/options/scanner.parquet"
OPTIONS_SCANNER_REPORT_REL = "reports/options_scanner/manifest.json"


def scored_candidate_to_row(
    candidate: "ScoredCandidate",
    *,
    scan_meta: dict[str, Any],
) -> dict[str, Any]:
    """Flatten a ``ScoredCandidate`` into a Parquet row."""
    exp = candidate.expiration
    exp_str = exp.isoformat() if hasattr(exp, "isoformat") else str(exp)
    earnings_date = candidate.earnings_date
    return {
        **scan_meta,
        "symbol": candidate.symbol,
        "option_symbol": candidate.option_symbol,
        "strike": candidate.strike,
        "expiration": exp_str,
        "dte": candidate.dte,
        "bid": candidate.bid,
        "ask": candidate.ask,
        "mid": candidate.mid,
        "bid_ask_spread_pct": candidate.bid_ask_spread_pct,
        "premium_per_contract": candidate.premium_per_contract,
        "collateral_required": candidate.collateral_required,
        "annualized_return_pct": candidate.annualized_return_pct,
        "score": candidate.score,
        "delta": candidate.delta,
        "theta": candidate.theta,
        "implied_volatility": candidate.implied_volatility,
        "volume": candidate.volume,
        "open_interest": candidate.open_interest,
        "earnings_within_dte": candidate.earnings_within_dte,
        "earnings_date": earnings_date.isoformat() if earnings_date else None,
        "macro_event_in_dte": candidate.macro_event_in_dte,
    }


def write_scanner_parquet(
    rows: list[dict[str, Any]],
    *,
    ctx: StorageContext,
) -> int:
    if not rows:
        logger.warning("options_scanner_parquet_empty")
        return 0
    write_parquet(pd.DataFrame(rows), OPTIONS_SCANNER_REL, atomic=True, ctx=ctx)
    logger.info(
        "options_scanner_parquet_written",
        rows=len(rows),
        rel=OPTIONS_SCANNER_REL,
    )
    return len(rows)


def write_scanner_report(payload: dict[str, Any], *, ctx: StorageContext) -> str:
    write_json(payload, OPTIONS_SCANNER_REPORT_REL, atomic=True, ctx=ctx)
    logger.info("options_scanner_report_written", rel=OPTIONS_SCANNER_REPORT_REL)
    return OPTIONS_SCANNER_REPORT_REL


def load_scanner_parquet(
    *,
    ctx: StorageContext,
) -> tuple[list[dict[str, Any]], str | None]:
    if not storage_exists(OPTIONS_SCANNER_REL, ctx=ctx):
        return [], None
    df = read_parquet(OPTIONS_SCANNER_REL, ctx=ctx)
    if df is None or df.empty:
        return [], None
    as_of = str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else None
    return df.to_dict(orient="records"), as_of


def load_scanner_report(*, ctx: StorageContext) -> dict[str, Any] | None:
    if not storage_exists(OPTIONS_SCANNER_REPORT_REL, ctx=ctx):
        return None
    return read_json(OPTIONS_SCANNER_REPORT_REL, ctx=ctx)


def build_scan_payload(
    *,
    report: dict[str, Any] | None,
    candidate_rows: list[dict[str, Any]],
) -> dict[str, Any]:
    """Shape published scanner JSON from report manifest + candidate rows."""
    if report is None:
        return {"status": "unavailable", "message": "No scanner report available"}

    payload = {
        "scan_id": report.get("scan_id"),
        "scanned_at": report.get("scanned_at"),
        "as_of_date": report.get("as_of_date"),
        "symbols_scanned": report.get("symbols_scanned", 0),
        "chain_source": report.get("chain_source"),
        "pipeline_stages": report.get("pipeline_stages", []),
        "csp_candidates": [],
        "cc_candidates": [],
        "llm_analyses": [],
        "earnings_context": report.get("earnings_context", {}),
        "institutional_ownership": report.get("institutional_ownership", {}),
        "allocation": report.get("allocation"),
        "allocated_trades": report.get("allocated_trades", []),
        "errors": report.get("errors", []),
        "csp_diagnostics": report.get("csp_diagnostics", {}),
    }
    for row in candidate_rows:
        payload["csp_candidates"].append(
            {
                "symbol": row.get("symbol"),
                "option_symbol": row.get("option_symbol"),
                "strike": row.get("strike"),
                "expiration": row.get("expiration"),
                "dte": row.get("dte"),
                "bid": row.get("bid"),
                "ask": row.get("ask"),
                "mid": row.get("mid"),
                "bid_ask_spread_pct": row.get("bid_ask_spread_pct"),
                "premium_per_contract": row.get("premium_per_contract"),
                "collateral_required": row.get("collateral_required"),
                "annualized_return_pct": row.get("annualized_return_pct"),
                "score": row.get("score"),
                "delta": row.get("delta"),
                "theta": row.get("theta"),
                "implied_volatility": row.get("implied_volatility"),
                "volume": row.get("volume"),
                "open_interest": row.get("open_interest"),
                "earnings_within_dte": row.get("earnings_within_dte"),
                "earnings_date": row.get("earnings_date"),
            }
        )
    return payload
