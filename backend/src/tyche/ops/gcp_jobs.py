"""Cloud Run Job runners with run manifests (GCP-F)."""

from __future__ import annotations

import asyncio
import os
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
    "stocks-conviction-batch",
    "stocks-derived-batch",
    "stocks-deep-dive-batch",
    "stocks-screener-index-batch",
    "candidate-universe-batch",
    "options-chain-prep-batch",
    "options-scanner-batch",
    "options-snapshot-batch",
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
    return proc.returncode, "".join(lines)


def _subprocess_exit_hint(code: int) -> str:
    """Human-readable hint for common Cloud Run subprocess failures."""
    if code == -9:
        return (
            "likely OOM (SIGKILL) — dataset build fits 16 GiB; walk-forward XGBoost "
            "needs 32 GiB on tyche-run-demand-gate (or set TYCHE_DEMAND_GATE_REUSE_DATASET=true)"
        )
    if code < 0:
        return f"process killed by signal {-code}"
    return f"exit {code}"



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

    from tyche.market_data.ingest_dates import resolve_ingest_end_date
    from tyche.ops.job_progress import log_job_phase

    ingest_end = resolve_ingest_end_date(settings.ingest_window, job_name="ingest-data")

    try:
        log_job_phase(
            "ingest-data",
            "bootstrap_ohlcv",
            tickers=store.get_ticker_count(),
            ingest_end_date=ingest_end.isoformat(),
        )
        result = await bootstrap_ohlcv(
            polygon,
            store,
            days=5,
            end_date=ingest_end,
            progress_job="ingest-data",
        )
        log_job_phase("ingest-data", "bootstrap_ohlcv", status="complete", **result)
        if result.get("dates_requested", 0) > 0 and result.get("dates_fetched", 0) == 0:
            manifest.warnings.append(
                f"ohlcv_fetch_miss:end_date={ingest_end.isoformat()}"
            )
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
        manifest.extra = {
            **result,
            "ingest_end_date": ingest_end.isoformat(),
            "market_caps_repriced": caps_updated,
        }
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

    from tyche.market_data.ingest_dates import resolve_ingest_end_date

    ingest_end = resolve_ingest_end_date(
        settings.ingest_window, job_name="ingest-options-flatfiles"
    )

    script = _backend_dir() / "scripts" / "ingest_options_flatfiles.py"
    cmd = [
        sys.executable,
        str(script),
        "--from-ohlcv",
        "--end-date",
        ingest_end.isoformat(),
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

    log_job_phase(
        "ingest-options-flatfiles",
        "subprocess",
        ingest_end_date=ingest_end.isoformat(),
        cmd=" ".join(cmd[-8:]),
    )
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

    from tyche.market_data.ingest_dates import resolve_ingest_end_date

    ingest_end = resolve_ingest_end_date(
        settings.ingest_window, job_name="ingest-demand-data"
    )
    counts = await ingest_demand_data(
        settings,
        do_fundamentals=settings.fundamentals_refresh_enabled,
        do_estimates=settings.estimates_refresh_enabled,
        do_short_interest=settings.short_interest_refresh_enabled,
        do_guidance=settings.guidance_refresh_enabled,
        as_of=ingest_end,
    )
    counts["ingest_end_date"] = ingest_end.isoformat()
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
    # Latest snapshots + dated history (written by AlphaSignalStore.write).
    as_of = summary.get("as_of_date")
    history_paths: list[str] = []
    for v in variants:
        history_paths.append(f"alpha_history/{v}/_current.json")
        if as_of:
            history_paths.append(f"alpha_history/{v}/{as_of}.parquet")
    manifest.output_paths = [
        "alpha_signals.parquet",
        "alpha_signals_sustained.parquet",
        *history_paths,
    ]
    manifest.extra = summary
    if summary.get("status") == "empty":
        manifest.warnings.append("alpha_batch_no_features")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("alpha-batch", rid, "failed", rel, summary)

    # Persistence read from the freshly-appended dated snapshots (best-effort).
    if getattr(settings, "alpha_persistence_enabled", True):
        try:
            from tyche.workflow.alpha_persistence import run_alpha_persistence

            log_job_phase("alpha-batch", "persistence", variants=variants)
            pers = run_alpha_persistence(
                settings,
                variants=variants,
                sessions=settings.alpha_persistence_sessions,
                top=settings.alpha_persistence_top,
            )
            summary["persistence"] = pers.get("variants", {})
            manifest.output_paths.extend(
                f"signals/alpha/persistence_{v}.json" for v in variants
            )
            log_job_phase("alpha-batch", "persistence", status="complete")
        except Exception as exc:  # noqa: BLE001 - persistence is best-effort
            manifest.warnings.append(f"alpha_persistence_failed: {exc}")
            log_job_phase("alpha-batch", "persistence", status="failed", error=str(exc))

    manifest.finish(status="success")
    rel = manifest.write(ctx=ctx)
    return JobResult("alpha-batch", rid, "success", rel, summary)


async def run_stocks_conviction_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Compute stocks conviction and export ``signals/stocks/conviction.parquet``."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="stocks_conviction_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = ["ohlcv_daily/", "ticker_meta.parquet", "derived/"]

    from tyche.conviction.engine import ConvictionEngine
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
    from tyche.market_data.derived_store import DerivedMetricsStore
    from tyche.market_data.stocks_conviction_store import STOCKS_CONVICTION_REL
    from tyche.ml.inference import CSPSafetyPredictor
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.conviction_batch import run_conviction_batch

    store = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)
    derived = DerivedMetricsStore(data_dir=settings.data_dir, ctx=ctx)
    predictor = CSPSafetyPredictor(data_dir=settings.data_dir)
    engine = ConvictionEngine(
        ema_fast=settings.ema_fast_period,
        ema_slow=settings.ema_slow_period,
        pullback_proximity_pct=settings.pullback_proximity_pct,
        max_extension_pct=settings.max_extension_pct,
        min_days_above_emas=settings.min_days_above_emas,
        max_days_above_emas=settings.max_days_above_emas,
        pullback_csp_enabled=settings.pullback_csp_enabled,
        min_prior_streak=settings.min_prior_streak,
        derived_store=derived,
        csp_predictor=predictor,
        oversold_dip_pct_21ema=settings.oversold_dip_pct_21ema,
        oversold_dip_pct_50ema=settings.oversold_dip_pct_50ema,
        oversold_min_prior_uptrend=settings.oversold_min_prior_uptrend,
    )

    log_job_phase("stocks-conviction-batch", "execute", status="start")
    result = await run_conviction_batch(
        data_store=store,
        conviction_engine=engine,
        ticker_meta_store=meta,
        min_market_cap=settings.conviction_batch_min_market_cap_millions * 1_000_000,
        min_price=settings.conviction_batch_min_price,
        min_avg_volume=settings.conviction_batch_min_avg_volume,
        retention_days=settings.conviction_snapshot_retention_days,
        persist_sqlite=False,
        export_parquet=True,
        ctx=ctx,
        run_id=rid,
    )
    summary = result.to_dict()
    log_job_phase(
        "stocks-conviction-batch",
        "execute",
        status="complete" if result.parquet_rows_written else "empty",
        signals=result.signals_computed,
        parquet_rows=result.parquet_rows_written,
    )

    manifest.extra = summary
    if result.parquet_rows_written:
        manifest.output_paths = [STOCKS_CONVICTION_REL]
        manifest.finish(status="success")
    elif result.signals_computed == 0:
        manifest.warnings.append("stocks_conviction_no_signals")
        manifest.finish(status="failed")
    else:
        manifest.errors.extend(result.errors or ["parquet_export_failed"])
        manifest.finish(status="failed")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("stocks-conviction-batch", rid, status, rel, summary)


