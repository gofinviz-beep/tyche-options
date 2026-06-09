"""Shared helpers for market-data Parquet stores (GCP-B)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import pandas as pd
import pyarrow as pa

from tyche.storage.parquet_io import (
    exists as storage_exists,
    list_files,
    parquet_num_rows,
    read_parquet,
    write_parquet,
    write_parquet_table,
)
from tyche.storage.paths import StorageContext, join_uri, storage_context_from_settings

if TYPE_CHECKING:
    pass

TickerNormalize = Literal["upper", "as_is"]


def context_for_data_access(
    data_dir: str = "data",
    ctx: StorageContext | None = None,
) -> StorageContext:
    """Resolve storage context for a store constructor.

    When *ctx* is omitted and ``data_backend=gcs`` in settings, uses the GCS
    bucket/prefix from config. Otherwise uses a local root at *data_dir*
    (tests and explicit ``Store(data_dir=tmp_path)`` calls).
    """
    if ctx is not None:
        return ctx
    # Unit tests pass tmp_path as data_dir — never route those through live GCS.
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return StorageContext(backend="local", local_root=Path(data_dir))
    from tyche.config import get_settings

    settings = get_settings()
    if settings.data_backend == "gcs":
        return storage_context_from_settings(settings)
    return StorageContext(backend="local", local_root=Path(data_dir))


def safe_ticker_stem(ticker: str, *, normalize: TickerNormalize = "upper") -> str:
    """Sanitize a ticker symbol for use as a Parquet filename stem."""
    safe = ticker.replace("/", "_").replace("\\", "_").replace(" ", "_")
    if normalize == "upper":
        return safe.upper()
    return safe


@dataclass
class StoreBackend:
    """Relative-path I/O facade for one store subdirectory."""

    subdir: str
    ctx: StorageContext
    ticker_normalize: TickerNormalize = "upper"
    upper_stems: bool = True

    @classmethod
    def create(
        cls,
        subdir: str,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
        *,
        ticker_normalize: TickerNormalize = "upper",
        upper_stems: bool = True,
    ) -> StoreBackend:
        backend = cls(
            subdir=subdir,
            ctx=context_for_data_access(data_dir, ctx),
            ticker_normalize=ticker_normalize,
            upper_stems=upper_stems,
        )
        if backend.ctx.backend == "local":
            backend.store_dir.mkdir(parents=True, exist_ok=True)
        return backend

    @property
    def store_dir(self) -> Path:
        """Logical store directory (local path for display / tests)."""
        if self.subdir:
            return self.ctx.local_root / self.subdir
        return self.ctx.local_root

    def rel(self, *parts: str) -> str:
        base = self.subdir or ""
        return join_uri(base, *parts) if base or parts else ""

    def file_rel(self, filename: str) -> str:
        if self.subdir:
            return join_uri(self.subdir, filename)
        return filename

    def ticker_rel(self, ticker: str) -> str:
        stem = safe_ticker_stem(ticker, normalize=self.ticker_normalize)
        return self.file_rel(f"{stem}.parquet")

    def exists(self, relative: str) -> bool:
        return storage_exists(relative, ctx=self.ctx)

    def read_df(
        self,
        relative: str,
        *,
        columns: list[str] | None = None,
    ) -> pd.DataFrame | None:
        if not self.exists(relative):
            return None
        return read_parquet(relative, columns=columns, ctx=self.ctx)

    def write_df(
        self,
        relative: str,
        df: pd.DataFrame,
        *,
        schema: pa.Schema | None = None,
        atomic: bool = True,
    ) -> None:
        if schema is not None:
            write_parquet_table(
                df,
                relative,
                schema=schema,
                atomic=atomic,
                ctx=self.ctx,
            )
            return
        write_parquet(df, relative, atomic=atomic, ctx=self.ctx)

    def merge_write(
        self,
        relative: str,
        new_df: pd.DataFrame,
        schema: pa.Schema,
        dedup_cols: list[str],
        *,
        sort_cols: list[str] | None = None,
    ) -> int:
        """Read-merge-dedup-write pattern used by per-ticker stores."""
        if new_df.empty:
            return 0

        existing = self.read_df(relative)
        if existing is not None and not existing.empty:
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined = combined.drop_duplicates(subset=dedup_cols, keep="last")
        if sort_cols:
            combined = combined.sort_values(sort_cols)
        combined = combined.reset_index(drop=True)

        ordered = combined[[f.name for f in schema]]
        self.write_df(relative, ordered, schema=schema)
        return len(combined)

    def iter_parquet_rels(self) -> list[str]:
        """Relative paths of non-underscore Parquet files in this store."""
        prefix = self.subdir or ""
        paths = list_files(prefix, suffix=".parquet", ctx=self.ctx)
        rels: list[str] = []
        for path in paths:
            name = Path(path).name
            if name.startswith("_"):
                continue
            rels.append(self.file_rel(name))
        return sorted(rels)

    def list_ticker_stems(self) -> list[str]:
        stems: list[str] = []
        for rel in self.iter_parquet_rels():
            stem = Path(rel).stem
            stems.append(stem.upper() if self.upper_stems else stem)
        return stems

    def has_any_parquet(self) -> bool:
        prefix = self.subdir or ""
        return bool(list_files(prefix, suffix=".parquet", ctx=self.ctx))

    def parquet_rows(self, relative: str) -> int:
        if not self.exists(relative):
            return 0
        return parquet_num_rows(relative, ctx=self.ctx)
