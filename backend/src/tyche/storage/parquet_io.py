"""Parquet read/write with local and GCS backends."""

from __future__ import annotations

import os
import uuid
from pathlib import Path

import io

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from tyche.exceptions import DataStoreError
from tyche.storage.paths import (
    StorageContext,
    coerce_storage_path,
    get_gcs_filesystem,
    get_storage_context,
    is_gcs_path,
    resolve_data_path,
)

logger = structlog.get_logger()


def _temp_path(target: str | Path) -> str | Path:
    token = uuid.uuid4().hex
    target_str = str(target)
    if is_gcs_path(target_str):
        return f"{target_str}.tmp.{token}"
    path = Path(target_str)
    return path.with_name(f"{path.name}.tmp.{token}")


def _promote_local(temp: Path, final: Path) -> None:
    final.parent.mkdir(parents=True, exist_ok=True)
    os.replace(temp, final)


def _promote_gcs(temp_uri: str, final_uri: str) -> None:
    fs = get_gcs_filesystem()
    fs.cp(temp_uri, final_uri)
    if fs.exists(temp_uri):
        fs.rm(temp_uri)


def read_parquet(
    path_or_relative: str,
    *,
    columns: list[str] | None = None,
    ctx: StorageContext | None = None,
) -> pd.DataFrame:
    """Read a Parquet file from local disk or GCS."""
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    try:
        return pd.read_parquet(path, columns=columns)
    except FileNotFoundError as exc:
        raise DataStoreError(f"Parquet not found: {path}") from exc
    except OSError as exc:
        raise DataStoreError(f"Failed to read Parquet {path}: {exc}") from exc


def write_parquet(
    df: pd.DataFrame,
    path_or_relative: str,
    *,
    atomic: bool = True,
    ctx: StorageContext | None = None,
) -> None:
    """Write a Parquet file locally or to GCS.

    When *atomic* is True, writes to a temp object then promotes to the final path.
    """
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    if not atomic:
        _write_parquet_direct(df, path)
        return

    temp = _temp_path(path)
    try:
        _write_parquet_direct(df, temp)
        if is_gcs_path(path):
            _promote_gcs(str(temp), str(path))
        else:
            _promote_local(Path(temp), Path(path))
    except Exception:
        _cleanup_temp(temp)
        raise


def _write_parquet_direct(df: pd.DataFrame, path: str | Path) -> None:
    path_str = str(path)
    if is_gcs_path(path_str):
        df.to_parquet(path_str, index=False)
        return
    local = Path(path_str)
    local.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(local, index=False)


def _write_table_direct(table: pa.Table, path: str | Path) -> None:
    path_str = str(path)
    if is_gcs_path(path_str):
        buf = io.BytesIO()
        pq.write_table(table, buf, compression="snappy")
        with get_gcs_filesystem().open(path_str, "wb") as handle:
            handle.write(buf.getvalue())
        return
    local = Path(path_str)
    local.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, local, compression="snappy")


def write_parquet_table(
    df: pd.DataFrame,
    path_or_relative: str,
    *,
    schema: pa.Schema,
    atomic: bool = True,
    ctx: StorageContext | None = None,
) -> None:
    """Write a DataFrame with an enforced PyArrow schema."""
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    if not atomic:
        _write_table_direct(table, path)
        return

    temp = _temp_path(path)
    try:
        _write_table_direct(table, temp)
        if is_gcs_path(path):
            _promote_gcs(str(temp), str(path))
        else:
            _promote_local(Path(temp), Path(path))
    except Exception:
        _cleanup_temp(temp)
        raise


def parquet_num_rows(
    path_or_relative: str,
    *,
    ctx: StorageContext | None = None,
) -> int:
    """Return row count from Parquet footer metadata without loading data."""
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    try:
        if is_gcs_path(path):
            with get_gcs_filesystem().open(str(path), "rb") as handle:
                return pq.read_metadata(handle).num_rows
        return pq.read_metadata(path).num_rows
    except (OSError, FileNotFoundError):
        return 0


def _cleanup_temp(temp: str | Path) -> None:
    temp_str = str(temp)
    try:
        if is_gcs_path(temp_str):
            fs = get_gcs_filesystem()
            if fs.exists(temp_str):
                fs.rm(temp_str)
        else:
            p = Path(temp_str)
            if p.exists():
                p.unlink()
    except OSError:
        logger.warning("storage_temp_cleanup_failed", path=temp_str)


def exists(
    path_or_relative: str,
    *,
    ctx: StorageContext | None = None,
) -> bool:
    """Return whether a Parquet or other object exists at *path_or_relative*."""
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    if is_gcs_path(path):
        return bool(get_gcs_filesystem().exists(str(path)))
    return Path(path).exists()


def list_files(
    prefix_or_relative: str,
    suffix: str | None = None,
    *,
    ctx: StorageContext | None = None,
) -> list[str]:
    """List files under a prefix, optionally filtered by *suffix*.

    Returns resolved paths (local :class:`Path` strings or ``gs://`` URIs).
    """
    context = get_storage_context(ctx)
    base = resolve_data_path(prefix_or_relative, ctx=context)
    suffix = suffix or ""

    if context.backend == "gcs":
        base_uri = str(base).rstrip("/") + "/"
        fs = get_gcs_filesystem()
        try:
            entries = fs.find(base_uri)
        except FileNotFoundError:
            return []
        paths = [e for e in entries if not e.endswith("/")]
        if suffix:
            paths = [p for p in paths if p.endswith(suffix)]
        return sorted(paths)

    base_path = Path(base)
    if not base_path.exists():
        return []
    if base_path.is_file():
        candidates = [base_path]
    else:
        candidates = [p for p in base_path.rglob("*") if p.is_file()]

    if suffix:
        candidates = [p for p in candidates if p.name.endswith(suffix)]

    return sorted(str(p) for p in candidates)