async def run_stocks_derived_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Compute deep dips + history summaries and export signal Parquet."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="stocks_derived_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        "ohlcv_daily/",
        "ticker_meta.parquet",
        "signals/stocks/conviction.parquet",
    ]

    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
    from tyche.market_data.stocks_deep_dips_store import STOCKS_DEEP_DIPS_REL
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.history_summary import STOCKS_HISTORY_SUMMARY_REL
    from tyche.workflow.stocks_derived_batch import run_stocks_derived_batch

    store = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)

    log_job_phase("stocks-derived-batch", "execute", status="start")
    result = await run_stocks_derived_batch(
        settings=settings,
        data_store=store,
        ticker_meta_store=meta,
        conviction_engine=None,
        ctx=ctx,
        run_id=rid,
    )
    summary = result.to_dict()
    log_job_phase(
        "stocks-derived-batch",
        "execute",
        status="complete",
        deep_dips=result.deep_dip_alerts,
        history_rows=result.history_rows,
    )

    manifest.extra = summary
    manifest.output_paths = [
        STOCKS_DEEP_DIPS_REL,
        STOCKS_HISTORY_SUMMARY_REL,
    ]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.history_rows == 0 and result.deep_dip_alerts == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("stocks-derived-batch", rid, status, rel, summary)


