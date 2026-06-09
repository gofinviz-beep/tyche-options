"""Publish route-level JSON artifacts for the UI/API cache layer (GCP-C).

Reads compact signal snapshots (alpha Parquet, optional SQLite conviction/news)
and writes ``published/routes/*.json`` plus ``published/manifest.json``.

Usage (from ``backend/``):
    .venv/bin/python scripts/publish_signals.py
    .venv/bin/python scripts/publish_signals.py --data-dir data --alpha-limit 200
    .venv/bin/python scripts/publish_signals.py --no-strict   # allow missing optional upstream
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.exceptions import PublishError  # noqa: E402
from tyche.storage.paths import storage_context_from_settings  # noqa: E402
from tyche.workflow.publish_signals import PublishConfig, run_publish_signals  # noqa: E402

logger = structlog.get_logger()


def main() -> None:
    parser = argparse.ArgumentParser(description="Publish route-level signal JSON artifacts")
    parser.add_argument("--data-dir", default=None, help="Data root (default: settings.data_dir)")
    parser.add_argument(
        "--alpha-limit",
        type=int,
        default=500,
        help="Max alpha rows in stocks_alpha.json (default: 500)",
    )
    parser.add_argument(
        "--conviction-limit",
        type=int,
        default=5000,
        help="Max conviction snapshot rows (default: 5000)",
    )
    parser.add_argument(
        "--intelligence-limit",
        type=int,
        default=500,
        help="Max intelligence rows per route (default: 500)",
    )
    parser.add_argument(
        "--no-strict",
        action="store_true",
        help="Do not fail when required alpha upstream is missing (dev only)",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="Optional run id (default: generated UTC timestamp)",
    )
    args = parser.parse_args()

    settings = get_settings()
    data_dir = args.data_dir or settings.data_dir
    ctx = storage_context_from_settings(settings)

    # Standalone CLI: register SQLite engines so conviction/news snapshots can load.
    import asyncio

    from tyche.persistence.database import ensure_news_db, init_conviction_db

    init_conviction_db(settings.db_dir)
    if settings.data_backend != "gcs":
        asyncio.run(ensure_news_db(settings.db_dir))

    config = PublishConfig(
        data_dir=data_dir,
        ctx=ctx,
        run_id=args.run_id,
        alpha_row_limit=args.alpha_limit,
        conviction_row_limit=args.conviction_limit,
        intelligence_row_limit=args.intelligence_limit,
        strict=not args.no_strict,
        max_stale_minutes=settings.published_max_age_minutes,
        settings=settings,
    )

    try:
        result = run_publish_signals(config)
    except PublishError as exc:
        logger.error("publish_signals_failed", error=str(exc))
        sys.exit(1)

    ok = sum(1 for r in result.routes if r.status == "ok")
    logger.info(
        "publish_signals_done",
        run_id=result.run_id,
        routes=len(result.routes),
        ok_routes=ok,
        manifest=result.manifest_rel,
        run_manifest=result.run_manifest_rel,
        warnings=result.warnings,
    )
    print(
        f"Published {len(result.routes)} routes "
        f"({ok} ok) → {result.manifest_rel} "
        f"(run_id={result.run_id})"
    )


if __name__ == "__main__":
    main()
