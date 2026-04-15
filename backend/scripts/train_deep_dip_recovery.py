"""Train XGBoost models for deep dip recovery prediction.

Builds a dataset filtered to oversold conditions ($1B+ cap, 60%+ institutional
ownership), then trains:
  1. Classification: P(recovery above 21-EMA) within 10/20/40 days
  2. Regression: expected peak recovery % within 10/20/40/60 days
  3. Regression: expected days to EMA recovery

These models learn per-ticker dip signatures — how deep each stock typically
dips, how fast it recovers, and how far past the pre-dip level it bounces.
This directly informs covered call strike selection and DTE.

Usage:
    # Full pipeline — run in screen for long execution
    screen -S dip_training
    cd backend && .venv/bin/python scripts/train_deep_dip_recovery.py
    # Ctrl+A, D to detach; screen -r dip_training to reattach

    # Quick test with fewer tickers
    .venv/bin/python scripts/train_deep_dip_recovery.py --max-tickers 50

    # Custom filters
    .venv/bin/python scripts/train_deep_dip_recovery.py --min-market-cap 4e9 --min-inst-pct 0.5
"""

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, "src")

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()

DIP_THRESHOLD_PCT = 5.0
MIN_PRIOR_STREAK = 5


def _identify_dip_rows(dataset: pd.DataFrame) -> pd.DataFrame:
    """Filter dataset to rows where the stock is in a deep dip.

    A deep dip is defined as price >= DIP_THRESHOLD_PCT below
    the 21-EMA, with a prior uptrend streak.
    """
    if "price_to_21ema_pct" not in dataset.columns:
        logger.error("missing_price_to_21ema_pct_column")
        return pd.DataFrame()

    dip_mask = dataset["price_to_21ema_pct"] <= -DIP_THRESHOLD_PCT

    if "days_above_streak" in dataset.columns:
        streak_col = "days_above_streak"
    elif "prior_streak" in dataset.columns:
        streak_col = "prior_streak"
    else:
        streak_col = None

    if streak_col:
        streak_mask = dataset[streak_col] >= MIN_PRIOR_STREAK
        mask = dip_mask & streak_mask
        logger.info(
            "dip_filter_applied",
            total_rows=len(dataset),
            below_ema=int(dip_mask.sum()),
            with_streak=int(mask.sum()),
            streak_col=streak_col,
        )
    else:
        mask = dip_mask
        logger.info(
            "dip_filter_applied",
            total_rows=len(dataset),
            below_ema=int(dip_mask.sum()),
            streak_filter="skipped (no streak column)",
        )

    return dataset[mask].copy()


def _filter_institutional(
    dataset: pd.DataFrame,
    min_pct: float,
) -> pd.DataFrame:
    """Filter to tickers with institutional ownership >= min_pct."""
    if "institutional_pct" not in dataset.columns:
        logger.warning("no_institutional_pct_column, skipping filter")
        return dataset

    has_data = dataset["institutional_pct"].notna()
    above = dataset["institutional_pct"] >= min_pct
    mask = has_data & above

    dropped = has_data.sum() - mask.sum()
    logger.info(
        "institutional_filter",
        with_data=int(has_data.sum()),
        above_threshold=int(mask.sum()),
        dropped=int(dropped),
        min_pct=min_pct,
    )

    no_data = ~has_data
    return dataset[mask | no_data].copy()