async def run_deep_dive_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Precompute Stock Deep Dive payloads and write one Parquet per ticker."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="deep_dive_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        "ohlcv_daily/",
        "ticker_meta.parquet",
        "fundamentals/",
        "estimates/",
        "catalyst_signals/",
    ]

    from tyche.market_data.catalyst_store import CatalystSignalStore
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
    from tyche.market_data.deep_dive_store import DEEP_DIVE_REL
    from tyche.market_data.estimates_store import EstimatesStore
    from tyche.market_data.fundamentals_store import FundamentalsStore
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.deep_dive_batch import run_deep_dive_batch

    store = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)
    fundamentals = FundamentalsStore(data_dir=settings.data_dir, ctx=ctx)
    estimates = EstimatesStore(data_dir=settings.data_dir, ctx=ctx)
    catalysts = CatalystSignalStore(data_dir=settings.data_dir, ctx=ctx)

    log_job_phase("stocks-deep-dive-batch", "execute", status="start")
    result = await run_deep_dive_batch(
        ohlcv_store=store,
        meta_store=meta,
        fundamentals_store=fundamentals,
        estimates_store=estimates,
        catalyst_store=catalysts,
        min_market_cap_millions=settings.deep_dive_batch_min_market_cap_millions,
        ctx=ctx,
    )
    summary = result.to_dict()
    log_job_phase(
        "stocks-deep-dive-batch",
        "execute",
        status="complete",
        computed=result.tickers_computed,
        written=result.tickers_written,
    )

    manifest.extra = summary
    manifest.output_paths = [f"{DEEP_DIVE_REL}/"]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.tickers_written == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("stocks-deep-dive-batch", rid, status, rel, summary)


async def run_screener_index_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Precompute the v3 Stock Screener index (single compact universe file).

    Reads the v2 ``DeepDiveStore`` where present (chained after
    ``stocks-deep-dive-batch``), falls back to the inline
    ``TickerDeepDiveEngine`` otherwise. Fully standalone from conviction
    SQLite / its 5-layer cache.
    """
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="screener_index_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        "ohlcv_daily/",
        "ticker_meta.parquet",
        "signals/stocks/deep_dive/",
        "fundamentals/",
        "estimates/",
        "catalyst_signals/",
    ]

    from tyche.market_data.catalyst_store import CatalystSignalStore
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
    from tyche.market_data.deep_dive_store import DeepDiveStore
    from tyche.market_data.estimates_store import EstimatesStore
    from tyche.market_data.fundamentals_store import FundamentalsStore
    from tyche.market_data.screener_index_store import SCREENER_INDEX_REL
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.screener_index_batch import run_screener_index_batch

    store = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)
    deep_dive_store = DeepDiveStore(data_dir=settings.data_dir, ctx=ctx)
    fundamentals = FundamentalsStore(data_dir=settings.data_dir, ctx=ctx)
    estimates = EstimatesStore(data_dir=settings.data_dir, ctx=ctx)
    catalysts = CatalystSignalStore(data_dir=settings.data_dir, ctx=ctx)

    log_job_phase("stocks-screener-index-batch", "execute", status="start")
    result = await run_screener_index_batch(
        deep_dive_store=deep_dive_store,
        ohlcv_store=store,
        meta_store=meta,
        fundamentals_store=fundamentals,
        estimates_store=estimates,
        catalyst_store=catalysts,
        min_market_cap_millions=settings.screener_index_min_market_cap_millions,
        ctx=ctx,
    )
    summary = result.to_dict()
    log_job_phase(
        "stocks-screener-index-batch",
        "execute",
        status="complete",
        indexed=result.tickers_indexed,
        written=result.tickers_written,
    )

    manifest.extra = summary
    manifest.output_paths = [SCREENER_INDEX_REL]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.tickers_written == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("stocks-screener-index-batch", rid, status, rel, summary)


async def run_candidate_universe_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Build metadata-first options/stocks candidate universes."""
    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="candidate_universe_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        "ticker_meta.parquet",
        "alpha_signals_sustained.parquet",
        "signals/stocks/conviction.parquet",
        "ohlcv_daily/",
    ]

    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
    from tyche.market_data.universe_candidates_store import (
        CSP_SCAN_TICKERS_REL,
        OPTIONS_CANDIDATES_REL,
        STOCKS_CANDIDATES_REL,
    )
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.candidate_universe import run_candidate_universe_batch

    store = OHLCVStore(data_dir=settings.data_dir, ctx=ctx)
    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)

    log_job_phase("candidate-universe-batch", "execute", status="start")
    result = run_candidate_universe_batch(
        settings=settings,
        data_store=store,
        meta_store=meta,
        ctx=ctx,
        run_id=rid,
    )
    summary = result.to_dict()
    log_job_phase(
        "candidate-universe-batch",
        "execute",
        status="complete",
        options=result.options_candidates,
        csp_scan=result.csp_scan_tickers,
        stocks=result.stocks_candidates,
    )

    manifest.extra = summary
    manifest.output_paths = [
        OPTIONS_CANDIDATES_REL,
        CSP_SCAN_TICKERS_REL,
        STOCKS_CANDIDATES_REL,
    ]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.options_candidates == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("candidate-universe-batch", rid, status, rel, summary)


