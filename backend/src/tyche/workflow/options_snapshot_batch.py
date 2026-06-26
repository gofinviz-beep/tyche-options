"""Optional live Tradier refresh — NOT for the 2:30 AM morning pipeline.

Use ``options-chain-prep-batch`` (flatfile-sourced) for pre-market cloud compute.
Run this job manually or on a post-open schedule when live bid/ask/OI matter.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import structlog

from tyche.config import TycheSettings
from tyche.market_data.options_chain_snapshot_store import (
    OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
    OPTIONS_TRADIER_SNAPSHOT_REPORT_REL,
    build_tradier_summary_rows,
    write_prep_summary_parquet,
    write_tradier_snapshot_report,
)
from tyche.market_data.universe_candidates_store import (
    CSP_SCAN_TICKERS_REL,
    load_csp_scan_tickers,
)
from tyche.storage.paths import StorageContext
from tyche.workflow.options_snapshot import SnapshotStats, run_options_snapshot

logger = structlog.get_logger()


@dataclass
class OptionsSnapshotBatchResult:
    snapshot_date: date
    tickers_requested: int = 0
    tickers_succeeded: int = 0
    tickers_skipped: int = 0
    tickers_failed: int = 0
    contracts_stored: int = 0
    rows_added: int = 0
    api_calls: int = 0
    duration_ms: float = 0.0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_date": self.snapshot_date.isoformat(),
            "tickers_requested": self.tickers_requested,
            "tickers_succeeded": self.tickers_succeeded,
            "tickers_skipped": self.tickers_skipped,
            "tickers_failed": self.tickers_failed,
            "contracts_stored": self.contracts_stored,
            "rows_added": self.rows_added,
            "api_calls": self.api_calls,
            "duration_ms": round(self.duration_ms, 2),
            "errors": self.errors,
            "output_paths": [
                "options_chains/",
                OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
                OPTIONS_TRADIER_SNAPSHOT_REPORT_REL,
            ],
        }


def load_snapshot_candidate_tickers(
    *,
    ctx: StorageContext,
    max_tickers: int,
) -> list[str]:
    """Read CSP-eligible tickers from ``csp_scan_tickers.parquet``."""
    tickers, _ = load_csp_scan_tickers(ctx=ctx, row_limit=max_tickers if max_tickers > 0 else None)
    return tickers


async def run_options_snapshot_batch(
    *,
    settings: TycheSettings,
    ctx: StorageContext,
    run_id: str | None = None,
    snapshot_date: date | None = None,
    puts_only: bool = True,
) -> OptionsSnapshotBatchResult:
    """Fetch Tradier chains for candidate tickers and persist to GCS."""
    t0 = time.perf_counter()
    if snapshot_date is None:
        from tyche.market_data.ingest_dates import resolve_ingest_end_date

        snapshot_date = resolve_ingest_end_date(
            settings.ingest_window,
            job_name="options-snapshot-batch",
        )

    result = OptionsSnapshotBatchResult(snapshot_date=snapshot_date)

    if not settings.tradier_api_token:
        result.errors.append("missing_tradier_api_token")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    tickers = load_snapshot_candidate_tickers(
        ctx=ctx,
        max_tickers=settings.options_snapshot_max_tickers,
    )
    result.tickers_requested = len(tickers)
    if not tickers:
        result.errors.append("csp_scan_tickers_empty")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    logger.info(
        "options_snapshot_batch_start",
        tickers=len(tickers),
        snapshot_date=snapshot_date.isoformat(),
    )

    try:
        stats: SnapshotStats = await run_options_snapshot(
            tickers=tickers,
            settings=settings,
            snapshot_date=snapshot_date,
            puts_only=puts_only,
            ctx=ctx,
        )
    except Exception:
        logger.error("options_snapshot_batch_failed", exc_info=True)
        result.errors.append("tradier_snapshot_failed")
        result.duration_ms = (time.perf_counter() - t0) * 1000
        return result

    result.tickers_succeeded = stats.tickers_succeeded
    result.tickers_skipped = stats.tickers_skipped
    result.tickers_failed = stats.tickers_failed
    result.contracts_stored = stats.contracts_stored
    result.rows_added = stats.rows_added
    result.api_calls = stats.api_calls

    summary_rows = build_tradier_summary_rows(
        tickers=tickers,
        stats=stats,
        snapshot_date=snapshot_date,
        run_id=run_id,
    )
    try:
        write_prep_summary_parquet(summary_rows, ctx=ctx)
    except Exception:
        logger.error("options_snapshot_summary_export_failed", exc_info=True)
        result.errors.append("summary_export_failed")

    report = {
        "snapshot_date": snapshot_date.isoformat(),
        "run_id": run_id,
        "tickers_requested": result.tickers_requested,
        "tickers_succeeded": result.tickers_succeeded,
        "tickers_skipped": result.tickers_skipped,
        "tickers_failed": result.tickers_failed,
        "contracts_stored": result.contracts_stored,
        "rows_added": result.rows_added,
        "api_calls": result.api_calls,
        "elapsed_seconds": round(stats.elapsed_seconds, 2),
        "candidate_source": CSP_SCAN_TICKERS_REL,
        "source": "tradier",
    }
    try:
        write_tradier_snapshot_report(report, ctx=ctx)
    except Exception:
        logger.error("options_snapshot_report_export_failed", exc_info=True)
        result.errors.append("report_export_failed")

    result.duration_ms = (time.perf_counter() - t0) * 1000
    logger.info("options_snapshot_batch_complete", **result.to_dict())
    return result
