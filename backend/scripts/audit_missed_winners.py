"""Missed-winners probe — why known winners did or did not surface in alpha.

Read-only diagnostic. Does not change scoring or models.

Run from ``backend/``:
    .venv/bin/python scripts/audit_missed_winners.py --source snapshot
    .venv/bin/python scripts/audit_missed_winners.py --source engine --tickers MU AVGO SNDK STX ARM
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.market_data.alpha_store import AlphaSignalStore  # noqa: E402
from tyche.ml.dataset import build_latest_features  # noqa: E402
from tyche.ml.xgb_baseline import ALPHA_SUSTAINED_TARGETS, ALPHA_TARGETS  # noqa: E402
from tyche.strategy.alpha_engine import (  # noqa: E402
    AlphaScoreEngine,
    HORIZON_TARGETS,
    _DEMAND_MULT_CEIL,
    _DEMAND_MULT_FLOOR,
    _DEMAND_SENSITIVITY,
    _FACTOR_WEIGHT,
    _ML_WEIGHT,
    _OVEREXTENSION_FLOOR,
)

logger = structlog.get_logger()

DEFAULT_TICKERS = ["MU", "AVGO", "SNDK", "STX", "ARM"]
_SUSTAINED_TO_CANONICAL = dict(zip(ALPHA_SUSTAINED_TARGETS, ALPHA_TARGETS, strict=True))
_WATCH_THRESHOLD = 44.0


def _opt(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return float(val)


def _apply_killed_by(
    *,
    killed_by: list[str],
    ml_blend: float | None,
    overextension_penalty: float | None,
    missing: list[str],
    alpha_score: float | None,
    low_ml_threshold: float,
    anti_chase_threshold: float,
    missing_demand_threshold: int,
    watch_threshold: float,
) -> None:
    # killed_by is a diagnostic label only. Changing these thresholds changes labels,
    # not the underlying measured score, ML blend, penalty, or demand multiplier.
    if ml_blend is not None and ml_blend < low_ml_threshold:
        killed_by.append("low_ml_prob")
    if overextension_penalty is not None and overextension_penalty < anti_chase_threshold:
        killed_by.append("anti_chase")
    if len(missing) >= missing_demand_threshold:
        killed_by.append("missing_demand")
    if alpha_score is not None and alpha_score < watch_threshold:
        killed_by.append("below_threshold")


def _score_breakdown(
    engine: AlphaScoreEngine,
    row: pd.Series,
    breakout_probs: dict[str, np.ndarray] | None,
    index: int,
    *,
    low_ml_threshold: float,
    anti_chase_threshold: float,
    missing_demand_threshold: int,
    watch_threshold: float,
) -> dict[str, Any]:
    """Mirror AlphaScoreEngine.score_from_features intermediate values."""
    factors = engine._compute_factors(row)
    factor_blend = factors.blended()

    p_swing = engine._prob_at(breakout_probs or {}, HORIZON_TARGETS["swing"], index)
    p_trend = engine._prob_at(breakout_probs or {}, HORIZON_TARGETS["trend"], index)
    p_thematic = engine._prob_at(breakout_probs or {}, HORIZON_TARGETS["thematic"], index)
    ml_blend = engine._ml_blend(p_swing, p_trend, p_thematic)

    if ml_blend is not None:
        composite_before_penalty = _ML_WEIGHT * ml_blend + _FACTOR_WEIGHT * factor_blend
    else:
        composite_before_penalty = factor_blend

    overext = engine._overextension(row)
    overextension_penalty = 1.0 - (1.0 - _OVEREXTENSION_FLOOR) * overext
    composite_after_penalty = composite_before_penalty * overextension_penalty

    regime = engine._classify_regime(row)
    dims = engine._demand_dimensions(row, regime)
    demand_multiplier = max(
        _DEMAND_MULT_FLOOR,
        min(_DEMAND_MULT_CEIL, 1.0 + _DEMAND_SENSITIVITY * dims.net),
    )
    composite_final = min(1.0, composite_after_penalty * demand_multiplier)
    alpha_score = round(100.0 * composite_final, 1)
    signal = engine._classify_signal(alpha_score)

    missing: list[str] = []
    if dims.fund is None:
        missing.append("D-FUND")
    if dims.est is None:
        missing.append("D-EST")
    if dims.catalyst is None:
        missing.append("D-CAT")
    if dims.policy is None:
        missing.append("D-POL")
    if dims.squeeze is None:
        missing.append("D-TECH")

    killed_by: list[str] = []
    _apply_killed_by(
        killed_by=killed_by,
        ml_blend=ml_blend,
        overextension_penalty=overextension_penalty,
        missing=missing,
        alpha_score=alpha_score,
        low_ml_threshold=low_ml_threshold,
        anti_chase_threshold=anti_chase_threshold,
        missing_demand_threshold=missing_demand_threshold,
        watch_threshold=watch_threshold,
    )

    return {
        "ml_blend": ml_blend,
        "factor_blend": round(factor_blend, 4),
        "composite_before_penalty": round(composite_before_penalty, 4),
        "composite_after_penalty": round(composite_after_penalty, 4),
        "composite_final": round(composite_final, 4),
        "overextension_penalty": round(overextension_penalty, 4),
        "demand_multiplier": round(demand_multiplier, 4),
        "alpha_score": alpha_score,
        "signal": signal,
        "regime": regime,
        "missing_demand_dimensions": ",".join(missing) if missing else "",
        "killed_by": ",".join(killed_by) if killed_by else "",
        "breakout_prob_swing": p_swing,
        "breakout_prob_trend": p_trend,
        "breakout_prob_thematic": p_thematic,
        "demand_net": dims.net,
        "score_without_overextension_penalty": round(
            100.0 * min(1.0, composite_before_penalty * demand_multiplier), 1
        ),
        "score_with_neutral_demand_multiplier": round(
            100.0 * min(1.0, composite_after_penalty), 1
        ),
        "score_before_penalty_and_demand": round(100.0 * composite_before_penalty, 1),
        "counterfactual_mode": "exact",
    }


def _load_snapshot_map(data_dir: str) -> tuple[dict[str, dict], str]:
    for variant in ("sustained", "peak"):
        store = AlphaSignalStore(data_dir=data_dir, variant=variant)
        if store.exists:
            signals, _, _ = store.read_latest()
            if signals:
                return {str(s["ticker"]).upper(): s for s in signals}, variant
    return {}, "none"


def _probe_snapshot(
    tickers: list[str],
    data_dir: str,
    *,
    low_ml_threshold: float,
    anti_chase_threshold: float,
    missing_demand_threshold: int,
    watch_threshold: float,
) -> list[dict]:
    snap_map, variant = _load_snapshot_map(data_dir)
    rows: list[dict] = []
    for ticker in tickers:
        rec = snap_map.get(ticker.upper())
        if rec is None:
            rows.append({
                "ticker": ticker,
                "source": "snapshot",
                "snapshot_variant": variant,
                "killed_by": "not_in_snapshot",
                "counterfactual_mode": "approx_from_snapshot",
            })
            continue
        demand = rec.get("demand") or {}
        missing: list[str] = []
        for key, label in (
            ("fund", "D-FUND"),
            ("est", "D-EST"),
            ("catalyst", "D-CAT"),
            ("policy", "D-POL"),
            ("squeeze", "D-TECH"),
        ):
            if demand.get(key) is None:
                missing.append(label)

        ml_vals = [
            rec.get("breakout_prob_swing"),
            rec.get("breakout_prob_trend"),
            rec.get("breakout_prob_thematic"),
        ]
        ml_present = any(v is not None for v in ml_vals)
        ml_blend = None
        if ml_present:
            vals = [float(v) for v in ml_vals if v is not None]
            ml_blend = 0.6 * max(vals) + 0.4 * (sum(vals) / len(vals))

        score = rec.get("alpha_score")
        penalty = rec.get("overextension_penalty")
        demand_multiplier = rec.get("demand_multiplier")

        score_without_overextension_penalty_approx = None
        score_with_neutral_demand_multiplier_approx = None
        if score is not None and penalty not in (None, 0):
            try:
                score_without_overextension_penalty_approx = round(
                    min(100.0, float(score) / float(penalty)), 1
                )
            except (TypeError, ValueError, ZeroDivisionError):
                pass
        if score is not None and demand_multiplier not in (None, 0):
            try:
                score_with_neutral_demand_multiplier_approx = round(
                    min(100.0, float(score) / float(demand_multiplier)), 1
                )
            except (TypeError, ValueError, ZeroDivisionError):
                pass

        killed_by: list[str] = []
        _apply_killed_by(
            killed_by=killed_by,
            ml_blend=ml_blend,
            overextension_penalty=float(penalty) if penalty is not None else None,
            missing=missing,
            alpha_score=float(score) if score is not None else None,
            low_ml_threshold=low_ml_threshold,
            anti_chase_threshold=anti_chase_threshold,
            missing_demand_threshold=missing_demand_threshold,
            watch_threshold=watch_threshold,
        )

        rows.append({
            "ticker": ticker,
            "source": "snapshot",
            "snapshot_variant": variant,
            "ml_blend": ml_blend,
            "factor_blend": None,
            "composite_before_penalty": None,
            "composite_after_penalty": None,
            "composite_final": None,
            "overextension_penalty": penalty,
            "demand_multiplier": demand_multiplier,
            "alpha_score": score,
            "signal": rec.get("signal"),
            "regime": rec.get("regime"),
            "missing_demand_dimensions": ",".join(missing),
            "killed_by": ",".join(killed_by),
            "demand_net": demand.get("net"),
            "score_without_overextension_penalty": score_without_overextension_penalty_approx,
            "score_with_neutral_demand_multiplier": score_with_neutral_demand_multiplier_approx,
            "score_before_penalty_and_demand": None,
            "counterfactual_mode": "approx_from_snapshot",
        })
    return rows


def _probe_engine(
    tickers: list[str],
    data_dir: str,
    min_market_cap: float,
    *,
    low_ml_threshold: float,
    anti_chase_threshold: float,
    missing_demand_threshold: int,
    watch_threshold: float,
) -> list[dict]:
    features = build_latest_features(
        data_dir=data_dir,
        min_market_cap=min_market_cap,
        tickers=[t.upper() for t in tickers],
    )
    if features.empty:
        return [{"ticker": t, "source": "engine", "killed_by": "no_features"} for t in tickers]

    try:
        from tyche.ml.breakout import BreakoutPredictor
    except ImportError:
        BreakoutPredictor = None  # type: ignore[misc, assignment]

    probs: dict[str, np.ndarray] = {}
    if BreakoutPredictor is not None:
        bp = BreakoutPredictor(data_dir=data_dir, targets=ALPHA_SUSTAINED_TARGETS)
        if not bp.is_available:
            bp = BreakoutPredictor(data_dir=data_dir, targets=ALPHA_TARGETS)
        if bp.is_available:
            raw = bp.predict_proba_batch(features)
            for k, v in raw.items():
                probs[_SUSTAINED_TO_CANONICAL.get(k, k)] = v

    features = features.reset_index(drop=True)
    feat_index = {str(r["ticker"]).upper(): i for i, r in features.iterrows()}

    engine = AlphaScoreEngine()
    rows: list[dict] = []
    for ticker in tickers:
        t = ticker.upper()
        if t not in feat_index:
            rows.append({"ticker": ticker, "source": "engine", "killed_by": "no_features"})
            continue
        idx = feat_index[t]
        row = features.iloc[idx]
        breakdown = _score_breakdown(
            engine,
            row,
            probs or None,
            idx,
            low_ml_threshold=low_ml_threshold,
            anti_chase_threshold=anti_chase_threshold,
            missing_demand_threshold=missing_demand_threshold,
            watch_threshold=watch_threshold,
        )
        breakdown["ticker"] = ticker
        breakdown["source"] = "engine"
        rows.append(breakdown)
    return rows


def main() -> None:
    parser = argparse.ArgumentParser(description="Missed-winners alpha probe (read-only)")
    parser.add_argument(
        "--source",
        choices=("snapshot", "engine"),
        default="snapshot",
        help="Read stored snapshot or rescore via engine",
    )
    parser.add_argument(
        "--tickers",
        nargs="+",
        default=DEFAULT_TICKERS,
        help="Tickers to probe",
    )
    parser.add_argument("--data-dir", default="data")
    parser.add_argument(
        "--output",
        default="data/ml/alpha_results/missed_winners.csv",
    )
    parser.add_argument("--low-ml-threshold", type=float, default=0.30)
    parser.add_argument("--anti-chase-threshold", type=float, default=0.80)
    parser.add_argument("--missing-demand-threshold", type=int, default=3)
    parser.add_argument("--watch-threshold", type=float, default=_WATCH_THRESHOLD)
    args = parser.parse_args()

    settings = get_settings()
    min_cap = settings.alpha_min_market_cap_millions * 1e6
    tickers = [t.upper() for t in args.tickers]

    kw = {
        "low_ml_threshold": args.low_ml_threshold,
        "anti_chase_threshold": args.anti_chase_threshold,
        "missing_demand_threshold": args.missing_demand_threshold,
        "watch_threshold": args.watch_threshold,
    }

    if args.source == "snapshot":
        rows = _probe_snapshot(tickers, args.data_dir, **kw)
    else:
        rows = _probe_engine(tickers, args.data_dir, min_cap, **kw)

    out_df = pd.DataFrame(rows)
    if "alpha_score" in out_df.columns and out_df["alpha_score"].notna().any():
        out_df["score_percentile_within_probe"] = (
            out_df["alpha_score"].rank(pct=True, method="average")
        )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(out_path, index=False)

    print(f"\n{'=' * 60}")
    print(f"MISSED WINNERS ({args.source})")
    print(f"{'=' * 60}")
    for row in rows:
        print(
            f"  {row.get('ticker')}: score={row.get('alpha_score')} "
            f"signal={row.get('signal')} killed_by={row.get('killed_by')}"
        )
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