def run_options_chain_prep_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Build scanner chains from prior-day Massive flatfiles for candidates."""
    from tyche.market_data.options_chain_snapshot_store import (
        OPTIONS_CHAIN_CONTRACTS_REL,
        OPTIONS_CHAIN_PREP_REPORT_REL,
        OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
    )
    from tyche.market_data.universe_candidates_store import OPTIONS_CANDIDATES_REL
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.options_chain_prep import run_options_chain_prep_batch

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="options_chain_prep_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        OPTIONS_CANDIDATES_REL,
        "options_history/",
    ]

    log_job_phase("options-chain-prep-batch", "execute", status="start")
    result = run_options_chain_prep_batch(
        settings=settings,
        ctx=ctx,
        run_id=rid,
    )
    summary = result.to_dict()
    log_job_phase(
        "options-chain-prep-batch",
        "execute",
        status="complete",
        with_contracts=result.tickers_with_contracts,
        requested=result.tickers_requested,
    )

    manifest.extra = summary
    manifest.output_paths = [
        OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
        OPTIONS_CHAIN_CONTRACTS_REL,
        OPTIONS_CHAIN_PREP_REPORT_REL,
    ]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.tickers_with_contracts == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("options-chain-prep-batch", rid, status, rel, summary)


async def run_options_scanner_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Run CSP scanner over csp_scan_tickers using flatfile chain artifacts."""
    from tyche.market_data.data_store import TickerMetaStore
    from tyche.market_data.options_scanner_store import (
        OPTIONS_SCANNER_REL,
        OPTIONS_SCANNER_REPORT_REL,
    )
    from tyche.market_data.universe_candidates_store import CSP_SCAN_TICKERS_REL
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.options_scanner_batch import run_options_scanner_batch

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="options_scanner_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        CSP_SCAN_TICKERS_REL,
        "signals/stocks/conviction.parquet",
        "signals/options/options_chain_contracts.parquet",
        "ticker_meta.parquet",
    ]

    meta = TickerMetaStore(data_dir=settings.data_dir, ctx=ctx)
    log_job_phase("options-scanner-batch", "execute", status="start")
    result = await run_options_scanner_batch(
        settings=settings,
        ctx=ctx,
        meta_store=meta,
        run_id=rid,
    )
    summary = result.to_dict()
    log_job_phase(
        "options-scanner-batch",
        "execute",
        status="complete",
        scanned=result.symbols_scanned,
        candidates=result.csp_candidates,
    )

    manifest.extra = summary
    manifest.output_paths = [OPTIONS_SCANNER_REL, OPTIONS_SCANNER_REPORT_REL]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.csp_candidates == 0 and result.symbols_scanned == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("options-scanner-batch", rid, status, rel, summary)