def _print_dip_summary(dip_df: pd.DataFrame) -> None:
    """Print summary of the deep dip dataset."""
    n = len(dip_df)
    tickers = dip_df["ticker"].nunique()
    print(f"\n{'=' * 80}")
    print("DEEP DIP DATASET SUMMARY")
    print(f"{'=' * 80}")
    print(f"Total dip rows:    {n:,}")
    print(f"Unique tickers:    {tickers}")

    if "date" in dip_df.columns:
        dates = pd.to_datetime(dip_df["date"]).dt.date
        print(f"Date range:        {dates.min()} → {dates.max()}")

    if "price_to_21ema_pct" in dip_df.columns:
        pct = dip_df["price_to_21ema_pct"]
        print(f"Dip depth:         mean={pct.mean():.1f}%, median={pct.median():.1f}%, min={pct.min():.1f}%")

    for label in ["deep_dip_recovery_10d", "deep_dip_recovery_20d", "deep_dip_recovery_40d"]:
        if label in dip_df.columns:
            valid = dip_df[label].dropna()
            if len(valid) > 0:
                print(f"  {label:<30} n={len(valid):,}  recovery_rate={valid.mean():.1%}")

    for label in ["peak_recovery_pct_10d", "peak_recovery_pct_20d", "peak_recovery_pct_40d", "peak_recovery_pct_60d"]:
        if label in dip_df.columns:
            valid = dip_df[label].dropna()
            if len(valid) > 0:
                print(f"  {label:<30} n={len(valid):,}  mean={valid.mean():.1f}%  median={valid.median():.1f}%  p75={valid.quantile(0.75):.1f}%")

    if "days_to_ema_recovery" in dip_df.columns:
        valid = dip_df["days_to_ema_recovery"].dropna()
        if len(valid) > 0:
            print(f"  days_to_ema_recovery          n={len(valid):,}  mean={valid.mean():.1f}d  median={valid.median():.0f}d  p75={valid.quantile(0.75):.0f}d")

    top_tickers = dip_df["ticker"].value_counts().head(20)
    print(f"\nTop 20 most frequent dip tickers:")
    for ticker, count in top_tickers.items():
        subset = dip_df[dip_df["ticker"] == ticker]
        rec_col = "deep_dip_recovery_20d"
        if rec_col in subset.columns:
            rec_rate = subset[rec_col].dropna().mean()
            avg_depth = subset["price_to_21ema_pct"].mean()
            print(f"  {ticker:<8} {count:4d} dips  avg_depth={avg_depth:+.1f}%  rec_20d={rec_rate:.0%}")

    print(f"{'=' * 80}\n")


