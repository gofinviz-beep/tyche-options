"""One-time upload of ``backend/data/`` to GCS (GCP-E).

This script is **not** run automatically during development. Use it when the
GCS bucket exists and ADC is configured (``gcloud auth application-default login``).

Dry-run (safe default — counts files/bytes, writes a local manifest only):
    .venv/bin/python scripts/migrate_data_to_gcs.py \\
        --gcs-uri gs://tyche-data-prod --dry-run

Real upload:
    .venv/bin/python scripts/migrate_data_to_gcs.py \\
        --gcs-uri gs://tyche-data-prod --execute

Subset upload (top-level prefixes under ``data/``):
    .venv/bin/python scripts/migrate_data_to_gcs.py \\
        --gcs-uri gs://tyche-data-prod \\
        --include published,alpha_signals_sustained.parquet --dry-run
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.ops.data_migration import MigrationConfig, run_data_migration  # noqa: E402

logger = structlog.get_logger()


def _human_bytes(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024**2:
        return f"{n / 1024:.1f} KB"
    if n < 1024**3:
        return f"{n / 1024**2:.1f} MB"
    return f"{n / 1024**3:.2f} GB"


def main() -> None:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Upload local backend/data to GCS (one-time migration)",
    )
    parser.add_argument(
        "--local-data-root",
        default=settings.data_dir,
        help="Local data directory (default: settings.data_dir)",
    )
    parser.add_argument(
        "--gcs-uri",
        required=True,
        help="Destination gs://bucket or gs://bucket/prefix",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--dry-run",
        action="store_true",
        dest="dry_run",
        help="Count files/bytes only; do not upload (default)",
    )
    mode.add_argument(
        "--execute",
        action="store_false",
        dest="dry_run",
        help="Perform the upload and verify Parquet readback",
    )
    parser.set_defaults(dry_run=True)
    parser.add_argument(
        "--include",
        default="",
        help=(
            "Comma-separated relative prefixes to include "
            "(e.g. ohlcv_daily,fundamentals,published). Default: all."
        ),
    )
    parser.add_argument(
        "--delete-extra",
        action="store_true",
        help="Delete GCS objects not present locally (NOT implemented; ignored)",
    )
    parser.add_argument(
        "--verify-sample",
        type=int,
        default=3,
        metavar="N",
        help="Random Parquet files to read back after upload (default: 3)",
    )
    args = parser.parse_args()

    includes = tuple(p.strip() for p in args.include.split(",") if p.strip())
    local_root = Path(args.local_data_root).resolve()
    dry_run = args.dry_run

    if dry_run:
        logger.info("migration_dry_run_mode")
    else:
        logger.warning("migration_execute_mode", gcs_uri=args.gcs_uri)

    config = MigrationConfig(
        local_data_root=local_root,
        gcs_uri=args.gcs_uri.strip(),
        dry_run=dry_run,
        include_prefixes=includes,
        delete_extra=args.delete_extra,
        verify_sample_count=max(0, args.verify_sample),
    )

    try:
        result = run_data_migration(config)
    except FileNotFoundError as exc:
        logger.error("migration_failed", error=str(exc))
        sys.exit(1)

    print(
        f"{'[DRY RUN] ' if result.dry_run else ''}"
        f"Discovered {result.file_count} files ({_human_bytes(result.total_bytes)})"
    )
    print(f"Manifest: {local_root / result.manifest_rel}")
    if result.sample_readback:
        print("Readback sample:")
        for entry in result.sample_readback:
            status = "ok" if entry.get("ok") else "FAIL"
            print(f"  {status}: {entry.get('relative')} ({entry.get('rows', '?')} rows)")

    if result.errors:
        for err in result.errors:
            logger.error("migration_error", detail=err)
        sys.exit(1)

    if dry_run:
        print("Dry run complete. Re-run with --execute to upload.")


if __name__ == "__main__":
    main()
