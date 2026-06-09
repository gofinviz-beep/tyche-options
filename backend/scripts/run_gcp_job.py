#!/usr/bin/env python3
"""Cloud Run Job entrypoint (GCP-F).

Loads secrets from Secret Manager, runs one batch job, writes a run manifest.

Usage (from ``backend/`` or container WORKDIR):
    python scripts/run_gcp_job.py ingest-data
    python scripts/run_gcp_job.py ingest-demand-data
    python scripts/run_gcp_job.py ingest-news
    python scripts/run_gcp_job.py ingest-edgar
    python scripts/run_gcp_job.py publish-signals
    python scripts/run_gcp_job.py nightly-pipeline
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.ops.gcp_jobs import JOB_NAMES, execute_job  # noqa: E402
from tyche.ops.gcp_secrets import bootstrap_gcp_runtime  # noqa: E402

logger = structlog.get_logger()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a Tyche Cloud Run batch job")
    parser.add_argument(
        "job",
        choices=JOB_NAMES,
        help="Job name (matches Cloud Run Job id suffix)",
    )
    parser.add_argument("--run-id", default=None, help="Optional run id override")
    args = parser.parse_args()

    loaded = bootstrap_gcp_runtime()
    if loaded:
        logger.info("gcp_secrets_hydrated", count=len(loaded), secrets=loaded)

    settings = get_settings()
    if settings.data_backend != "gcs":
        logger.warning(
            "gcp_job_not_gcs_backend",
            data_backend=settings.data_backend,
            hint="Set TYCHE_DATA_BACKEND=gcs for Cloud Run Jobs",
        )
    if not settings.gcs_bucket:
        logger.error("gcp_job_missing_bucket")
        sys.exit(1)

    logger.info(
        "gcp_job_start",
        job=args.job,
        bucket=settings.gcs_bucket,
        prefix=settings.gcs_prefix or "(root)",
        run_env=settings.run_env,
    )

    try:
        result = asyncio.run(execute_job(args.job, run_id=args.run_id))
    except Exception as exc:
        import traceback

        traceback.print_exc()
        logger.error("gcp_job_failed", job=args.job, error=str(exc), exc_info=True)
        sys.exit(1)

    logger.info(
        "gcp_job_complete",
        job=result.job_name,
        status=result.status,
        run_id=result.run_id,
        manifest=result.manifest_rel,
    )
    print(f"OK {result.job_name} run_id={result.run_id} manifest={result.manifest_rel}")


if __name__ == "__main__":
    main()
