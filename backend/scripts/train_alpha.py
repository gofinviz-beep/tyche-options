"""CLI: Train + evaluate the directional Alpha (big-move) models.

Phase 1 of the Directional Alpha Engine. This is the GO/NO-GO gate: it runs
walk-forward evaluation of the big-move targets with momentum features ON vs
OFF and reports AUC/precision. If momentum features show no edge over the
~0.5 baseline, we stop before spending on partner data.

Usage:
    # Full pipeline: build dataset (with momentum) + evaluate + save models
    python scripts/train_alpha.py

    # Reuse an existing dataset parquet (must have big_move_* labels + momentum cols)
    python scripts/train_alpha.py --dataset data/ml/dataset.parquet

    # Build dataset only
    python scripts/train_alpha.py --build-only --output data/ml/alpha_dataset.parquet

    # Quick smoke test
    python scripts/train_alpha.py --max-tickers 50 --no-save-model

    # Evaluate only (skip saving production models)
    python scripts/train_alpha.py --no-save-model
"""

import argparse
import sys
import time

sys.path.insert(0, "src")

import structlog

logger = structlog.get_logger()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train + evaluate the Tyche directional Alpha big-move models",
    )
    parser.add_argument("--dataset", type=str, default=None, help="Path to pre-built dataset Parquet")
    parser.add_argument("--build-only", action="store_true", help="Only build + save the dataset")
    parser.add_argument("--output", type=str, default="data/ml/alpha_dataset.parquet", help="Output path for built dataset")
    parser.add_argument("--results-dir", type=str, default="data/ml/alpha_results", help="Directory for evaluation reports")
    parser.add_argument("--targets", nargs="+", default=None, help="Big-move target labels to evaluate")
    parser.add_argument("--train-days", type=int, default=252, help="Walk-forward train window (default 252 ~ 1yr)")
    parser.add_argument("--test-days", type=int, default=63, help="Walk-forward test window (default 63 ~ 3mo)")
    parser.add_argument("--max-tickers", type=int, default=None, help="Limit tickers for quick testing")
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=2e9,
        help="Min market cap (default $2B conservative). Use 250e6–500e6 only for discovery training.",
    )
    parser.add_argument(
        "--discovery-train",
        action="store_true",
        help="Use alpha_discovery_train_min_market_cap_millions from config for universe floor",
    )
    parser.add_argument("--data-dir", type=str, default="data", help="Root data directory")
    parser.add_argument("--save-model", action="store_true", default=True, help="Train + save production big-move models (default True)")
    parser.add_argument("--no-save-model", action="store_false", dest="save_model", help="Skip saving production models")
    parser.add_argument(
        "--feature-set",
        choices=["momentum", "demand"],
        default="momentum",
        help="momentum = v1 set; demand = Demand Conviction v2 (fundamentals + "
        "estimates + over-extension + short interest). Default momentum.",
    )
    parser.add_argument(
        "--sustained",
        action="store_true",
        help="Target the sustained big-move labels (forward close at horizon) "
        "instead of intra-window peaks — the de-biased Demand Conviction target.",
    )

    args = parser.parse_args()

    from tyche.config import get_settings
    from tyche.ml.dataset import build_dataset, load_dataset, save_dataset
    from tyche.ml.xgb_baseline import (
        ALPHA_SUSTAINED_TARGETS,
        ALPHA_TARGETS,
        demand_feature_columns,
        get_feature_columns,
        run_alpha_baselines,
        run_demand_baselines,
        train_production_model,
    )

    settings = get_settings()
    min_cap = args.min_market_cap
    if args.discovery_train:
        min_cap = settings.alpha_discovery_train_min_market_cap_millions * 1e6
    logger.info("train_universe", min_market_cap=min_cap, discovery_train=args.discovery_train)

    t0 = time.time()

    if args.dataset:
        logger.info("loading_dataset", path=args.dataset)
        dataset = load_dataset(args.dataset)
        logger.info("dataset_loaded", rows=len(dataset), tickers=dataset["ticker"].nunique())
    else:
        logger.info(
            "building_dataset",
            data_dir=args.data_dir,
            max_tickers=args.max_tickers,
            min_market_cap=min_cap,
        )
        dataset = build_dataset(
            data_dir=args.data_dir,
            min_market_cap=min_cap,
            max_tickers=args.max_tickers,
            include_neighbors=True,
            include_momentum=True,
        )
        if dataset.empty:
            logger.error("dataset_is_empty")
            sys.exit(1)
        save_dataset(dataset, args.output)

    if args.build_only:
        logger.info("build_only_complete", rows=len(dataset))
        return

    _print_label_summary(dataset)

    use_demand = args.feature_set == "demand"
    default_targets = ALPHA_SUSTAINED_TARGETS if args.sustained else ALPHA_TARGETS
    targets = args.targets or default_targets

    if use_demand:
        # Ablation: momentum-only vs full Demand Conviction feature set.
        reports = run_demand_baselines(
            dataset=dataset,
            targets=targets,
            train_days=args.train_days,
            test_days=args.test_days,
            output_dir=args.results_dir,
            use_class_weighting=settings.alpha_class_weighting_enabled,
            use_purged_splits=settings.alpha_purged_walk_forward_enabled,
            use_missingness_indicators=settings.alpha_discovery_enabled,
        )
        if reports:
            _print_demand_gate(reports)
        feature_cols = demand_feature_columns()
    else:
        reports = run_alpha_baselines(
            dataset=dataset,
            targets=targets,
            train_days=args.train_days,
            test_days=args.test_days,
            output_dir=args.results_dir,
            use_class_weighting=settings.alpha_class_weighting_enabled,
            use_purged_splits=settings.alpha_purged_walk_forward_enabled,
        )
        if reports:
            _print_go_no_go(reports)
        feature_cols = get_feature_columns(include_momentum=True)

    if args.save_model:
        for target in targets:
            if target not in dataset.columns:
                continue
            print(f"\n{'=' * 70}")
            print(f"TRAINING PRODUCTION MODEL: {target} ({args.feature_set})")
            print(f"{'=' * 70}")
            train_production_model(
                dataset=dataset,
                target=target,
                feature_cols=feature_cols,
                data_dir=args.data_dir,
                use_class_weighting=settings.alpha_class_weighting_enabled,
                use_missingness_indicators=(
                    use_demand and settings.alpha_discovery_enabled
                ),
            )
            print(f"Saved: data/ml/models/{target}.json")

    elapsed = time.time() - t0
    print(f"\nTotal elapsed: {elapsed:.0f}s")
    print(f"Reports saved to: {args.results_dir}/")


