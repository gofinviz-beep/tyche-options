"""One-time local ``data/`` → GCS upload helpers (GCP-E)."""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import structlog

from tyche.ops.run_manifest import new_run_id
from tyche.storage import read_parquet, write_json
from tyche.storage.paths import StorageContext, get_gcs_filesystem, join_uri

logger = structlog.get_logger()

_SKIP_DIR_PARTS = frozenset({"__pycache__", ".pytest_cache", ".mypy_cache"})
_SKIP_BASENAMES = frozenset({".DS_Store"})


@dataclass(frozen=True)
class LocalDataFile:
    """A file under the local data root eligible for upload."""

    absolute: Path
    relative: str
    size_bytes: int


@dataclass
class MigrationConfig:
    """Knobs for ``migrate_data_to_gcs``."""

    local_data_root: Path
    gcs_uri: str
    dry_run: bool = True
    include_prefixes: tuple[str, ...] = ()
    delete_extra: bool = False
    verify_sample_count: int = 3
    run_id: str | None = None


@dataclass
class MigrationResult:
    """Outcome of a migration run."""

    run_id: str
    dry_run: bool
    file_count: int
    total_bytes: int
    skipped_count: int
    uploaded_count: int
    errors: list[str] = field(default_factory=list)
    manifest_rel: str = ""
    sample_readback: list[dict[str, object]] = field(default_factory=list)


def parse_gcs_uri(uri: str) -> tuple[str, str]:
    """Split ``gs://bucket[/optional/prefix]`` into bucket and object prefix."""
    cleaned = (uri or "").strip()
    if not cleaned.startswith("gs://"):
        raise ValueError(f"gcs_uri must start with gs:// (got {uri!r})")
    rest = cleaned[5:].strip("/")
    if not rest:
        raise ValueError("gcs_uri must include a bucket name")
    bucket, _, prefix = rest.partition("/")
    if not bucket:
        raise ValueError(f"Invalid gcs_uri: {uri!r}")
    return bucket, prefix


def gcs_object_uri(gcs_uri: str, relative_path: str) -> str:
    """Build the destination ``gs://`` URI for a relative data path."""
    bucket, prefix = parse_gcs_uri(gcs_uri)
    rel = relative_path.replace("\\", "/").lstrip("/")
    if prefix:
        return f"gs://{bucket}/{prefix}/{rel}"
    return f"gs://{bucket}/{rel}"


def should_skip_relative(relative: str) -> bool:
    """Return True for temp/cache artifacts that must not be uploaded."""
    parts = relative.replace("\\", "/").split("/")
    if any(part in _SKIP_DIR_PARTS for part in parts):
        return True
    basename = parts[-1] if parts else relative
    if basename in _SKIP_BASENAMES:
        return True
    if basename.startswith(".") and basename not in {".gitkeep"}:
        return True
    if ".tmp." in basename:
        return True
    return False


def discover_local_files(
    local_root: Path,
    *,
    include_prefixes: tuple[str, ...] = (),
) -> list[LocalDataFile]:
    """Walk *local_root* and return upload candidates sorted by relative path."""
    root = local_root.resolve()
    if not root.is_dir():
        raise FileNotFoundError(f"Local data root not found: {root}")

    normalized_includes = tuple(
        p.strip().strip("/").replace("\\", "/")
        for p in include_prefixes
        if p.strip()
    )

    files: list[LocalDataFile] = []
    skipped = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if should_skip_relative(rel):
            skipped += 1
            continue
        if normalized_includes and not any(
            rel == prefix or rel.startswith(f"{prefix}/")
            for prefix in normalized_includes
        ):
            skipped += 1
            continue
        files.append(
            LocalDataFile(
                absolute=path,
                relative=rel,
                size_bytes=path.stat().st_size,
            )
        )

    logger.info(
        "migration_discover_complete",
        root=str(root),
        files=len(files),
        skipped=skipped,
        includes=list(normalized_includes) or "all",
    )
    return files


def _parquet_candidates(files: list[LocalDataFile]) -> list[LocalDataFile]:
    return [f for f in files if f.relative.endswith(".parquet")]


