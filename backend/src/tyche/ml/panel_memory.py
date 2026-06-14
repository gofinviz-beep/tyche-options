"""Memory-efficient operations for large ML feature panels."""

from __future__ import annotations

import gc

import numpy as np
import pandas as pd

from tyche.storage import read_parquet, write_parquet
from tyche.storage.store_io import context_for_data_access

# Flush ticker batches during build_dataset so we never hold ~9k frames in RAM.
DATASET_CHUNK_TICKERS = 64

# Round-trip through Parquet only for full-universe builds (demand gate / training).
_PANEL_COMPACT_MIN_ROWS = 1_000_000
_DEMAND_GATE_CHECKPOINT_REL = "ml/_checkpoints/demand_gate_base_panel.parquet"


def downcast_panel(df: pd.DataFrame) -> pd.DataFrame:
    """Shrink numeric dtypes and categorise tickers in-place."""
    if df.empty:
        return df

    for col in df.select_dtypes(include=["float64"]).columns:
        df[col] = df[col].astype(np.float32)

    for col in df.select_dtypes(include=["int64"]).columns:
        if col == "ticker":
            continue
        df[col] = pd.to_numeric(df[col], downcast="integer")

    if "ticker" in df.columns and not isinstance(df["ticker"].dtype, pd.CategoricalDtype):
        df["ticker"] = df["ticker"].astype("category")

    return df


def compact_panel_via_parquet(
    df: pd.DataFrame,
    rel_path: str,
    *,
    data_dir: str = "data",
) -> pd.DataFrame:
    """Round-trip through Parquet to drop pandas fragmentation."""
    downcast_panel(df)
    ctx = context_for_data_access(data_dir)
    write_parquet(df, rel_path, atomic=True, ctx=ctx)
    del df
    gc.collect()
    return read_parquet(rel_path, ctx=ctx)


def maybe_compact_panel(
    df: pd.DataFrame,
    *,
    data_dir: str,
    enabled: bool = True,
    min_rows: int = _PANEL_COMPACT_MIN_ROWS,
    rel_path: str = _DEMAND_GATE_CHECKPOINT_REL,
) -> pd.DataFrame:
    """Downcast always; Parquet round-trip only for large panels."""
    if not enabled or len(df) < min_rows:
        return downcast_panel(df)
    return compact_panel_via_parquet(df, rel_path, data_dir=data_dir)
