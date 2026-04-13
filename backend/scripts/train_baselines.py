"""CLI: Build tabular dataset and train XGBoost baseline models.

Phase 1 of the GNN architecture — establish baseline accuracy numbers
that the graph model must beat in Phase 2.

Usage:
    # Full pipeline: build dataset + train + evaluate
    python scripts/train_baselines.py

    # Build dataset only (save to Parquet for re-use)
    python scripts/train_baselines.py --build-only --output data/ml/dataset.parquet

    # Train from saved dataset
    python scripts/train_baselines.py --dataset data/ml/dataset.parquet

    # Specific targets
    python scripts/train_baselines.py --targets csp_win_14d direction_5d

    # Quick test with fewer tickers
    python scripts/train_baselines.py --max-tickers 50

    # Custom walk-forward windows
    python scripts/train_baselines.py --train-days 252 --test-days 63
"""

import argparse
import sys
import time

sys.path.insert(0, "src")

import structlog

logger = structlog.get_logger()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train XGBoost tabular baselines for Tyche GNN Phase 1",
    )
    parser.add_argument(
        "--dataset",
        type=str,
        default=None,
        help="Path to pre-built dataset Parquet file",
    )
    parser.add_argument(
        "--build-only",
        action="store_true",
        help="Only build and save the dataset, skip training",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="data/ml/dataset.parquet",
        help="Output path for built dataset (default: data/ml/dataset.parquet)",
    )
    parser.add_argument(
        "--results-dir",
        type=str,
        default="data/ml/results",
        help="Directory for baseline results (default: data/ml/results)",
    )
    parser.add_argument(
        "--targets",
        nargs="+",
        default=None,
        help="Target labels to evaluate (default: core set)",
    )
    parser.add_argument(
        "--train-days",
        type=int,
        default=126,
        help="Walk-forward train window in trading days (default: 126 ≈ 6mo)",
    )
    parser.add_argument(
        "--test-days",
        type=int,
        default=63,
        help="Walk-forward test window in trading days (default: 63 ≈ 3mo)",
    )
    parser.add_argument(
        "--max-tickers",
        type=int,
        default=None,
        help="Limit number of tickers for quick testing",
    )
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=4e9,
        help="Minimum market cap in dollars (default: 4B)",
    )
    parser.add_argument(
        "--data-dir",
        type=str,
        default="data",
        help="Root data directory (default: data)",
    )
    parser.add_argument(
        "--save-model",
        action="store_true",
        default=True,
        help="Train and save a production model for csp_win_5d (default: True)",
    )
    parser.add_argument(
        "--no-save-model",
        action="store_false",
        dest="save_model",
        help="Skip saving the production model",
    )

    args = parser.parse_args()

    from tyche.ml.dataset import build_dataset, load_dataset, save_dataset
    from tyche.ml.xgb_baseline import run_all_baselines, train_production_model

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
            min_market_cap=args.min_market_cap,
        )
        dataset = build_dataset(
            data_dir=args.data_dir,
            min_market_cap=args.min_market_cap,
            max_tickers=args.max_tickers,
            include_neighbors=True,
        )

        if dataset.empty:
            logger.error("dataset_is_empty")
            sys.exit(1)

        save_dataset(dataset, args.output)

    if args.build_only:
        logger.info("build_only_complete", rows=len(dataset))
        return

    _print_dataset_summary(dataset)

    reports = run_all_baselines(
        dataset=dataset,
        targets=args.targets,
        train_days=args.train_days,
        test_days=args.test_days,
        output_dir=args.results_dir,
    )

    if args.save_model:
        print(f"\n{'=' * 70}")
        print("TRAINING PRODUCTION MODEL: csp_win_5d")
        print(f"{'=' * 70}")
        train_production_model(
            dataset=dataset,
            target="csp_win_5d",
            data_dir=args.data_dir,
        )
        print("Production model saved to: data/ml/models/csp_win_5d.json")

    elapsed = time.time() - t0
    print(f"\nTotal elapsed: {elapsed:.0f}s")
    print(f"Reports saved to: {args.results_dir}/")

    if reports:
        _print_final_summary(reports)


def _print_dataset_summary(dataset) -> None:
    """Print a concise summary of the assembled dataset."""
    import pandas as pd

    print(f"\n{'=' * 70}")
    print("DATASET SUMMARY")
    print(f"{'=' * 70}")
    print(f"Rows:    {len(dataset):,}")
    print(f"Tickers: {dataset['ticker'].nunique()}")

    if "date" in dataset.columns:
        dates = pd.to_datetime(dataset["date"]).dt.date
        print(f"Date range: {dates.min()} → {dates.max()}")
        print(f"Unique dates: {dates.nunique()}")

    label_cols = [c for c in dataset.columns if c.startswith(("csp_win", "direction_", "forward_return", "pullback_recovery", "max_drawdown", "max_gain"))]
    if label_cols:
        print(f"\nLabel columns ({len(label_cols)}):")
        for col in sorted(label_cols):
            non_null = dataset[col].notna().sum()
            if "direction" in col or "csp_win" in col or "pullback" in col:
                if non_null > 0:
                    vals = dataset[col].dropna()
                    print(f"  {col:<30} valid={non_null:,}  mean={vals.mean():.3f}")
            else:
                print(f"  {col:<30} valid={non_null:,}")

    print(f"{'=' * 70}\n")


def _print_final_summary(reports) -> None:
    """Print the key question: does the GNN add signal?"""
    print(f"\n{'=' * 70}")
    print("KEY QUESTION: Do neighbor features add signal?")
    print(f"{'=' * 70}")

    targets_seen = set()
    for r in reports:
        targets_seen.add(r.target)

    for target in sorted(targets_seen):
        single = [r for r in reports if r.target == target and r.feature_set == "single"]
        neighbor = [r for r in reports if r.target == target and r.feature_set == "neighbor"]

        if single and neighbor:
            s = single[0]
            n = neighbor[0]
            delta_acc = n.mean_accuracy - s.mean_accuracy
            delta_auc = n.mean_auc - s.mean_auc
            verdict = "YES ✓" if delta_auc > 0.005 else "NO ✗" if delta_auc < -0.005 else "MARGINAL"

            print(
                f"\n  {target}:"
                f"\n    Single:   acc={s.mean_accuracy:.1f}%  auc={s.mean_auc:.4f}"
                f"\n    Neighbor: acc={n.mean_accuracy:.1f}%  auc={n.mean_auc:.4f}"
                f"\n    Δ acc={delta_acc:+.1f}pp  Δ auc={delta_auc:+.4f}  → {verdict}"
            )

    print(f"\n{'=' * 70}")


if __name__ == "__main__":
    main()