def _print_label_summary(dataset) -> None:
    print(f"\n{'=' * 70}")
    print("ALPHA DATASET SUMMARY")
    print(f"{'=' * 70}")
    print(f"Rows:    {len(dataset):,}")
    print(f"Tickers: {dataset['ticker'].nunique()}")

    big_move_cols = [c for c in dataset.columns if c.startswith("big_move_up_")]
    if big_move_cols:
        print(f"\nBig-move labels ({len(big_move_cols)}):")
        for col in sorted(big_move_cols):
            valid = dataset[col].notna().sum()
            if valid > 0:
                rate = dataset[col].dropna().mean()
                print(f"  {col:<28} valid={valid:>10,}  positive_rate={rate:.3f}")
    print(f"{'=' * 70}\n")


def _print_demand_gate(reports) -> None:
    """Does the Demand Conviction feature set beat momentum-only?"""
    print(f"\n{'=' * 70}")
    print("DEMAND GATE: Do fundamentals/estimates/anti-chase add lift?")
    print(f"{'=' * 70}")
    targets_seen = sorted({r.target for r in reports})
    for target in targets_seen:
        mom = [r for r in reports if r.target == target and r.feature_set == "momentum"]
        dem = [r for r in reports if r.target == target and r.feature_set == "demand"]
        if mom and dem:
            m, d = mom[0], dem[0]
            delta = d.mean_auc - m.mean_auc
            verdict = "GO" if delta > 0.005 else ("FLAT" if delta > -0.005 else "REGRESS")
            print(
                f"\n  {target}:"
                f"\n    Momentum-only: auc={m.mean_auc:.4f}  prec={m.mean_precision:.1f}%"
                f"\n    Demand (v2):   auc={d.mean_auc:.4f}  prec={d.mean_precision:.1f}%"
                f"\n    Lift: {delta:+.4f} auc  ->  {verdict}"
            )
    print(
        "\n  Keep the demand groups only if they add AUC/precision lift; drop "
        "non-additive groups (same discipline that dropped the MACD/MTF group)."
    )
    print(f"{'=' * 70}")


def _print_go_no_go(reports) -> None:
    """The decision: does the momentum feature group add directional edge?"""
    print(f"\n{'=' * 70}")
    print("GO / NO-GO: Do momentum features predict big moves?")
    print(f"{'=' * 70}")

    targets_seen = sorted({r.target for r in reports})
    for target in targets_seen:
        base = [r for r in reports if r.target == target and r.feature_set == "baseline"]
        mom = [r for r in reports if r.target == target and r.feature_set == "momentum"]
        if base and mom:
            b, m = base[0], mom[0]
            delta_auc = m.mean_auc - b.mean_auc
            verdict = "GO" if m.mean_auc >= 0.60 and delta_auc > 0.005 else (
                "WEAK" if m.mean_auc >= 0.55 else "NO-GO"
            )
            print(
                f"\n  {target}:"
                f"\n    Baseline (no momentum): auc={b.mean_auc:.4f}  prec={b.mean_precision:.1f}%"
                f"\n    Momentum:               auc={m.mean_auc:.4f}  prec={m.mean_precision:.1f}%"
                f"\n    Lift: {delta_auc:+.4f} auc  ->  {verdict}"
            )
    print(
        "\n  Interpretation: AUC >= 0.60 with positive lift = build out the engine."
        "\n  AUC ~0.55-0.60 = marginal (technicals alone are weak; partner data may help)."
        "\n  AUC < 0.55 = big moves are not predictable from these features; reconsider."
    )
    print(f"{'=' * 70}")


if __name__ == "__main__":
    main()
