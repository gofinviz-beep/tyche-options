"""Persistence for directional Alpha signals.

Two coordinated layers:

1. **Latest snapshot** — a single Parquet (``data/alpha_signals.parquet`` for the
   peak variant, ``data/alpha_signals_{variant}.parquet`` otherwise) holding the
   most recent full-universe alpha scan. Written by the nightly alpha batch and
   read by ``GET /api/v1/alpha/scan`` for instant page loads. Unchanged, fast.

2. **Dated history** — every ``write`` ALSO drops a per-day snapshot at
   ``alpha_history/{variant}/{YYYY-MM-DD}.parquet`` and refreshes a ``current``
   marker (``alpha_history/{variant}/_current.json``) pointing at the latest
   dated file. This turns day-over-day trend / persistence analysis into a cheap
   read of accumulated snapshots instead of an expensive feature-panel rebuild.

Both layers share the same normalized column schema (``factor_*`` / ``ddim_*``
flattened, plus ``as_of_date`` / ``computed_at``). History writes degrade
gracefully: a history failure is logged but never breaks the primary snapshot.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd
import structlog

from tyche.exceptions import DataStoreError
from tyche.storage import (
    exists as storage_exists,
    list_files,
    read_json,
    read_parquet,
    write_json,
    write_parquet,
)
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

_HISTORY_ROOT = "alpha_history"
_MARKER_NAME = "_current.json"


class AlphaSignalStore:
    """Parquet-backed store for the latest directional alpha scan + dated history."""

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

    # -- history rel-path helpers ------------------------------------------------
    @property
    def _history_dir(self) -> str:
        return f"{_HISTORY_ROOT}/{self._variant}"

    def _snapshot_rel(self, date_str: str) -> str:
        return f"{self._history_dir}/{date_str}.parquet"

    @property
    def _marker_rel(self) -> str:
        return f"{self._history_dir}/{_MARKER_NAME}"

    # -- writes ------------------------------------------------------------------
    def write(self, signal_dicts: list[dict[str, Any]], as_of: date) -> None:
        """Persist a full scan snapshot, replacing the latest and appending history."""
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

        write_parquet(df, self._rel_path, atomic=True, ctx=self._io.ctx)
        logger.info("alpha_store_written", rows=len(df), as_of=as_of.isoformat())

        # Dated history + current marker (best-effort; never break the primary write).
        self._write_history(df, as_of)

    def _write_history(self, df: pd.DataFrame, as_of: date) -> None:
        date_str = as_of.isoformat()
        snap_rel = self._snapshot_rel(date_str)
        try:
            write_parquet(df, snap_rel, atomic=True, ctx=self._io.ctx)
            marker = {
                "variant": self._variant,
                "latest_date": date_str,
                "rel_path": snap_rel,
                "rows": int(len(df)),
                "written_at": datetime.now(timezone.utc).isoformat(),
            }
            write_json(marker, self._marker_rel, atomic=True, ctx=self._io.ctx)
            logger.info(
                "alpha_history_written",
                variant=self._variant,
                date=date_str,
                rows=len(df),
            )
        except Exception as exc:  # noqa: BLE001 - history is best-effort
            logger.warning(
                "alpha_history_write_failed",
                variant=self._variant,
                date=date_str,
                error=str(exc),
            )

    def backfill_current_to_history(self) -> str | None:
        """Seed a dated snapshot from the existing latest single-file snapshot.

        One-time bootstrap so history is not empty before the next nightly batch.
        No-op (returns ``None``) if the latest snapshot is missing / dateless or a
        dated snapshot for that date already exists.
        """
        if not self.exists:
            return None
        df = read_parquet(self._rel_path, ctx=self._io.ctx)
        if df.empty or "as_of_date" not in df.columns:
            return None
        date_str = str(df["as_of_date"].iloc[0])
        try:
            as_of = date.fromisoformat(date_str)
        except ValueError:
            return None
        if storage_exists(self._snapshot_rel(date_str), ctx=self._io.ctx):
            return None
        self._write_history(df, as_of)
        return date_str

    # -- reads -------------------------------------------------------------------
    def read_latest(self) -> tuple[list[dict[str, Any]], str | None, str | None]:
        """Return (signals, as_of_date, computed_at) from the latest snapshot."""
        if not self.exists:
            return [], None, None
        df = read_parquet(self._rel_path, ctx=self._io.ctx)
        return self._records_from_df(df)

    def current(self) -> dict[str, Any] | None:
        """Return the ``current`` marker (latest dated snapshot metadata) or None."""
        if not storage_exists(self._marker_rel, ctx=self._io.ctx):
            return None
        try:
            marker = read_json(self._marker_rel, ctx=self._io.ctx)
        except DataStoreError:
            return None
        return marker if isinstance(marker, dict) else None

    def list_snapshot_dates(self) -> list[str]:
        """Return sorted ISO date strings for which a dated snapshot exists."""
        files = list_files(self._history_dir, suffix=".parquet", ctx=self._io.ctx)
        dates: set[str] = set()
        for f in files:
            stem = Path(f).name[: -len(".parquet")]
            try:
                date.fromisoformat(stem)
            except ValueError:
                continue
            dates.add(stem)
        return sorted(dates)

    def read_snapshot(
        self, date_str: str
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        """Return (signals, as_of_date, computed_at) for a single dated snapshot."""
        rel = self._snapshot_rel(date_str)
        if not storage_exists(rel, ctx=self._io.ctx):
            return [], None, None
        df = read_parquet(rel, ctx=self._io.ctx)
        return self._records_from_df(df)

    def read_history(
        self,
        sessions: int | None = None,
        dates: list[str] | None = None,
    ) -> pd.DataFrame:
        """Concatenate dated snapshots into a single trend panel.

        Selection: explicit ``dates`` if given, else the most recent ``sessions``
        snapshots, else all. Guarantees a ``date`` column (from ``as_of_date`` /
        the filename). Returns an empty frame when no snapshots exist — the cheap
        input for persistence / trend reads once history has accumulated.
        """
        available = self.list_snapshot_dates()
        if dates is not None:
            wanted = set(dates)
            picked = [d for d in available if d in wanted]
        elif sessions is not None:
            picked = available[-sessions:]
        else:
            picked = available

        frames: list[pd.DataFrame] = []
        for d in picked:
            try:
                df = read_parquet(self._snapshot_rel(d), ctx=self._io.ctx)
            except DataStoreError:
                continue
            if df.empty:
                continue
            if "date" not in df.columns:
                df["date"] = df["as_of_date"] if "as_of_date" in df.columns else d
            frames.append(df)
        if not frames:
            return pd.DataFrame()
        return pd.concat(frames, ignore_index=True)

    # -- shared record normalization --------------------------------------------
    def _records_from_df(
        self, df: pd.DataFrame
    ) -> tuple[list[dict[str, Any]], str | None, str | None]:
        if df.empty:
            return [], None, None

        as_of = str(df["as_of_date"].iloc[0]) if "as_of_date" in df.columns else None
        computed_at = (
            str(df["computed_at"].iloc[0]) if "computed_at" in df.columns else None
        )

        factor_cols = [c for c in df.columns if c.startswith("factor_")]
        demand_cols = [c for c in df.columns if c.startswith("ddim_")]
        meta_cols = {"as_of_date", "computed_at", "date"}
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
