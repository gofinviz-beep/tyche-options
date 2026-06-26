"""Slice 4 — build scanner-ready option chains from Massive flatfiles (not Tradier).

Morning pipeline runs before market open; prior-day OPRA aggregates are the
correct input. Live Tradier chains belong in a separate post-open refresh job.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Any

import pandas as pd
import structlog

from tyche.broker.tradier.symbols import normalize_option_type
from tyche.config import TycheSettings
from tyche.market_data.options_chain_snapshot_store import (
    OPTIONS_CHAIN_CONTRACTS_REL,
    OPTIONS_CHAIN_PREP_REPORT_REL,
    OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
    write_prep_contracts_parquet,
    write_prep_report,
    write_prep_summary_parquet,
)
from tyche.market_data.options_history_store import OptionsHistoryStore
from tyche.market_data.universe_candidates_store import (
    OPTIONS_CANDIDATES_REL,
    load_candidates_parquet,
)
from tyche.storage.paths import StorageContext

logger = structlog.get_logger()

_CHAIN_SOURCE = "flatfile"


@dataclass
class OptionsChainPrepResult:
    chain_date: date
    tickers_requested: int = 0
    tickers_with_contracts: int = 0
    tickers_missing: int = 0
    contract_rows: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chain_date": self.chain_date.isoformat(),
            "tickers_requested": self.tickers_requested,
            "tickers_with_contracts": self.tickers_with_contracts,
            "tickers_missing": self.tickers_missing,
            "contract_rows": self.contract_rows,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "source": _CHAIN_SOURCE,
            "output_paths": [
                OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
                OPTIONS_CHAIN_CONTRACTS_REL,
                OPTIONS_CHAIN_PREP_REPORT_REL,
            ],
        }


def load_prep_candidate_tickers(
    *,
    ctx: StorageContext,
    max_tickers: int,
) -> list[str]:
    records, _ = load_candidates_parquet(rel_path=OPTIONS_CANDIDATES_REL, ctx=ctx)
    tickers = [str(row["ticker"]) for row in records if row.get("ticker")]
    if max_tickers > 0:
        tickers = tickers[:max_tickers]
    return tickers


def extract_flatfile_contracts(
    history_store: OptionsHistoryStore,
    ticker: str,
    *,
    chain_date: date,
    min_dte: int,
    max_dte: int,
    puts_only: bool,
    computed_at: str,
    run_id: str | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Slice one ticker's flatfile history into scanner-shaped contract rows."""
    df = history_store.read_ticker(ticker, end_date=chain_date)
    if df.empty:
        summary = {
            "ticker": ticker,
            "chain_date": chain_date.isoformat(),
            "status": "missing",
            "contract_count": 0,
            "source": _CHAIN_SOURCE,
            "computed_at": computed_at,
            "source_run_id": run_id,
        }
        return [], summary

    latest = max(df["date"])
    day_df = df[df["date"] == latest].copy()
    if day_df.empty:
        summary = {
            "ticker": ticker,
            "chain_date": chain_date.isoformat(),
            "status": "missing",
            "contract_count": 0,
            "source": _CHAIN_SOURCE,
            "computed_at": computed_at,
            "source_run_id": run_id,
        }
        return [], summary

    if puts_only and "option_type" in day_df.columns:
        day_df = day_df[day_df["option_type"].str.upper() == "P"]
    if "dte" in day_df.columns:
        day_df = day_df[(day_df["dte"] >= min_dte) & (day_df["dte"] <= max_dte)]

    if day_df.empty:
        summary = {
            "ticker": ticker,
            "chain_date": latest.isoformat(),
            "status": "no_contracts_in_dte_window",
            "contract_count": 0,
            "source": _CHAIN_SOURCE,
            "computed_at": computed_at,
            "source_run_id": run_id,
        }
        return [], summary

    contracts: list[dict[str, Any]] = []
    for _, row in day_df.iterrows():
        close = float(row.get("close") or 0.0)
        if close <= 0:
            continue
        exp = row.get("expiration")
        if isinstance(exp, pd.Timestamp):
            exp = exp.date()
        contracts.append(
            {
                "ticker": ticker,
                "option_symbol": str(row.get("option_ticker") or ""),
                "chain_date": latest.isoformat(),
                "expiration": exp.isoformat() if hasattr(exp, "isoformat") else str(exp),
                "strike": float(row.get("strike") or 0.0),
                "option_type": normalize_option_type(str(row.get("option_type") or "P")),
                "bid": close,
                "ask": close,
                "mid": close,
                "last": close,
                "volume": int(row.get("volume") or 0),
                "open_interest": 0,
                "implied_volatility": None,
                "delta": None,
                "dte": int(row.get("dte") or 0),
                "source": _CHAIN_SOURCE,
                "computed_at": computed_at,
                "source_run_id": run_id,
            }
        )

    status = "ok" if contracts else "no_liquid_contracts"
    summary = {
        "ticker": ticker,
        "chain_date": latest.isoformat(),
        "status": status,
        "contract_count": len(contracts),
        "source": _CHAIN_SOURCE,
        "computed_at": computed_at,
        "source_run_id": run_id,
    }
    return contracts, summary