def _train_classification(
    dip_df: pd.DataFrame,
    feature_cols: list[str],
    target: str,
    train_days: int,
    test_days: int,
) -> dict | None:
    """Train and walk-forward evaluate an XGBoost classifier."""
    try:
        import xgboost as xgb
        from sklearn.metrics import accuracy_score, roc_auc_score
    except ImportError:
        logger.error("xgboost_not_installed")
        return None

    valid = dip_df.dropna(subset=[target]).copy()
    if len(valid) < 200:
        print(f"  {target}: only {len(valid)} valid rows, skipping (need >= 200)")
        return None

    available = [c for c in feature_cols if c in valid.columns]
    valid = valid.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(valid["date"]).dt.date
    unique_dates = sorted(dates.unique())

    if len(unique_dates) < train_days + test_days:
        print(f"  {target}: only {len(unique_dates)} unique dates, need {train_days + test_days}")
        return None

    params = {
        "objective": "binary:logistic",
        "eval_metric": "auc",
        "max_depth": 6,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 10,
        "random_state": 42,
        "verbosity": 0,
    }

    windows = []
    step = test_days
    i = 0
    while i + train_days + test_days <= len(unique_dates):
        train_end_date = unique_dates[i + train_days - 1]
        test_start_date = unique_dates[i + train_days]
        test_end_idx = min(i + train_days + test_days - 1, len(unique_dates) - 1)
        test_end_date = unique_dates[test_end_idx]

        train_mask = dates <= train_end_date
        test_mask = (dates > train_end_date) & (dates <= test_end_date)

        if train_mask.sum() < 50 or test_mask.sum() < 20:
            i += step
            continue

        X_train = valid.loc[train_mask, available].fillna(-999)
        y_train = valid.loc[train_mask, target]
        X_test = valid.loc[test_mask, available].fillna(-999)
        y_test = valid.loc[test_mask, target]

        model = xgb.XGBClassifier(**params)
        model.fit(X_train, y_train, verbose=False)

        y_pred = model.predict(X_test)
        y_prob = model.predict_proba(X_test)[:, 1]

        acc = accuracy_score(y_test, y_pred)
        try:
            auc = roc_auc_score(y_test, y_prob)
        except ValueError:
            auc = 0.5

        feat_imp = dict(zip(available, model.feature_importances_))

        windows.append({
            "train_end": str(train_end_date),
            "test_start": str(test_start_date),
            "test_end": str(test_end_date),
            "n_train": int(train_mask.sum()),
            "n_test": int(test_mask.sum()),
            "accuracy": acc,
            "auc": auc,
            "positive_rate": float(y_test.mean()),
            "feature_importance": feat_imp,
        })

        i += step

    if not windows:
        print(f"  {target}: no valid windows")
        return None

    avg_acc = np.mean([w["accuracy"] for w in windows])
    avg_auc = np.mean([w["auc"] for w in windows])
    avg_pos = np.mean([w["positive_rate"] for w in windows])

    print(f"\n  {target}:")
    print(f"    Windows:  {len(windows)}")
    print(f"    Avg Acc:  {avg_acc:.1%}")
    print(f"    Avg AUC:  {avg_auc:.4f}")
    print(f"    Avg +Rate: {avg_pos:.1%}")

    all_imp: dict[str, list[float]] = {}
    for w in windows:
        for feat, score in w["feature_importance"].items():
            all_imp.setdefault(feat, []).append(score)
    avg_imp = {k: np.mean(v) for k, v in all_imp.items()}
    top_10 = sorted(avg_imp.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"    Top 10 features:")
    for feat, imp in top_10:
        print(f"      {feat:<35} {imp:.4f}")

    return {
        "target": target,
        "model_type": "classification",
        "windows": len(windows),
        "avg_accuracy": avg_acc,
        "avg_auc": avg_auc,
        "avg_positive_rate": avg_pos,
        "top_features": top_10,
    }


