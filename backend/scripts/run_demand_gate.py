"""Step 3 orchestration: demand-feature gate + conditional promotion (one pass).

Runs the full Demand Conviction v2 retrain decision end-to-end so it can be
launched once (e.g. inside a detached ``screen`` session) and left to finish:

  1. Build the dataset once — demand features (D-FUND/D-EST/D-CAT/short interest/
     graph) + sustained big-move labels — and cache it to Parquet.
  2. Walk-forward ablation per horizon: momentum-only vs. the full demand feature
     set, on the *sustained* big-move targets (the de-biased target the v2 scorer
     is meant to serve).
  3. Promote (train + persist) the demand-feature production model **only** for
     horizons where demand adds at least ``--min-lift`` AUC over momentum.

Safe to background: promotion writes the *sustained* model artifacts
(``big_move_sustained_*``), which are net-new and do NOT overwrite the peak
``big_move_up_*`` models the live ``BreakoutPredictor`` currently serves. Flipping
the engine onto the sustained models is a separate, deliberate step gated on the
verdict this script prints (and writes to ``demand_gate_verdict.json``).

Usage (from ``backend/``):
    .venv/bin/python scripts/run_demand_gate.py                 # full universe
    .venv/bin/python scripts/run_demand_gate.py --max-tickers 40 --no-promote  # smoke
    .venv/bin/python scripts/run_demand_gate.py --dataset data/ml/alpha_dataset.parquet
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import structlog

from tyche.ops.job_progress import log_job_phase, log_job_progress

logger = structlog.get_logger()

JOB_NAME = "run-demand-gate"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Demand-feature gate + conditional promotion (Step 3)",
    )
    parser.add_argument("--data-dir", default="data", help="Root data directory")
    parser.add_argument(
        "--dataset", default=None, help="Reuse a pre-built dataset Parquet (skips build)"
    )
    parser.add_argument(
        "--output",
        default="data/ml/alpha_dataset.parquet",
        help="Where to cache the built dataset",
    )
    parser.add_argument(
        "--results-dir", default="data/ml/alpha_results", help="Directory for reports"
    )
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=2e9,
        help="Min market cap (default $2B). Use --discovery-train for $250M discovery floor.",
    )
    parser.add_argument(
        "--discovery-train",
        action="store_true",
        help="Use alpha_discovery_train_min_market_cap_millions from config",
    )
    parser.add_argument("--max-tickers", type=int, default=None, help="Limit tickers (smoke)")
    parser.add_argument("--train-days", type=int, default=252)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument(
        "--min-lift",
        type=float,
        default=0.005,
        help="Min AUC lift (demand - momentum) to promote a horizon (default 0.005)",
    )
    parser.add_argument(
        "--targets", nargs="+", default=None, help="Override the sustained targets to evaluate"
    )
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Run the ablation only; never train/save production models",
    )
    args = parser.parse_args()

    from tyche.config import get_settings
    from tyche.ml.dataset import build_dataset, load_dataset, save_dataset
    from tyche.ml.xgb_baseline import (
        ALPHA_SUSTAINED_TARGETS,
        demand_feature_columns,
        run_demand_baselines,
        train_production_model,
    )

    settings = get_settings()
    min_cap = args.min_market_cap
    if args.discovery_train:
        min_cap = settings.alpha_discovery_train_min_market_cap_millions * 1e6
    logger.info("train_universe", min_market_cap=min_cap, discovery_train=args.discovery_train)

    t0 = time.time()

    # 1. Dataset (build once, or reuse).
    if args.dataset:
        log_job_phase(JOB_NAME, "load_dataset", path=args.dataset)
        logger.info("loading_dataset", path=args.dataset)
        dataset = load_dataset(args.dataset)
        log_job_phase(
            JOB_NAME,
            "load_dataset",
            status="complete",
            rows=len(dataset),
            tickers=int(dataset["ticker"].nunique()),
        )
    else:
        log_job_phase(
            JOB_NAME,
            "build_dataset",
            data_dir=args.data_dir,
            min_market_cap=min_cap,
            max_tickers=args.max_tickers,
        )
        logger.info(
            "building_dataset",
            data_dir=args.data_dir,
            min_market_cap=min_cap,
            max_tickers=args.max_tickers,
        )
        dataset = build_dataset(
            data_dir=args.data_dir,
            min_market_cap=min_cap,
            max_tickers=args.max_tickers,
            include_neighbors=True,
            include_momentum=True,
            include_demand=True,
            job_name=JOB_NAME,
        )
        if dataset.empty:
            logger.error("dataset_is_empty")
            sys.exit(1)
        save_dataset(dataset, args.output)
    logger.info(
        "dataset_ready",
        rows=len(dataset),
        tickers=int(dataset["ticker"].nunique()),
        elapsed_s=round(time.time() - t0, 1),
    )

    # 2. Ablation on the sustained targets.
    targets = args.targets or ALPHA_SUSTAINED_TARGETS
    log_job_phase(JOB_NAME, "demand_ablation", targets=targets)
    reports = run_demand_baselines(
        dataset=dataset,
        targets=targets,
        train_days=args.train_days,
        test_days=args.test_days,
        output_dir=args.results_dir,
        use_class_weighting=settings.alpha_class_weighting_enabled,
        use_purged_splits=settings.alpha_purged_walk_forward_enabled,
        use_missingness_indicators=settings.alpha_discovery_enabled,
        job_name=JOB_NAME,
    )
    log_job_phase(
        JOB_NAME,
        "demand_ablation",
        status="complete",
        reports=len(reports),
    )
    if not reports:
        logger.error("no_reports", targets=targets)
        sys.exit(1)

    verdict: dict[str, dict] = {}
    for target in sorted({r.target for r in reports}):
        mom = next((r for r in reports if r.target == target and r.feature_set == "momentum"), None)
        dem = next((r for r in reports if r.target == target and r.feature_set == "demand"), None)
        if mom is None or dem is None:
            continue
        lift = dem.mean_auc - mom.mean_auc
        verdict[target] = {
            "momentum_auc": round(mom.mean_auc, 4),
            "demand_auc": round(dem.mean_auc, 4),
            "lift": round(lift, 4),
            "momentum_precision": round(mom.mean_precision, 2),
            "demand_precision": round(dem.mean_precision, 2),
            "decision": "GO" if lift >= args.min_lift else "HOLD",
        }

    print(f"\n{'=' * 72}")
    print(f"DEMAND GATE VERDICT (sustained targets, min_lift={args.min_lift:+.4f})")
    print(f"{'=' * 72}")
    for target, v in verdict.items():
        print(
            f"  {target}:\n"
            f"    momentum: auc={v['momentum_auc']:.4f}  prec={v['momentum_precision']:.1f}%\n"
            f"    demand:   auc={v['demand_auc']:.4f}  prec={v['demand_precision']:.1f}%\n"
            f"    lift={v['lift']:+.4f} auc  ->  {v['decision']}"
        )

    # 3. Conditional promotion (sustained artifacts only — non-destructive).
    promoted: list[str] = []
    if args.no_promote:
        log_job_phase(JOB_NAME, "promote_models", status="skip", reason="no_promote")
        print("\n--no-promote set: ablation only, no models trained.")
    else:
        feature_cols = demand_feature_columns()
        promote_targets = [t for t, v in verdict.items() if v["decision"] == "GO"]
        log_job_phase(
            JOB_NAME,
            "promote_models",
            candidates=len(promote_targets),
        )
        for target, v in verdict.items():
            if v["decision"] != "GO":
                print(f"\n  SKIP {target} (lift {v['lift']:+.4f} < {args.min_lift:+.4f})")
                continue
            log_job_progress(
                JOB_NAME,
                "promote_models",
                done=len(promoted) + 1,
                total=max(len(promote_targets), 1),
                target=target,
            )
            print(f"\n{'=' * 72}")
            print(f"PROMOTE {target} — train production model on demand features")
            print(f"{'=' * 72}")
            train_production_model(
                dataset=dataset,
                target=target,
                feature_cols=feature_cols,
                data_dir=args.data_dir,
                use_class_weighting=settings.alpha_class_weighting_enabled,
                use_missingness_indicators=settings.alpha_discovery_enabled,
            )
            promoted.append(target)
            print(f"Saved: data/ml/models/{target}.json")
        log_job_phase(
            JOB_NAME,
            "promote_models",
            status="complete",
            promoted=len(promoted),
        )

    summary = {
        "elapsed_s": round(time.time() - t0, 1),
        "min_lift": args.min_lift,
        "feature_count": len(demand_feature_columns()),
        "verdict": verdict,
        "promoted": promoted,
    }
    results_dir = Path(args.results_dir)
    results_dir.mkdir(parents=True, exist_ok=True)
    from tyche.storage import write_json
    from tyche.storage.paths import join_uri
    from tyche.storage.store_io import context_for_data_access

    ctx = context_for_data_access(args.data_dir)
    results_rel = str(args.results_dir).replace("\\", "/")
    for prefix in (f"{args.data_dir}/", "data/"):
        if results_rel.startswith(prefix):
            results_rel = results_rel[len(prefix) :]
            break
    verdict_rel = join_uri(results_rel, "demand_gate_verdict.json")
    write_json(summary, verdict_rel, atomic=True, ctx=ctx)

    print(f"\nReports + verdict: {args.results_dir}/")
    print(f"DEMAND_GATE_DONE {json.dumps(summary)}")


if __name__ == "__main__":
    main()