def run_options_chain_prep_batch(
    *,
    settings: TycheSettings,
    ctx: StorageContext,
    run_id: str | None = None,
    chain_date: date | None = None,
    puts_only: bool = True,
) -> OptionsChainPrepResult:
    """Materialize prior-session chains for the candidate universe from flatfiles."""
    t0 = time.perf_counter()
    if chain_date is None:
        from tyche.market_data.ingest_dates import resolve_ingest_end_date

        chain_date = resolve_ingest_end_date(
            settings.ingest_window,
            job_name="options-chain-prep-batch",
        )

    result = OptionsChainPrepResult(chain_date=chain_date)
    tickers = load_prep_candidate_tickers(
        ctx=ctx,
        max_tickers=settings.options_snapshot_max_tickers,
    )
    result.tickers_requested = len(tickers)
    if not tickers:
        result.errors.append("options_candidates_empty")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    history_store = OptionsHistoryStore(data_dir=settings.data_dir, ctx=ctx)
    computed_at = datetime.now(timezone.utc).isoformat()
    min_dte = settings.options_snapshot_min_dte
    max_dte = settings.options_snapshot_max_dte

    all_contracts: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []

    for ticker in tickers:
        contracts, summary = extract_flatfile_contracts(
            history_store,
            ticker,
            chain_date=chain_date,
            min_dte=min_dte,
            max_dte=max_dte,
            puts_only=puts_only,
            computed_at=computed_at,
            run_id=run_id,
        )
        summaries.append(summary)
        if contracts:
            result.tickers_with_contracts += 1
            all_contracts.extend(contracts)
        elif summary["status"] == "missing":
            result.tickers_missing += 1

    result.contract_rows = len(all_contracts)

    try:
        write_prep_summary_parquet(summaries, ctx=ctx)
    except Exception:
        logger.error("options_chain_prep_summary_failed", exc_info=True)
        result.errors.append("summary_export_failed")

    try:
        write_prep_contracts_parquet(all_contracts, ctx=ctx)
    except Exception:
        logger.error("options_chain_prep_contracts_failed", exc_info=True)
        result.errors.append("contracts_export_failed")

    report = {
        "chain_date": chain_date.isoformat(),
        "run_id": run_id,
        "source": _CHAIN_SOURCE,
        "candidate_source": OPTIONS_CANDIDATES_REL,
        "history_source": "options_history/",
        **result.to_dict(),
    }
    try:
        write_prep_report(report, ctx=ctx)
    except Exception:
        logger.error("options_chain_prep_report_failed", exc_info=True)
        result.errors.append("report_export_failed")

    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("options_chain_prep_batch_complete", **result.to_dict())
    return result