async def run_options_snapshot_batch_job(
    *,
    settings: TycheSettings | None = None,
    ctx: StorageContext | None = None,
    run_id: str | None = None,
) -> JobResult:
    """Optional live Tradier refresh — run post-open, not in morning pipeline."""
    from tyche.market_data.options_chain_snapshot_store import (
        OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
        OPTIONS_TRADIER_SNAPSHOT_REPORT_REL,
    )
    from tyche.market_data.universe_candidates_store import CSP_SCAN_TICKERS_REL
    from tyche.ops.job_progress import log_job_phase
    from tyche.workflow.options_snapshot_batch import run_options_snapshot_batch

    settings = settings or get_settings()
    ctx = ctx or storage_context_from_settings(settings)
    rid = run_id or new_run_id()
    manifest = RunManifest.start(
        job_name="options_snapshot_batch",
        run_id=rid,
        data_backend=ctx.backend,
    )
    manifest.input_paths = [
        CSP_SCAN_TICKERS_REL,
    ]

    if not settings.tradier_api_token:
        manifest.errors.append("missing_tradier_api_token")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        return JobResult("options-snapshot-batch", rid, "failed", rel, {})

    log_job_phase("options-snapshot-batch", "execute", status="start")
    result = await run_options_snapshot_batch(
        settings=settings,
        ctx=ctx,
        run_id=rid,
    )
    summary = result.to_dict()
    log_job_phase(
        "options-snapshot-batch",
        "execute",
        status="complete",
        succeeded=result.tickers_succeeded,
        requested=result.tickers_requested,
    )

    manifest.extra = summary
    manifest.output_paths = [
        "options_chains/",
        OPTIONS_CHAIN_SNAPSHOT_SUMMARY_REL,
        OPTIONS_TRADIER_SNAPSHOT_REPORT_REL,
    ]
    if result.errors:
        manifest.warnings.extend(result.errors)
    if result.tickers_succeeded == 0:
        manifest.finish(status="failed")
    else:
        manifest.finish(status="success")

    rel = manifest.write(ctx=ctx)
    status = "success" if manifest.status == "success" else "failed"
    return JobResult("options-snapshot-batch", rid, status, rel, summary)


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
    dataset_rel = "ml/alpha_dataset.parquet"
    cmd = [
        sys.executable,
        str(script),
        "--data-dir",
        settings.data_dir,
        "--output",
        dataset_rel,
        "--results-dir",
        "ml/alpha_results",
    ]
    reuse_dataset = os.environ.get("TYCHE_DEMAND_GATE_REUSE_DATASET", "").lower() in (
        "1",
        "true",
        "yes",
    )
    from tyche.storage import exists as storage_exists

    if reuse_dataset and storage_exists(dataset_rel, ctx=ctx):
        cmd.extend(["--dataset", dataset_rel])
        manifest.extra["reuse_dataset"] = True
        logger.info("demand_gate_reuse_dataset", path=dataset_rel)
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
        hint = _subprocess_exit_hint(code)
        manifest.errors.append(f"exit_code={code}; {hint}")
        manifest.finish(status="failed")
        rel = manifest.write(ctx=ctx)
        raise RuntimeError(f"run_demand_gate failed ({hint})")
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
    if as_of is None:
        from tyche.market_data.ingest_dates import resolve_ingest_end_date

        as_of = resolve_ingest_end_date(
            settings.ingest_window, job_name="audit-snapshots"
        )
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
    "stocks-conviction-batch": run_stocks_conviction_batch_job,
    "stocks-derived-batch": run_stocks_derived_batch_job,
    "stocks-deep-dive-batch": run_deep_dive_batch_job,
    "stocks-screener-index-batch": run_screener_index_batch_job,
    "candidate-universe-batch": run_candidate_universe_batch_job,
    "options-chain-prep-batch": run_options_chain_prep_batch_job,
    "options-scanner-batch": run_options_scanner_batch_job,
    "options-snapshot-batch": run_options_snapshot_batch_job,
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
