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

import structlog

from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.ml.dataset import build_latest_features
from tyche.strategy.alpha_engine import AlphaScoreEngine

logger = structlog.get_logger()


def run_alpha_batch(
    data_dir: str = "data",
    min_market_cap: float = 1e9,
    tickers: list[str] | None = None,
    max_tickers: int | None = None,
    engine: AlphaScoreEngine | None = None,
    predictor: Any | None = None,
    persist: bool = True,
) -> dict[str, Any]:
    """Compute and (optionally) persist directional alpha signals.

    Returns a summary dict with counts and timing.
    """
    t0 = time.time()
    engine = engine or AlphaScoreEngine()

    if predictor is None:
        try:
            from tyche.ml.breakout import BreakoutPredictor

            bp = BreakoutPredictor(data_dir=data_dir)
            predictor = bp if bp.is_available else None
        except ImportError:
            predictor = None

    features = build_latest_features(
        data_dir=data_dir,
        min_market_cap=min_market_cap,
        tickers=tickers,
        max_tickers=max_tickers,
    )
    if features.empty:
        logger.warning("alpha_batch_no_features")
        return {"status": "empty", "signals": 0}

    probs = predictor.predict_proba_batch(features) if predictor is not None else {}
    signals = engine.score_from_features(features, breakout_probs=probs)
    signals.sort(key=lambda s: s.alpha_score, reverse=True)

    as_of = _resolve_as_of(features)
    signal_dicts = [s.to_dict() for s in signals]

    if persist:
        store = AlphaSignalStore(data_dir=data_dir)
        store.write(signal_dicts, as_of=as_of)

    elapsed = time.time() - t0
    buys = sum(1 for s in signals if s.signal in ("strong_buy", "buy"))
    logger.info(
        "alpha_batch_complete",
        signals=len(signals),
        buys=buys,
        ml_available=predictor is not None,
        elapsed_s=round(elapsed, 1),
    )

    return {
        "status": "ok",
        "signals": len(signals),
        "buy_signals": buys,
        "ml_available": predictor is not None,
        "as_of_date": as_of.isoformat(),
        "elapsed_s": round(elapsed, 1),
    }


def _resolve_as_of(features) -> date:
    if "date" in features.columns and not features["date"].empty:
        import pandas as pd

        try:
            return pd.to_datetime(features["date"]).max().date()
        except Exception:
            pass
    return date.today()
