"""Path resolution for local and GCS data backends."""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from tyche.exceptions import DataStoreError

if TYPE_CHECKING:
    from tyche.config import TycheSettings

DataBackend = Literal["local", "gcs"]


@dataclass(frozen=True)
class StorageContext:
    """Resolved storage backend and roots for path operations."""

    backend: DataBackend
    local_root: Path
    gcs_bucket: str | None = None
    gcs_prefix: str = ""

    def __post_init__(self) -> None:
        if self.backend == "gcs" and not self.gcs_bucket:
            raise DataStoreError(
                "gcs_bucket is required when data_backend=gcs "
                "(set TYCHE_GCS_BUCKET)"
            )


def _normalize_relative(relative_path: str) -> str:
    """Normalize a relative path to forward-slash form without leading slashes."""
    cleaned = relative_path.replace("\\", "/").strip()
    while cleaned.startswith("./"):
        cleaned = cleaned[2:]
    return cleaned.lstrip("/")


def is_gcs_path(path: str | Path) -> bool:
    """Return True when *path* is a ``gs://`` URI."""
    return str(path).startswith("gs://")


def join_uri(*parts: str) -> str:
    """Join path segments with forward slashes.

    Works for ``gs://`` URIs and relative POSIX-style paths.
    """
    if not parts:
        return ""

    first = str(parts[0]).replace("\\", "/")
    rest = [_normalize_relative(str(p)) for p in parts[1:] if str(p)]

    if is_gcs_path(first):
        base = first.rstrip("/")
        if not rest:
            return base
        tail = "/".join(p for p in rest if p)
        return f"{base}/{tail}" if tail else base

    segments = [_normalize_relative(first)] + rest
    return "/".join(s for s in segments if s)


def storage_context_from_settings(settings: TycheSettings) -> StorageContext:
    """Build a :class:`StorageContext` from application settings."""
    backend = settings.data_backend
    if backend not in ("local", "gcs"):
        raise DataStoreError(
            f"Invalid data_backend={backend!r}; expected 'local' or 'gcs'"
        )
    bucket = (settings.gcs_bucket or "").strip() or None
    prefix = (settings.gcs_prefix or "").strip().strip("/")
    return StorageContext(
        backend=backend,
        local_root=Path(settings.data_dir),
        gcs_bucket=bucket,
        gcs_prefix=prefix,
    )


def get_storage_context(ctx: StorageContext | None = None) -> StorageContext:
    """Return *ctx* or build one from :func:`tyche.config.get_settings`."""
    if ctx is not None:
        return ctx
    from tyche.config import get_settings

    return storage_context_from_settings(get_settings())


def resolve_data_path(
    relative_path: str,
    *,
    ctx: StorageContext | None = None,
) -> str | Path:
    """Resolve *relative_path* to a local :class:`Path` or ``gs://`` URI."""
    context = get_storage_context(ctx)
    rel = _normalize_relative(relative_path)
    if not rel:
        raise DataStoreError("relative_path must not be empty")

    if context.backend == "local":
        return context.local_root / Path(rel)

    bucket = context.gcs_bucket
    assert bucket is not None  # validated in StorageContext.__post_init__
    if context.gcs_prefix:
        object_path = join_uri(context.gcs_prefix, rel)
    else:
        object_path = rel
    return f"gs://{bucket}/{object_path}"


def coerce_storage_path(
    path_or_relative: str,
    *,
    ctx: StorageContext | None = None,
) -> str | Path:
    """Accept absolute paths/URIs or resolve relative paths via *ctx*."""
    if is_gcs_path(path_or_relative):
        return path_or_relative
    path = Path(path_or_relative)
    if path.is_absolute():
        return path
    return resolve_data_path(path_or_relative, ctx=ctx)


@lru_cache(maxsize=1)
def get_gcs_filesystem():
    """Return a cached :class:`gcsfs.GCSFileSystem` (ADC / workload identity)."""
    import gcsfs

    return gcsfs.GCSFileSystem()
