"""JSON read/write with local and GCS backends."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tyche.exceptions import DataStoreError
from tyche.storage.paths import (
    StorageContext,
    coerce_storage_path,
    get_gcs_filesystem,
    is_gcs_path,
)
from tyche.storage.parquet_io import _cleanup_temp, _promote_gcs, _promote_local, _temp_path


def read_json(
    path_or_relative: str,
    *,
    ctx: StorageContext | None = None,
) -> dict | list:
    """Read JSON from local disk or GCS."""
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    try:
        if is_gcs_path(path):
            with get_gcs_filesystem().open(str(path), "r") as handle:
                return json.load(handle)
        with Path(path).open(encoding="utf-8") as handle:
            return json.load(handle)
    except FileNotFoundError as exc:
        raise DataStoreError(f"JSON not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise DataStoreError(f"Invalid JSON at {path}: {exc}") from exc
    except OSError as exc:
        raise DataStoreError(f"Failed to read JSON {path}: {exc}") from exc


def write_json(
    obj: Any,
    path_or_relative: str,
    *,
    atomic: bool = True,
    indent: int | None = 2,
    ctx: StorageContext | None = None,
) -> None:
    """Write JSON locally or to GCS.

    When *atomic* is True, writes to a temp object then promotes to the final path.
    """
    path = coerce_storage_path(path_or_relative, ctx=ctx)
    payload = json.dumps(obj, indent=indent, default=str)
    if not atomic:
        _write_json_direct(payload, path)
        return

    temp = _temp_path(path)
    try:
        _write_json_direct(payload, temp)
        if is_gcs_path(path):
            _promote_gcs(str(temp), str(path))
        else:
            _promote_local(Path(temp), Path(path))
    except Exception:
        _cleanup_temp(temp)
        raise


def _write_json_direct(payload: str, path: str | Path) -> None:
    path_str = str(path)
    if is_gcs_path(path_str):
        with get_gcs_filesystem().open(path_str, "w") as handle:
            handle.write(payload)
        return
    local = Path(path_str)
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(payload, encoding="utf-8")
