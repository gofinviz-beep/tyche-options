"""Cloud Run Job runners with run manifests (GCP-F)."""

from __future__ import annotations

import asyncio
import subprocess
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import structlog

from tyche.config import TycheSettings, get_settings
from tyche.exceptions import PublishError
from tyche.ops.audit_snapshots import run_audit_snapshots
from tyche.ops.run_manifest import RunManifest, new_run_id
from tyche.storage.paths import StorageContext, storage_context_from_settings
from tyche.workflow.alpha_batch import run_alpha_batch
from tyche.workflow.demand_data import ingest_demand_data
from tyche.workflow.publish_signals import PublishConfig, run_publish_signals

logger = structlog.get_logger()

JOB_NAMES = (
    "ingest-data",
    "ingest-options-flatfiles",
    "ingest-demand-data",
    "ingest-news",
    "ingest-edgar",
    "alpha-batch",
    "run-demand-gate",
    "publish-signals",
    "audit-snapshots",
    "nightly-pipeline",
)

NIGHTLY_PIPELINE_STEPS: tuple[str, ...] = (
    "ingest-data",
    "ingest-options-flatfiles",
    "ingest-demand-data",
    "ingest-news",
    "ingest-edgar",
    "alpha-batch",
    "run-demand-gate",
    "publish-signals",
    "audit-snapshots",
)

# Demand gate promotes models; nightly publish can proceed if it fails.
_OPTIONAL_PIPELINE_STEPS = frozenset({"run-demand-gate"})


@dataclass(frozen=True)
class JobResult:
    job_name: str
    run_id: str
    status: str
    manifest_rel: str | None
    summary: dict[str, Any]


def _backend_dir() -> Path:
    return Path(__file__).resolve().parents[3]


def _run_subprocess(
    cmd: list[str],
    *,
    cwd: Path | None = None,
) -> tuple[int, str]:
    """Run a script and stream stdout to the container log (Cloud Logging).

    Previously used ``capture_output=True``, which hid all subprocess logs until
    the job finished — flatfiles/IV could look idle for hours on GCS.
    """
    proc = subprocess.Popen(
        cmd,
        cwd=str(cwd or _backend_dir()),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    lines: list[str] = []
    assert proc.stdout is not None
    for line in proc.stdout:
        lines.append(line)
        # Pass through immediately so Cloud Run logs show live progress.
        sys.stdout.write(line)
        sys.stdout.flush()
    proc.wait()
    output = "".join(lines)
    return proc.returncode or 0, output


async def run_ingest_data(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Incremental OHLCV refresh + market-cap reprice (nightly)."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="ingest_data",
        run_id=rid,
        data_backend=ctx.backend,
    )

    from tyche.market_data.data_store import (
        OHLCVStore,
        TickerMetaStore,
        bootstrap_ohlcv,
        recompute_market_caps_from_shares,
    )
    from tyche.market_data.polygon import PolygonClient

    if not settings.polygon_api_key:
        manifest.errors.append("TYCHE_POLYGON_API_KEY missing")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("ingest-data", rid, "failed", rel, {"error": "no_polygon_key"})

    polygon = PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=settings.polygon_rate_limit_rpm,
    )
    store = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)

    from tyche.ops.job_progress import log_job_phase

    try:
        log_job_phase("ingest-data", "bootstrap_ohlcv", tickers=store.get_ticker_count())
        result = await bootstrap_ohlcv(
            polygon,
            store,
            days=5,
            include_today=True,
            progress_job="ingest-data",
        )
        log_job_phase("ingest-data", "bootstrap_ohlcv", status="complete", **result)
        log_job_phase("ingest-data", "recompute_market_caps")
        caps_updated = await asyncio.to_thread(
            recompute_market_caps_from_shares,
            meta,
            store,
            progress_job="ingest-data",
        )
        log_job_phase(
            "ingest-data",
            "recompute_market_caps",
            status="complete",
            updated=caps_updated,
        )
        manifest.output_paths = ["ohlcv_daily/", "ticker_meta.parquet"]
        manifest.extra = {**result, "market_caps_repriced": caps_updated}
        manifest.finish(status="success")
        rel = manifest.write(ctx=ctx)
        return JobResult("ingest-data", rid, "success", rel, manifest.extra)
    except Exception as exc:
        manifest.errors.append(str(exc))
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        raise


