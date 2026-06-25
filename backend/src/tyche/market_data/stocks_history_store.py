"""Cloud Parquet store for stocks history summaries + transitions."""

from __future__ import annotations

from typing import Any

import pandas as pd
import structlog

from tyche.schemas.stocks import ConvictionTransitionResponse
from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.json_io import sanitize_json_records
from tyche.storage.paths import StorageContext
from tyche.workflow.history_summary import STOCKS_HISTORY_SUMMARY_REL

logger = structlog.get_logger()

STOCKS_TRANSITIONS_REL = "signals/stocks/transitions.parquet"


def write_history_summary_parquet(
    rows: list[dict[str, Any]],
    *,
    ctx: StorageContext,
) -> int:
    if not rows:
        logger.warning("history_summary_parquet_empty")
        return 0
    df = pd.DataFrame(rows)
    write_parquet(df, STOCKS_HISTORY_SUMMARY_REL, atomic=True, ctx=ctx)
    logger.info("history_summary_parquet_written", rows=len(df))
    return len(df)


def load_history_summary_rows(
    *,
    ctx: StorageContext,
    rel_path: str = STOCKS_HISTORY_SUMMARY_REL,
) -> list[dict[str, Any]]:
    if not storage_exists(rel_path, ctx=ctx):
        return []
    df = read_parquet(rel_path, ctx=ctx)
    if df is None or df.empty:
        return []
    return sanitize_json_records(df.to_dict(orient="records"))


def write_transitions_parquet(
    transitions: list[dict[str, Any]],
    *,
    ctx: StorageContext,
) -> int:
    if not transitions:
        return 0
    df = pd.DataFrame(transitions)
    write_parquet(df, STOCKS_TRANSITIONS_REL, atomic=True, ctx=ctx)
    logger.info("transitions_parquet_written", rows=len(df))
    return len(df)


def load_transition_responses(
    *,
    ctx: StorageContext,
    rel_path: str = STOCKS_TRANSITIONS_REL,
) -> list[ConvictionTransitionResponse]:
    if not storage_exists(rel_path, ctx=ctx):
        return []
    df = read_parquet(rel_path, ctx=ctx)
    if df is None or df.empty:
        return []
    records = sanitize_json_records(df.to_dict(orient="records"))
    return [ConvictionTransitionResponse.model_validate(r) for r in records]
