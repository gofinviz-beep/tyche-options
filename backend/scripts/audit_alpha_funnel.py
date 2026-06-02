"""Alpha funnel diagnostic — where the scored universe collapses.

Read-only audit: no scoring, model, or UI changes.

Run from ``backend/``:
    .venv/bin/python scripts/audit_alpha_funnel.py
    .venv/bin/python scripts/audit_alpha_funnel.py --data-dir data

Writes ``data/ml/alpha_results/funnel_audit.json`` and prints a summary.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from tyche.config import get_settings  # noqa: E402
from tyche.market_data.alpha_store import AlphaSignalStore  # noqa: E402
from tyche.ml.dataset import build_latest_features  # noqa: E402

logger = structlog.get_logger()

_FEATURE_COLS = (
    "f_rev_growth_yoy",
    "e_eps_revision_90d",
    "cat_demand_score",
    "si_days_to_cover",
)
_ML_PROB_COLS = (
    "breakout_prob_swing",
    "breakout_prob_trend",
    "breakout_prob_thematic",
)
_SCORE_THRESHOLDS = (44, 58, 72)


def _has_value(series: pd.Series) -> int:
    if series.empty:
        return 0
    return int(series.notna().sum())


def _count_scores(scores: pd.Series, threshold: float) -> int:
    if scores.empty:
        return 0
    return int((scores >= threshold).sum())


def _load_snapshot(data_dir: str) -> tuple[list[dict], str, str | None]:
    for variant in ("sustained", "peak"):
        store = AlphaSignalStore(data_dir=data_dir, variant=variant)
        if store.exists:
            signals, as_of, computed_at = store.read_latest()
            if signals:
                return signals, variant, as_of
    return [], "none", None


def _signals_to_frame(signals: list[dict]) -> pd.DataFrame:
    if not signals:
        return pd.DataFrame()
    rows = []
    for s in signals:
        demand = s.get("demand") or {}
        rows.append({
            "ticker": s.get("ticker"),
            "alpha_score": s.get("alpha_score"),
            "signal": s.get("signal"),
            "regime": s.get("regime"),
            "overextension_penalty": s.get("overextension_penalty"),
            "demand_multiplier": s.get("demand_multiplier"),
            "demand_net": demand.get("net"),
            "breakout_prob_swing": s.get("breakout_prob_swing"),
            "breakout_prob_trend": s.get("breakout_prob_trend"),
            "breakout_prob_thematic": s.get("breakout_prob_thematic"),
        })
    return pd.DataFrame(rows)


def _feature_coverage(
    data_dir: str,
    tickers: list[str],
    min_market_cap: float,
    *,
    max_feature_tickers: int | None = None,
) -> tuple[dict[str, int], bool, int]:
    """Latest feature rows for snapshot tickers (coverage counts).

    Returns (counts, capped, tickers_used).
    """
    if not tickers:
        return {c: 0 for c in _FEATURE_COLS}, False, 0

    capped = False
    used = tickers
    if max_feature_tickers is not None and len(tickers) > max_feature_tickers:
        used = tickers[:max_feature_tickers]
        capped = True
        logger.info(
            "feature_coverage_capped",
            requested=len(tickers),
            built=len(used),
            max_feature_tickers=max_feature_tickers,
        )

    features = build_latest_features(
        data_dir=data_dir,
        min_market_cap=min_market_cap,
        tickers=used,
    )
    if features.empty:
        return {c: 0 for c in _FEATURE_COLS}, capped, len(used)

    out: dict[str, int] = {"feature_rows": len(features)}
    for col in _FEATURE_COLS:
        if col in features.columns:
            out[col] = _has_value(features[col])
        else:
            out[col] = 0
    return out, capped, len(used)


def run_audit(
    *,
    data_dir: str,
    min_market_cap: float,
    max_feature_tickers: int | None = None,
) -> dict:
    signals, variant, as_of = _load_snapshot(data_dir)
    snap_df = _signals_to_frame(signals)
    universe = len(snap_df)

    tickers = (
        [str(t) for t in snap_df["ticker"].dropna().unique().tolist()]
        if not snap_df.empty
        else []
    )
    feature_counts, feature_capped, feature_tickers_used = _feature_coverage(
        data_dir,
        tickers,
        min_market_cap,
        max_feature_tickers=max_feature_tickers,
    )

    has_ml = 0
    if not snap_df.empty:
        ml_present = pd.Series(False, index=snap_df.index)
        for col in _ML_PROB_COLS:
            if col in snap_df.columns:
                ml_present = ml_present | snap_df[col].notna()
        has_ml = int(ml_present.sum())

    counts = {
        "universe": universe,
        "has_f_rev_growth_yoy": feature_counts.get("f_rev_growth_yoy", 0),
        "has_e_eps_revision_90d": feature_counts.get("e_eps_revision_90d", 0),
        "has_cat_demand_score": feature_counts.get("cat_demand_score", 0),
        "has_si_days_to_cover": feature_counts.get("si_days_to_cover", 0),
        "has_ml_probabilities": has_ml,
        "alpha_score_gte_44": _count_scores(snap_df.get("alpha_score", pd.Series(dtype=float)), 44),
        "alpha_score_gte_58": _count_scores(snap_df.get("alpha_score", pd.Series(dtype=float)), 58),
        "alpha_score_gte_72": _count_scores(snap_df.get("alpha_score", pd.Series(dtype=float)), 72),
    }

    top25 = []
    if not snap_df.empty:
        top = snap_df.sort_values("alpha_score", ascending=False).head(25)
        for _, row in top.iterrows():
            top25.append({
                "ticker": row["ticker"],
                "alpha_score": row["alpha_score"],
                "signal": row["signal"],
                "regime": row["regime"],
                "overextension_penalty": row["overextension_penalty"],
                "demand_multiplier": row["demand_multiplier"],
                "demand_net": row["demand_net"],
            })

    return {
        "snapshot_variant": variant,
        "as_of_date": as_of,
        "feature_rows_built": feature_counts.get("feature_rows", 0),
        "feature_tickers_requested": len(tickers),
        "feature_tickers_built": feature_tickers_used,
        "feature_coverage_capped": feature_capped,
        "min_market_cap": min_market_cap,
        "counts": counts,
        "top_25_by_alpha_score": top25,
        "notes": [
            "Score-threshold counts come from the alpha snapshot.",
            "Feature-column counts come from build_latest_features() for snapshot tickers.",
            "Use --max-feature-tickers to cap slow rebuilds on large snapshots.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Alpha funnel diagnostic (read-only)")
    parser.add_argument("--data-dir", default="data", help="Root data directory")
    parser.add_argument(
        "--output",
        default="data/ml/alpha_results/funnel_audit.json",
        help="JSON output path",
    )
    parser.add_argument(
        "--max-feature-tickers",
        type=int,
        default=None,
        help="Cap build_latest_features to first N snapshot tickers (faster smoke runs)",
    )
    args = parser.parse_args()

    settings = get_settings()
    min_cap = settings.alpha_min_market_cap_millions * 1e6

    report = run_audit(
        data_dir=args.data_dir,
        min_market_cap=min_cap,
        max_feature_tickers=args.max_feature_tickers,
    )

    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(report, indent=2))

    counts = report["counts"]
    print(f"\n{'=' * 60}")
    print(f"ALPHA FUNNEL AUDIT (variant={report['snapshot_variant']}, as_of={report['as_of_date']})")
    print(f"{'=' * 60}")
    for key, val in counts.items():
        print(f"  {key:<28} {val:>8}")
    print(f"\nTop 25 by alpha_score:")
    for row in report["top_25_by_alpha_score"]:
        print(
            f"  {row['ticker']:<6} score={row['alpha_score']:.1f} "
            f"sig={row['signal']:<11} regime={row['regime']:<9} "
            f"penalty={row['overextension_penalty']} demand_mult={row['demand_multiplier']} "
            f"net={row['demand_net']}"
        )
    print(f"\nWrote {out_path}")


if __name__ == "__main__":
    main()
