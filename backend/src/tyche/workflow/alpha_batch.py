"""Nightly directional Alpha batch.

Builds the latest-date feature row per ticker, runs the ML big-move predictor,
scores every name through the deterministic AlphaScoreEngine, and persists the
ranked snapshot to ``data/alpha_signals.parquet`` for instant page loads.

Mirrors the conviction batch: heavy computation runs once after market close;
the API just reads the snapshot.
"""

from __future__ import annotations

import time
from datetime import date
from typing import Any

import pandas as pd
import structlog

from tyche.config import TycheSettings
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.ml.dataset import build_latest_features
from tyche.ml.xgb_baseline import ALPHA_SUSTAINED_TARGETS, ALPHA_TARGETS
from tyche.ops.job_progress import log_job_phase
from tyche.storage.paths import StorageContext, storage_context_from_settings
from tyche.strategy.alpha_engine import AlphaScoreEngine, build_alpha_score_engine

logger = structlog.get_logger()

JOB_NAME = "alpha-batch"

# Model targets backing each page variant. "peak" = the legacy intra-window
# big-move models; "sustained" = the de-biased models that require the move to
# still hold at the end of the horizon. Both share the same horizon ordering
# (swing / trend / thematic), so sustained probabilities remap onto the engine's
# canonical (peak) target keys for transparent scoring.
_VARIANT_TARGETS: dict[str, list[str]] = {
    "peak": ALPHA_TARGETS,
    "sustained": ALPHA_SUSTAINED_TARGETS,
}
_SUSTAINED_TO_CANONICAL = dict(zip(ALPHA_SUSTAINED_TARGETS, ALPHA_TARGETS))


def _build_predictor(
    data_dir: str,
    variant: str,
    *,
    ctx: StorageContext | None = None,
) -> Any | None:
    """Load the BreakoutPredictor for *variant* (None if no artifacts / no ml)."""
    try:
        from tyche.ml.breakout import BreakoutPredictor
    except ImportError:
        return None
    targets = _VARIANT_TARGETS.get(variant, ALPHA_TARGETS)
    bp = BreakoutPredictor(data_dir=data_dir, targets=targets, ctx=ctx)
    return bp if bp.is_available else None


def _score_variant(
    features: pd.DataFrame,
    *,
    variant: str,
    engine: AlphaScoreEngine,
    data_dir: str,
    predictor: Any | None,
    persist: bool,
    settings: TycheSettings | None = None,
) -> dict[str, Any]:
    """Score the pre-built feature frame for one model variant and persist it."""
    probs = predictor.predict_proba_batch(features) if predictor is not None else {}
    if variant == "sustained" and probs:
        probs = {_SUSTAINED_TO_CANONICAL.get(k, k): v for k, v in probs.items()}

    signals = engine.score_from_features(features, breakout_probs=probs)
    signals.sort(key=lambda s: s.alpha_score, reverse=True)

    as_of = _resolve_as_of(features, settings=settings)
    if persist:
        AlphaSignalStore(data_dir=data_dir, variant=variant).write(
            [s.to_dict() for s in signals], as_of=as_of
        )

    buys = sum(1 for s in signals if s.signal in ("strong_buy", "buy"))
    logger.info(
        "alpha_variant_scored",
        variant=variant,
        signals=len(signals),
        buys=buys,
        ml_available=predictor is not None,
    )
    return {
        "status": "ok",
        "variant": variant,
        "signals": len(signals),
        "buy_signals": buys,
        "ml_available": predictor is not None,
        "as_of_date": as_of.isoformat(),
    }


def run_alpha_batch(
    data_dir: str = "data",
    min_market_cap: float = 1e9,
    tickers: list[str] | None = None,
    max_tickers: int | None = None,
    engine: AlphaScoreEngine | None = None,
    predictor: Any | None = None,
    persist: bool = True,
    variants: list[str] | None = None,
    settings: TycheSettings | None = None,
) -> dict[str, Any]:
    """Compute and (optionally) persist directional alpha signals.

    Builds the latest-date feature frame ONCE and scores it through each
    requested model variant ("peak" and/or "sustained"). Scoring is cheap
    relative to the feature build, so producing both snapshots is nearly free.

    Returns the summary dict for the primary (first) variant, with a ``variants``
    key listing everything produced. ``predictor`` (if supplied) is only used for
    the "peak" variant; other variants load their own models.
    """
    t0 = time.time()
    if engine is None and settings is not None:
        engine = build_alpha_score_engine(
            discovery_enabled=settings.alpha_discovery_enabled,
            percentile_signals=settings.alpha_percentile_signals_enabled,
            demand_adjusted_extension=settings.alpha_demand_adjusted_extension_enabled,
            demand_mult_ceil_discovery=settings.alpha_demand_mult_ceil_discovery,
        )
    engine = engine or AlphaScoreEngine()
    variants = variants or ["peak"]
    ctx = storage_context_from_settings(settings) if settings is not None else None

    log_job_phase(
        JOB_NAME,
        "build_features",
        min_market_cap=min_market_cap,
        variants=variants,
    )
    features = build_latest_features(
        data_dir=data_dir,
        min_market_cap=min_market_cap,
        tickers=tickers,
        max_tickers=max_tickers,
        job_name=JOB_NAME,
    )
    if features.empty:
        logger.warning("alpha_batch_no_features")
        log_job_phase(JOB_NAME, "build_features", status="empty")
        return {"status": "empty", "signals": 0}
    log_job_phase(
        JOB_NAME,
        "build_features",
        status="complete",
        feature_rows=len(features),
    )

    results: dict[str, dict[str, Any]] = {}
    for variant in variants:
        log_job_phase(JOB_NAME, "score_variant", variant=variant)
        var_pred = (
            predictor
            if (variant == "peak" and predictor is not None)
            else _build_predictor(data_dir, variant, ctx=ctx)
        )
        results[variant] = _score_variant(
            features,
            variant=variant,
            engine=engine,
            data_dir=data_dir,
            predictor=var_pred,
            persist=persist,
            settings=settings,
        )
        log_job_phase(
            JOB_NAME,
            "score_variant",
            status="complete",
            variant=variant,
            signals=results[variant].get("signals", 0),
            buys=results[variant].get("buy_signals", 0),
        )

    elapsed = time.time() - t0
    primary = dict(results[variants[0]])
    primary["elapsed_s"] = round(elapsed, 1)
    primary["variants"] = list(results.keys())
    logger.info(
        "alpha_batch_complete",
        variants=primary["variants"],
        signals=primary.get("signals", 0),
        buys=primary.get("buy_signals", 0),
        ml_available=primary.get("ml_available", False),
        elapsed_s=primary["elapsed_s"],
    )
    return primary


def _resolve_as_of(
    features: pd.DataFrame,
    settings: TycheSettings | None = None,
) -> date:
    if "date" in features.columns and not features["date"].empty:
        try:
            return pd.to_datetime(features["date"]).max().date()
        except Exception:
            pass
    if settings is not None:
        from tyche.market_data.ingest_dates import resolve_ingest_end_date

        return resolve_ingest_end_date(
            settings.ingest_window, job_name="alpha-batch"
        )
    from tyche.market_data.ingest_dates import ingest_end_date

    return ingest_end_date(None, job_name="alpha-batch")