def verify_sample_readback(
    *,
    gcs_uri: str,
    files: list[LocalDataFile],
    sample_count: int,
) -> list[dict[str, object]]:
    """Read a random sample of uploaded Parquet files from GCS."""
    candidates = _parquet_candidates(files)
    if not candidates:
        return []

    bucket, prefix = parse_gcs_uri(gcs_uri)
    ctx = StorageContext(
        backend="gcs",
        local_root=Path("data"),
        gcs_bucket=bucket,
        gcs_prefix=prefix,
    )

    sample = random.sample(
        candidates,
        k=min(sample_count, len(candidates)),
    )
    results: list[dict[str, object]] = []
    for item in sample:
        entry: dict[str, object] = {"relative": item.relative, "ok": False}
        try:
            df = read_parquet(item.relative, ctx=ctx)
            entry["ok"] = True
            entry["rows"] = len(df)
            entry["columns"] = len(df.columns)
        except Exception as exc:
            entry["error"] = str(exc)
        results.append(entry)
    return results


def run_data_migration(config: MigrationConfig) -> MigrationResult:
    """Upload local data to GCS (or dry-run) and write a migration manifest."""
    run_id = config.run_id or new_run_id()
    started_at = datetime.now(timezone.utc).isoformat()
    files = discover_local_files(
        config.local_data_root,
        include_prefixes=config.include_prefixes,
    )
    total_bytes = sum(f.size_bytes for f in files)

    if config.delete_extra:
        logger.warning(
            "migration_delete_extra_not_implemented",
            delete_extra=True,
        )

    errors: list[str] = []
    uploaded = 0
    fs = None if config.dry_run else get_gcs_filesystem()

    for item in files:
        dest = gcs_object_uri(config.gcs_uri, item.relative)
        if config.dry_run:
            uploaded += 1
            continue
        assert fs is not None
        try:
            fs.put(str(item.absolute), dest)
            uploaded += 1
            if uploaded % 500 == 0:
                logger.info("migration_upload_progress", uploaded=uploaded, total=len(files))
        except Exception as exc:
            msg = f"{item.relative}: {exc}"
            errors.append(msg)
            logger.error("migration_upload_failed", relative=item.relative, error=str(exc))

    sample_readback: list[dict[str, object]] = []
    if not config.dry_run and not errors:
        sample_readback = verify_sample_readback(
            gcs_uri=config.gcs_uri,
            files=files,
            sample_count=config.verify_sample_count,
        )
        for entry in sample_readback:
            if not entry.get("ok"):
                errors.append(
                    f"readback failed for {entry.get('relative')}: {entry.get('error')}",
                )

    ended_at = datetime.now(timezone.utc).isoformat()
    status = "dry_run" if config.dry_run else ("failed" if errors else "success")

    manifest = {
        "run_id": run_id,
        "job_name": "migrate_data_to_gcs",
        "started_at": started_at,
        "ended_at": ended_at,
        "status": status,
        "dry_run": config.dry_run,
        "local_root": str(config.local_data_root.resolve()),
        "gcs_uri": config.gcs_uri,
        "include_prefixes": list(config.include_prefixes),
        "delete_extra": config.delete_extra,
        "file_count": len(files),
        "total_bytes": total_bytes,
        "uploaded_count": uploaded,
        "errors": errors,
        "sample_readback": sample_readback,
    }
    manifest_rel = join_uri("runs", "migration", run_id, "manifest.json")
    manifest_path = config.local_data_root / manifest_rel
    write_json(manifest, str(manifest_path), atomic=True)

    if not config.dry_run and fs is not None and not errors:
        try:
            fs.put(str(manifest_path), gcs_object_uri(config.gcs_uri, manifest_rel))
        except Exception as exc:
            errors.append(f"manifest upload: {exc}")
            manifest["status"] = "failed"
            manifest["errors"] = errors
            write_json(manifest, str(manifest_path), atomic=True)

    logger.info(
        "migration_complete",
        run_id=run_id,
        status=status,
        files=len(files),
        bytes=total_bytes,
        dry_run=config.dry_run,
        errors=len(errors),
    )
    return MigrationResult(
        run_id=run_id,
        dry_run=config.dry_run,
        file_count=len(files),
        total_bytes=total_bytes,
        skipped_count=0,
        uploaded_count=uploaded,
        errors=errors,
        manifest_rel=manifest_rel,
        sample_readback=sample_readback,
    )