async def run_ingest_options_flatfiles(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """S3 flatfile options ingest + IV pipeline."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="ingest_options_flatfiles",
        run_id=rid,
        data_backend=ctx.backend,
    )

    if not settings.massive_s3_access_key or not settings.massive_s3_secret_key:
        manifest.errors.append("Massive S3 credentials missing")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult(
            "ingest-options-flatfiles",
            rid,
            "failed",
            rel,
            {"error": "no_s3_credentials"},
        )

    script = _backend_dir() / "scripts" / "ingest_options_flatfiles.py"
    cmd = [
        sys.executable,
        str(script),
        "--from-ohlcv",
        "--include-today",
        "--days-back",
        "3",
        "--concurrency",
        "8",
        "--min-market-cap",
        str(int(settings.flatfile_ingest_min_market_cap)),
    ]
    manifest.input_paths = ["ohlcv_daily/"]
    manifest.output_paths = [
        "options_history/",
        "options_iv/",
        "derived/",
    ]

    from tyche.ops.job_progress import log_job_phase

    log_job_phase("ingest-options-flatfiles", "subprocess", cmd=" ".join(cmd[-6:]))
    code, output = await asyncio.to_thread(_run_subprocess, cmd)
    manifest.extra["output_tail"] = output[-2000:] if len(output) > 2000 else output
    if code != 0:
        manifest.errors.append(f"exit_code={code}")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        raise RuntimeError(f"ingest_options_flatfiles failed (exit {code})")
    log_job_phase("ingest-options-flatfiles", "subprocess", status="complete")

    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("ingest-options-flatfiles", rid, "success", rel, manifest.extra)


async def run_ingest_demand_data(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Fundamentals, estimates, estimate_snapshots, short interest, guidance."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="ingest_demand_data",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.output_paths = [
        "fundamentals/",
        "estimates/",
        "estimate_snapshots/",
        "short_interest/",
        "catalyst_signals/",
    ]

    if not settings.polygon_api_key and not settings.finnhub_api_key:
        manifest.errors.append("Finnhub and Polygon credentials missing")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("ingest-demand-data", rid, "failed", rel, {"error": "no_credentials"})

    counts = await ingest_demand_data(
        settings,
        do_fundamentals=settings.fundamentals_refresh_enabled,
        do_estimates=settings.estimates_refresh_enabled,
        do_short_interest=settings.short_interest_refresh_enabled,
        do_guidance=settings.guidance_refresh_enabled,
    )
    manifest.tickers_requested = counts.get("tickers", 0)
    manifest.tickers_succeeded = counts.get("estimates", 0)
    manifest.extra = counts
    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("ingest-demand-data", rid, "success", rel, counts)


def run_alpha_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Score directional alpha and persist peak + sustained snapshots."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="alpha_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        "ohlcv_daily/",
        "fundamentals/",
        "estimates/",
        "estimate_snapshots/",
        "short_interest/",
        "catalyst_signals/",
    ]
    variants = ["peak", "sustained"] if settings.alpha_sustained_enabled else ["peak"]
    floor = settings.alpha_min_market_cap_millions * 1_000_000

    from tyche.strategy.alpha_engine import build_alpha_score_engine

    engine = build_alpha_score_engine(
        discovery_enabled=settings.alpha_discovery_enabled,
        percentile_signals=settings.alpha_percentile_signals_enabled,
        demand_adjusted_extension=settings.alpha_demand_adjusted_extension_enabled,
        demand_mult_ceil_discovery=settings.alpha_demand_mult_ceil_discovery,
    )
    from tyche.ops.job_progress import log_job_phase

    log_job_phase("alpha-batch", "execute", variants=variants, min_market_cap=floor)
    summary = run_alpha_batch(
        data_dir=settings.data_dir,
        min_market_cap=floor,
        variants=variants,
        settings=settings,
        engine=engine,
    )
    log_job_phase(
        "alpha-batch",
        "execute",
        status=summary.get("status", "ok"),
        signals=summary.get("signals", 0),
    )
    manifest.output_paths = [
        "alpha_signals.parquet",
        "alpha_signals_sustained.parquet",
    ]
    manifest.extra = summary
    if summary.get("status") == "empty":
        manifest.warnings.append("alpha_batch_no_features")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("alpha-batch", rid, "failed", rel, summary)

    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("alpha-batch", rid, "success", rel, summary)


async def run_ingest_news(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """News ingest + Gemini classification + signal export to GCS."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="ingest_news",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = ["ohlcv_daily/", "ticker_meta.parquet"]
    manifest.output_paths = [
        "news_articles/",
        "signals/intelligence/news.parquet",
    ]

    if not settings.polygon_api_key:
        manifest.errors.append("TYCHE_POLYGON_API_KEY missing")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("ingest-news", rid, "failed", rel, {"error": "no_polygon_key"})

    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.news_pipeline import run_news_pipeline

    log_job_phase("ingest-news", "pipeline", status="start")
    pipeline = await run_news_pipeline(settings)
    log_job_phase(
        "ingest-news",
        "pipeline",
        status="complete",
        articles_classified=pipeline.articles_classified,
        total_persisted=pipeline.total_persisted,
    )

    manifest.extra = {
        "pipeline": {
            "polygon_fetched": pipeline.polygon_fetched,
            "finnhub_fetched": pipeline.finnhub_fetched,
            "total_persisted": pipeline.total_persisted,
            "articles_classified": pipeline.articles_classified,
            "signals_exported": pipeline.signals_rebuilt,
            "errors": pipeline.errors,
        },
    }
    if pipeline.errors:
        manifest.warnings.extend(pipeline.errors[:5])

    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("ingest-news", rid, "success", rel, manifest.extra)


async def run_ingest_edgar(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """EDGAR 8-K + Form 4 ingest, classification, intelligence signal export."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="ingest_edgar",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = ["ohlcv_daily/", "ticker_meta.parquet"]
    manifest.output_paths = [
        "filings_8k/",
        "insider_transactions/",
        "signals/intelligence/filings.parquet",
        "signals/intelligence/insider.parquet",
    ]

    if not settings.edgar_user_agent_email:
        manifest.errors.append("TYCHE_EDGAR_USER_AGENT_EMAIL missing")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("ingest-edgar", rid, "failed", rel, {"error": "no_edgar_email"})

    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.edgar_pipeline import run_edgar_pipeline

    log_job_phase("ingest-edgar", "pipeline", status="start")
    pipeline = await run_edgar_pipeline(settings)
    log_job_phase(
        "ingest-edgar",
        "pipeline",
        status="complete",
        eightk_persisted=pipeline.eightk_persisted,
        insider_tx_persisted=pipeline.insider_tx_persisted,
    )

    manifest.extra = {
        "pipeline": {
            "tickers_resolved": pipeline.tickers_resolved,
            "eightk_persisted": pipeline.eightk_persisted,
            "eightk_classified": pipeline.eightk_classified,
            "insider_tx_persisted": pipeline.insider_tx_persisted,
            "signals_exported": pipeline.signals_rebuilt,
            "errors": pipeline.errors,
        },
    }
    if pipeline.errors:
        manifest.warnings.extend(pipeline.errors[:5])

    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("ingest-edgar", rid, "success", rel, manifest.extra)


async def run_demand_gate_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Walk-forward demand ablation + conditional sustained model promotion."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="run_demand_gate",
        run_id=rid,
        data_backend=ctx.backend,
    )

    script = _backend_dir() / "scripts" / "run_demand_gate.py"
    cmd = [
        sys.executable,
        str(script),
        "--data-dir",
        settings.data_dir,
        "--output",
        "ml/alpha_dataset.parquet",
        "--results-dir",
        "ml/alpha_results",
    ]
    manifest.input_paths = manifest.input_paths + [
        "ohlcv_daily/",
        "fundamentals/",
        "estimates/",
        "estimate_snapshots/",
    ]
    manifest.output_paths = [
        "ml/alpha_results/demand_gate_verdict.json",
        "ml/models/",
    ]

    from tyche.ops.job_progress import log_job_phase

    log_job_phase("run-demand-gate", "subprocess", status="start")
    code, output = await asyncio.to_thread(_run_subprocess, cmd)
    manifest.extra["output_tail"] = output[-3000:] if len(output) > 3000 else output
    if code != 0:
        manifest.errors.append(f"exit_code={code}")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        raise RuntimeError(f"run_demand_gate failed (exit {code})")
    log_job_phase("run-demand-gate", "subprocess", status="complete")

    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("run-demand-gate", rid, "success", rel, manifest.extra)


def run_publish_signals_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Publish route-level JSON artifacts to ``published/routes/``."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()

    config = PublishConfig(
        data_dir=settings.data_dir,
        ctx=ctx,
        run_id=rid,
        alpha_row_limit=500,
        conviction_row_limit=5000,
        intelligence_row_limit=500,
        strict=True,
        max_stale_minutes=settings.published_max_age_minutes,
        settings=settings,
    )

    from tyche.ops.job_progress import log_job_phase

    log_job_phase("publish-signals", "execute", status="start")
    try:
        result = run_publish_signals(config)
    except PublishError as exc:
        manifest = RunManifest.start(
            job_name="publish_signals",
            run_id=rid,
            data_backend=ctx.backend,
        )
        manifest.errors.append(str(exc))
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        raise RuntimeError(str(exc)) from exc

    summary = {
        "routes": len(result.routes),
        "manifest": result.manifest_rel,
        "warnings": result.warnings,
    }
    log_job_phase(
        "publish-signals",
        "execute",
        status="complete",
        routes=len(result.routes),
    )
    return JobResult(
        "publish-signals",
        rid,
        "success",
        result.run_manifest_rel,
        summary,
    )


def run_audit_snapshots_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
    as_of: date | None = None,
) -> JobResult:
    """Audit estimate snapshot cadence after demand ingest."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    summary = run_audit_snapshots(
        settings=settings,
        ctx=ctx,
        run_id=run_id,
        as_of=as_of,
    )
    return JobResult(
        "audit-snapshots",
        summary["run_id"],
        summary["status"],
        summary["run_manifest"],
        summary,
    )


async def run_nightly_pipeline(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Run the full nightly chain in-process (fallback to Cloud Workflows)."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="nightly_pipeline",
        run_id=rid,
        data_backend=ctx.backend,
    )
    step_results: list[dict[str, Any]] = []

    for step in NIGHTLY_PIPELINE_STEPS:
        logger.info("nightly_pipeline_step_start", step=step)
        try:
            result = await execute_job(step, run_id=f"{rid}-{step}")
        except Exception as exc:
            entry = {"step": step, "status": "failed", "error": str(exc)}
            step_results.append(entry)
            if step in _OPTIONAL_PIPELINE_STEPS:
                manifest.warnings.append(f"{step}_failed:{exc}")
                logger.warning("nightly_pipeline_optional_step_failed", step=step)
                continue
            manifest.errors.append(f"{step}:{exc}")
            manifest.extra["steps"] = step_results
            manifest.finish(status="failed")
            rel = manifest.write(ctx=ctx)
            raise RuntimeError(f"nightly pipeline failed at {step}: {exc}") from exc

        entry = {
            "step": step,
            "status": result.status,
            "run_id": result.run_id,
            "manifest": result.manifest_rel,
        }
        step_results.append(entry)
        if result.status != "success" and step not in _OPTIONAL_PIPELINE_STEPS:
            manifest.errors.append(f"{step}_status={result.status}")
            manifest.extra["steps"] = step_results
            manifest.finish(status="failed")
            rel = manifest.write(ctx=ctx)
            raise RuntimeError(f"nightly pipeline step {step} returned {result.status}")

        if result.status != "success" and step in _OPTIONAL_PIPELINE_STEPS:
            manifest.warnings.append(f"{step}_status={result.status}")

    manifest.extra["steps"] = step_results
    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("nightly-pipeline", rid, "success", rel, manifest.extra)


_JOB_RUNNERS: dict[str, Callable[..., Any]] = {
    "ingest-data": run_ingest_data,
    "ingest-options-flatfiles": run_ingest_options_flatfiles,
    "ingest-demand-data": run_ingest_demand_data,
    "ingest-news": run_ingest_news,
    "ingest-edgar": run_ingest_edgar,
    "alpha-batch": run_alpha_batch_job,
    "run-demand-gate": run_demand_gate_job,
    "publish-signals": run_publish_signals_job,
    "audit-snapshots": run_audit_snapshots_job,
    "nightly-pipeline": run_nightly_pipeline,
}


async def execute_job(job_name: str, *, run_id: str | None = None) -> JobResult:
    """Dispatch a registered Cloud Run job by CLI name."""
    if job_name not in _JOB_RUNNERS:
        raise ValueError(f"Unknown job: {job_name}. Choose from: {', '.join(JOB_NAMES)}")

    from tyche.ops.job_progress import log_job_phase

    log_job_phase(job_name, "execute", status="start", run_id=run_id)
    runner = _JOB_RUNNERS[job_name]
    try:
        if asyncio.iscoroutinefunction(runner):
            result = await runner(run_id=run_id)
        else:
            result = runner(run_id=run_id)
    except Exception:
        log_job_phase(job_name, "execute", status="failed", run_id=run_id)
        raise
    log_job_phase(
        job_name,
        "execute",
        status=result.status,
        run_id=result.run_id,
        manifest=result.manifest_rel,
    )
    return result