def _train_regression(
    dip_df: pd.DataFrame,
    feature_cols: list[str],
    target: str,
    train_days: int,
    test_days: int,
) -> dict | None:
    """Train and walk-forward evaluate an XGBoost regressor."""
    try:
        import xgboost as xgb
        from sklearn.metrics import mean_absolute_error, r2_score
    except ImportError:
        return None

    valid = dip_df.dropna(subset=[target]).copy()
    if len(valid) < 200:
        print(f"  {target}: only {len(valid)} valid rows, skipping")
        return None

    available = [c for c in feature_cols if c in valid.columns]
    valid = valid.sort_values("date").reset_index(drop=True)
    dates = pd.to_datetime(valid["date"]).dt.date
    unique_dates = sorted(dates.unique())

    if len(unique_dates) < train_days + test_days:
        print(f"  {target}: only {len(unique_dates)} unique dates, need {train_days + test_days}")
        return None

    params = {
        "objective": "reg:squarederror",
        "max_depth": 6,
        "learning_rate": 0.05,
        "n_estimators": 200,
        "subsample": 0.8,
        "colsample_bytree": 0.8,
        "min_child_weight": 10,
        "random_state": 42,
        "verbosity": 0,
    }

    windows = []
    step = test_days
    i = 0
    while i + train_days + test_days <= len(unique_dates):
        train_end_date = unique_dates[i + train_days - 1]
        test_end_idx = min(i + train_days + test_days - 1, len(unique_dates) - 1)
        test_end_date = unique_dates[test_end_idx]

        train_mask = dates <= train_end_date
        test_mask = (dates > train_end_date) & (dates <= test_end_date)

        if train_mask.sum() < 50 or test_mask.sum() < 20:
            i += step
            continue

        X_train = valid.loc[train_mask, available].fillna(-999)
        y_train = valid.loc[train_mask, target]
        X_test = valid.loc[test_mask, available].fillna(-999)
        y_test = valid.loc[test_mask, target]

        model = xgb.XGBRegressor(**params)
        model.fit(X_train, y_train, verbose=False)

        y_pred = model.predict(X_test)

        mae = mean_absolute_error(y_test, y_pred)
        r2 = r2_score(y_test, y_pred)

        feat_imp = dict(zip(available, model.feature_importances_))

        windows.append({
            "n_train": int(train_mask.sum()),
            "n_test": int(test_mask.sum()),
            "mae": mae,
            "r2": r2,
            "actual_mean": float(y_test.mean()),
            "pred_mean": float(y_pred.mean()),
            "feature_importance": feat_imp,
        })

        i += step

    if not windows:
        return None

    avg_mae = np.mean([w["mae"] for w in windows])
    avg_r2 = np.mean([w["r2"] for w in windows])
    avg_actual = np.mean([w["actual_mean"] for w in windows])

    print(f"\n  {target}:")
    print(f"    Windows:    {len(windows)}")
    print(f"    Avg MAE:    {avg_mae:.2f}%")
    print(f"    Avg R²:     {avg_r2:.4f}")
    print(f"    Avg Actual: {avg_actual:.2f}%")

    all_imp: dict[str, list[float]] = {}
    for w in windows:
        for feat, score in w["feature_importance"].items():
            all_imp.setdefault(feat, []).append(score)
    avg_imp = {k: np.mean(v) for k, v in all_imp.items()}
    top_10 = sorted(avg_imp.items(), key=lambda x: x[1], reverse=True)[:10]
    print(f"    Top 10 features:")
    for feat, imp in top_10:
        print(f"      {feat:<35} {imp:.4f}")

    return {
        "target": target,
        "model_type": "regression",
        "windows": len(windows),
        "avg_mae": avg_mae,
        "avg_r2": avg_r2,
        "top_features": top_10,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Train XGBoost models for deep dip recovery prediction",
    )
    parser.add_argument("--min-market-cap", type=float, default=1e9)
    parser.add_argument("--min-inst-pct", type=float, default=0.60)
    parser.add_argument("--max-tickers", type=int, default=None)
    parser.add_argument("--data-dir", type=str, default="data")
    parser.add_argument("--train-days", type=int, default=252)
    parser.add_argument("--test-days", type=int, default=63)
    parser.add_argument(
        "--dataset", type=str, default=None,
        help="Load pre-built dataset from Parquet instead of building",
    )
    parser.add_argument(
        "--save-dataset", type=str, default="data/ml/deep_dip_dataset.parquet",
        help="Save assembled dataset to Parquet for re-use",
    )
    args = parser.parse_args()

    t0 = time.time()

    from tyche.ml.dataset import build_dataset, load_dataset, save_dataset
    from tyche.ml.xgb_baseline import get_feature_columns

    print(f"\n{'#' * 80}")
    print(f"{'DEEP DIP RECOVERY — XGBoost TRAINING':^80}")
    print(f"{'#' * 80}")
    print(f"\nFilters: min_market_cap=${args.min_market_cap/1e9:.0f}B, min_institutional={args.min_inst_pct:.0%}")

    if args.dataset:
        print(f"Loading pre-built dataset: {args.dataset}")
        dataset = load_dataset(args.dataset)
    else:
        print("Building dataset (this may take several minutes)...")
        dataset = build_dataset(
            data_dir=args.data_dir,
            min_market_cap=args.min_market_cap,
            max_tickers=args.max_tickers,
            include_neighbors=True,
            include_etf=True,
            include_correlation=True,
        )

    if dataset.empty:
        print("ERROR: Dataset is empty")
        sys.exit(1)

    print(f"Full dataset: {len(dataset):,} rows, {dataset['ticker'].nunique()} tickers")

    if args.save_dataset and not args.dataset:
        save_dataset(dataset, args.save_dataset)
        print(f"Dataset saved to {args.save_dataset}")

    dataset = _filter_institutional(dataset, args.min_inst_pct)
    print(f"After institutional filter: {len(dataset):,} rows, {dataset['ticker'].nunique()} tickers")

    dip_df = _identify_dip_rows(dataset)

    if dip_df.empty or len(dip_df) < 100:
        print(f"\nOnly {len(dip_df)} dip rows found. Relaxing streak requirement...")
        global MIN_PRIOR_STREAK
        MIN_PRIOR_STREAK = 0
        dip_df = _identify_dip_rows(dataset)

    if dip_df.empty:
        print("ERROR: No deep dip rows found in dataset")
        sys.exit(1)

    _print_dip_summary(dip_df)

    feature_cols = get_feature_columns(
        include_neighbors=True,
        include_etf=True,
        include_correlation=True,
        include_market_context=True,
    )
    available_features = [c for c in feature_cols if c in dip_df.columns]
    print(f"Features available: {len(available_features)}")

    # ── Classification: Will it recover? ──────────────────────────
    print(f"\n{'=' * 80}")
    print("CLASSIFICATION MODELS: P(recovery above 21-EMA)")
    print(f"{'=' * 80}")

    classification_results = []
    for target in ["deep_dip_recovery_10d", "deep_dip_recovery_20d", "deep_dip_recovery_40d"]:
        result = _train_classification(
            dip_df, available_features, target,
            args.train_days, args.test_days,
        )
        if result:
            classification_results.append(result)

    # ── Regression: How far will it recover? ──────────────────────
    print(f"\n{'=' * 80}")
    print("REGRESSION MODELS: Peak recovery magnitude (%)")
    print(f"{'=' * 80}")

    regression_results = []
    for target in ["peak_recovery_pct_10d", "peak_recovery_pct_20d", "peak_recovery_pct_40d", "peak_recovery_pct_60d"]:
        result = _train_regression(
            dip_df, available_features, target,
            args.train_days, args.test_days,
        )
        if result:
            regression_results.append(result)

    # ── Regression: How long until recovery? ──────────────────────
    print(f"\n{'=' * 80}")
    print("REGRESSION MODEL: Days to EMA recovery")
    print(f"{'=' * 80}")

    days_result = _train_regression(
        dip_df, available_features, "days_to_ema_recovery",
        args.train_days, args.test_days,
    )
    if days_result:
        regression_results.append(days_result)

    # ── Final summary ─────────────────────────────────────────────
    elapsed = time.time() - t0
    print(f"\n{'#' * 80}")
    print(f"{'SUMMARY':^80}")
    print(f"{'#' * 80}")
    print(f"\nTotal elapsed: {elapsed:.0f}s ({elapsed/60:.1f} min)")
    print(f"Dip rows used: {len(dip_df):,} ({dip_df['ticker'].nunique()} tickers)")

    if classification_results:
        print(f"\nClassification (will it recover?):")
        print(f"  {'Target':<35} {'Windows':>7} {'Acc':>8} {'AUC':>8} {'Pos%':>7}")
        print(f"  {'-' * 65}")
        for r in classification_results:
            print(
                f"  {r['target']:<35} {r['windows']:>7} "
                f"{r['avg_accuracy']:>7.1%} {r['avg_auc']:>7.4f} "
                f"{r['avg_positive_rate']:>6.1%}"
            )

    if regression_results:
        print(f"\nRegression (how far/long?):")
        print(f"  {'Target':<35} {'Windows':>7} {'MAE':>8} {'R²':>8}")
        print(f"  {'-' * 58}")
        for r in regression_results:
            print(
                f"  {r['target']:<35} {r['windows']:>7} "
                f"{r['avg_mae']:>7.2f} {r['avg_r2']:>7.4f}"
            )

    print(f"\n{'#' * 80}")


if __name__ == "__main__":
    main()
