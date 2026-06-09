"""Persistence for directional Alpha signals.

A single Parquet snapshot (``data/alpha_signals.parquet``) holds the most
recent full-universe alpha scan, written by the nightly alpha batch and read
by ``GET /api/v1/alpha/scan``. Mirrors the lightweight snapshot pattern used
by the conviction signal store.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from tyche.storage import exists as storage_exists, read_parquet, write_parquet
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()


class AlphaSignalStore:
    """Parquet-backed store for the latest directional alpha scan."""

    def __init__(
        self,
        data_dir: str = "data",
        variant: str = "peak",
        ctx: StorageContext | None = None,
        rel_path: str | None = None,
    ) -> None:
        self._variant = variant or "peak"
        if rel_path:
            name = rel_path
        else:
            name = (
                "alpha_signals.parquet"
                if self._variant == "peak"
                else f"alpha_signals_{self._variant}.parquet"
            )
        self._io = StoreBackend.create("", data_dir, ctx)
        self._rel_path = name

    @property
    def variant(self) -> str:
        return self._variant

    @property
    def path(self) -> Path:
        return self._io.store_dir / self._rel_path

    @property
    def exists(self) -> bool:
        return storage_exists(self._rel_path, ctx=self._io.ctx)

    def write(self, signal_dicts: list[dict[str, Any]], as_of: date) -> None:
        """Persist a full scan snapshot, replacing any previous snapshot."""
        if not signal_dicts:
            logger.warning("alpha_store_write_empty")
            return

        df = pd.DataFrame(signal_dicts)
        if "factors" in df.columns:
            factors = pd.json_normalize(df["factors"]).add_prefix("factor_")
            df = pd.concat([df.drop(columns=["factors"]), factors], axis=1)
        if "demand" in df.columns:
            demand = pd.json_normalize(df["demand"]).add_prefix("ddim_")
            df = pd.concat([df.drop(columns=["demand"]), demand], axis=1)

        df["as_of_date"] = as_of.isoformat()
        df["computed_at"] = datetime.now(timezone.utc).isoformat()

        write_parquet(
            df,
            self._rel_path,
            atomic=True,
            ctx=self._io.ctx,
        )
        logger.info("alpha_store_written", rows=len(df), as_of=as_of.isoformat())

    def read_latest(self) -> tuple[list[dict[str, Any]], str | None, str | None]:
        """Return (signals, as_of_date, computed_at) from the snapshot."""
        if not self.exists:
            return [], None, None

        df = read_parquet(self._rel_path, ctx=self._io.ctx)
        if df.empty:
            return [], None, None

        as_of = str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else None
        computed_at = (
            str(df["computed_at"].iloc[0]) if "computed_at" in df.columns else None
        )

        factor_cols = [c for c in df.columns if c.startswith("factor_")]
        demand_cols = [c for c in df.columns if c.startswith("ddim_")]
        meta_cols = {"as_of_date", "computed_at"}
        records: list[dict[str, Any]] = []
        for _, row in df.iterrows():
            rec: dict[str, Any] = {}
            factors: dict[str, float] = {}
            demand: dict[str, float] = {}
            for col in df.columns:
                if col in meta_cols:
                    continue
                val = row[col]
                if col in factor_cols:
                    factors[col[len("factor_") :]] = _clean(val)
                elif col in demand_cols:
                    demand[col[len("ddim_") :]] = _clean(val)
                else:
                    rec[col] = _clean(val)
            rec["factors"] = factors
            if demand:
                rec["demand"] = demand
            records.append(rec)

        return records, as_of, computed_at


def _clean(val: Any) -> Any:
    if pd.isna(val):
        return None
    if hasattr(val, "item"):
        return val.item()
    return val
