"""Persistence for the v3 Stock Screener index.

A single compact Parquet snapshot (``signals/stocks/screener_index.parquet``)
holds one row per equity-universe ticker with scalar-only columns (multi-
timeframe RSI, EMA stack, returns, and the ``setup_score``/``setup_label``
"Diamond Finder" fields). Mirrors the lightweight single-file snapshot
pattern used by ``AlphaSignalStore`` / ``conviction_signals.parquet`` — never
per-ticker screener files, and never the large deep-dive payloads.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pandas as pd
import structlog

from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.json_io import sanitize_json_records
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

SCREENER_INDEX_REL = "signals/stocks/screener_index.parquet"


class ScreenerIndexStore:
    """Parquet-backed store for the latest universe-wide screener index."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("", data_dir, ctx)

    @property
    def ctx(self) -> StorageContext:
        return self._io.ctx

    @property
    def exists(self) -> bool:
        return storage_exists(SCREENER_INDEX_REL, ctx=self._io.ctx)

    def write(self, rows: list[dict[str, Any]], ctx: StorageContext | None = None) -> int:
        """Overwrite the single index Parquet with all rows. Returns row count."""
        use_ctx = ctx or self._io.ctx
        if not rows:
            logger.warning("screener_index_write_empty")
            return 0

        df = pd.DataFrame(rows)
        df["computed_at"] = datetime.now(timezone.utc).isoformat()
        write_parquet(df, SCREENER_INDEX_REL, atomic=True, ctx=use_ctx)
        logger.info("screener_index_written", rows=len(df))
        return len(df)

    def read(self, ctx: StorageContext | None = None) -> pd.DataFrame | None:
        """Return the full index as a DataFrame, or ``None`` if absent/empty."""
        use_ctx = ctx or self._io.ctx
        if not storage_exists(SCREENER_INDEX_REL, ctx=use_ctx):
            return None
        df = read_parquet(SCREENER_INDEX_REL, ctx=use_ctx)
        if df is None or df.empty:
            return None
        return df


def load_screener_rows(
    *,
    ctx: StorageContext,
    rel_path: str = SCREENER_INDEX_REL,
) -> tuple[list[dict[str, Any]], str | None, str | None]:
    """Return ``(rows, as_of_date, computed_at)`` from the screener index Parquet."""
    if not storage_exists(rel_path, ctx=ctx):
        return [], None, None

    df = read_parquet(rel_path, ctx=ctx)
    if df is None or df.empty:
        return [], None, None

    computed_at = str(df["computed_at"].iloc[0]) if "computed_at" in df.columns else None
    as_of_date = str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else None
    records = sanitize_json_records(df.to_dict(orient="records"))
    return records, as_of_date, computed_at
