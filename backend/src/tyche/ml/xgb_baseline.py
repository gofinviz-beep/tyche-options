"""XGBoost baseline models with walk-forward evaluation.

Trains and evaluates XGBoost classifiers for:
  1. CSP safety (binary: put expires worthless)
  2. Direction prediction (5d/10d/20d up/down/flat)
  3. Pullback recovery (binary: price recovers above EMA)

Two model variants:
  A. Per-stock tabular features only
  B. Tabular + neighbor-aggregated sector features

Walk-forward evaluation uses strict temporal splits to prevent leakage.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()

try:
    import xgboost as xgb
    from sklearn.metrics import (
        accuracy_score,
        classification_report,
        f1_score,
        precision_score,
        recall_score,
        roc_auc_score,
    )

    _ML_AVAILABLE = True
except ImportError:
    _ML_AVAILABLE = False


from tyche.ml.features import (
    CORRELATION_FEATURE_COLS,
    ETF_FEATURE_COLS,
    FEATURE_COLS,
    MARKET_CONTEXT_COLS,
    MOMENTUM_FEATURE_COLS,
    NEIGHBOR_FEATURE_COLS,
    RS_FEATURE_COLS,
)

_DEFAULT_XGB_PARAMS: dict = {
    "objective": "binary:logistic",
    "eval_metric": "auc",
    "max_depth": 6,
    "learning_rate": 0.05,
    "n_estimators": 300,
    "subsample": 0.8,
    "colsample_bytree": 0.8,
    "min_child_weight": 10,
    "reg_alpha": 0.1,
    "reg_lambda": 1.0,
    "random_state": 42,
    "tree_method": "hist",
    "verbosity": 0,
}

_MULTICLASS_XGB_PARAMS: dict = {
    **_DEFAULT_XGB_PARAMS,
    "objective": "multi:softprob",
    "eval_metric": "mlogloss",
    "num_class": 3,
}


@dataclass
class ModelResult:
    """Results from a single walk-forward window."""

    window_id: int
    train_start: str
    train_end: str
    test_start: str
    test_end: str
    train_rows: int
    test_rows: int
    accuracy: float = 0.0
    precision: float = 0.0
    recall: float = 0.0
    f1: float = 0.0
    auc: float = 0.0
    feature_importance: dict[str, float] = field(default_factory=dict)


@dataclass
class BaselineReport:
    """Aggregate results across all walk-forward windows."""

    model_name: str
    target: str
    feature_set: str
    windows: list[ModelResult] = field(default_factory=list)

    @property
    def mean_accuracy(self) -> float:
        if not self.windows:
            return 0.0
        return float(np.nanmean([w.accuracy for w in self.windows]))

    @property
    def mean_auc(self) -> float:
        if not self.windows:
            return 0.0
        # Single-class test windows yield NaN AUC (rare targets); ignore them.
        vals = [w.auc for w in self.windows if not np.isnan(w.auc)]
        return float(np.mean(vals)) if vals else 0.0

    @property
    def mean_precision(self) -> float:
        if not self.windows:
            return 0.0
        return float(np.nanmean([w.precision for w in self.windows]))

    @property
    def mean_recall(self) -> float:
        if not self.windows:
            return 0.0
        return float(np.nanmean([w.recall for w in self.windows]))

    @property
    def mean_f1(self) -> float:
        if not self.windows:
            return 0.0
        return float(np.nanmean([w.f1 for w in self.windows]))

    @property
    def std_accuracy(self) -> float:
        if len(self.windows) < 2:
            return 0.0
        return float(np.std([w.accuracy for w in self.windows], ddof=1))

    def print_report(self) -> None:
        print(f"\n{'=' * 80}")
        print(f"MODEL: {self.model_name}")
        print(f"Target: {self.target} | Features: {self.feature_set}")
        print(f"Windows: {len(self.windows)}")
        print(f"{'=' * 80}")

        header = (
            f"{'#':>3}  {'Test Period':>25}  {'Rows':>6}  "
            f"{'Acc':>6}  {'AUC':>6}  {'Prec':>6}  {'Rec':>6}  {'F1':>6}"
        )
        print(header)
        print("-" * 80)

        for w in self.windows:
            print(
                f"{w.window_id:>3}  {w.test_start} → {w.test_end}  "
                f"{w.test_rows:>6}  {w.accuracy:>5.1f}%  {w.auc:>.4f}  "
                f"{w.precision:>5.1f}%  {w.recall:>5.1f}%  {w.f1:>.4f}"
            )

        print("-" * 80)
        print(
            f"AVG  {'':>25}  {'':>6}  {self.mean_accuracy:>5.1f}%  "
            f"{self.mean_auc:>.4f}  {self.mean_precision:>5.1f}%  "
            f"{self.mean_recall:>5.1f}%  {self.mean_f1:>.4f}"
        )
        print(f"Accuracy σ: {self.std_accuracy:.2f}%")

        if self.windows and self.windows[0].feature_importance:
            print(f"\nTop 15 Features (averaged across windows):")
            avg_imp = _average_importance(self.windows)
            for name, score in avg_imp[:15]:
                print(f"  {name:<35} {score:.4f}")

    def to_dict(self) -> dict:
        return {
            "model_name": self.model_name,
            "target": self.target,
            "feature_set": self.feature_set,
            "n_windows": len(self.windows),
            "mean_accuracy": round(self.mean_accuracy, 4),
            "mean_auc": round(self.mean_auc, 4),
            "mean_precision": round(self.mean_precision, 4),
            "mean_recall": round(self.mean_recall, 4),
            "mean_f1": round(self.mean_f1, 4),
            "std_accuracy": round(self.std_accuracy, 4),
            "windows": [
                {
                    "window_id": w.window_id,
                    "test_start": w.test_start,
                    "test_end": w.test_end,
                    "train_rows": w.train_rows,
                    "test_rows": w.test_rows,
                    "accuracy": round(w.accuracy, 4),
                    "auc": round(w.auc, 4),
                }
                for w in self.windows
            ],
            "top_features": _average_importance(self.windows)[:20],
        }


def _average_importance(
    windows: list[ModelResult],
) -> list[tuple[str, float]]:
    """Average feature importances across walk-forward windows."""
    if not windows:
        return []

    all_features: dict[str, list[float]] = {}
    for w in windows:
        for feat, score in w.feature_importance.items():
            all_features.setdefault(feat, []).append(score)

    averaged = {k: np.mean(v) for k, v in all_features.items()}
    return sorted(averaged.items(), key=lambda x: x[1], reverse=True)


def get_feature_columns(
    include_neighbors: bool = False,
    include_etf: bool = True,
    include_correlation: bool = True,
    include_market_context: bool = True,
    include_momentum: bool = False,
) -> list[str]:
    """Return the feature column list for the specified model variant.

    ``include_momentum`` is opt-in (default False) so the CSP model's feature
    set is unchanged. The Alpha BreakoutPredictor passes ``include_momentum=True``.
    """
    cols = list(FEATURE_COLS)
    if include_neighbors:
        cols.extend(NEIGHBOR_FEATURE_COLS)
    if include_etf:
        cols.extend(ETF_FEATURE_COLS)
    if include_correlation:
        cols.extend(CORRELATION_FEATURE_COLS)
    if include_market_context:
        cols.extend(MARKET_CONTEXT_COLS)
    if include_momentum:
        cols.extend(MOMENTUM_FEATURE_COLS)
        cols.extend(RS_FEATURE_COLS)
    return cols


def walk_forward_evaluate(
    dataset: pd.DataFrame,
    target: str,
    feature_cols: list[str] | None = None,
    include_neighbors: bool = False,
    train_days: int = 126,
    test_days: int = 63,
    step_days: int | None = None,
    xgb_params: dict | None = None,
    model_name: str = "xgb_baseline",
) -> BaselineReport:
    """Run walk-forward evaluation of an XGBoost model.

    Args:
        dataset: Full dataset with feature + label columns, plus ``date`` and ``ticker``.
        target: Label column name (e.g. ``csp_win_14d``, ``direction_5d``).
        feature_cols: Override feature column list.
        include_neighbors: Whether to include neighbor-aggregated features.
        train_days: Number of unique trading dates in each train window.
        test_days: Number of unique trading dates in each test window.
        step_days: Step size between windows (default: test_days).
        xgb_params: Override XGBoost hyperparameters.
        model_name: Descriptive name for the report.

    Returns:
        BaselineReport with per-window and aggregate metrics.
    """
    if not _ML_AVAILABLE:
        raise ImportError(
            "XGBoost and scikit-learn are required. "
            "Install with: pip install -e '.[ml]'"
        )

    step_days = step_days or test_days

    if feature_cols is None:
        feature_cols = get_feature_columns(include_neighbors)

    available_cols = [c for c in feature_cols if c in dataset.columns]
    missing = set(feature_cols) - set(available_cols)
    if missing:
        logger.warning("walk_forward_missing_features", missing=sorted(missing))
    feature_cols = available_cols

    is_multiclass = target.startswith("direction_")
    params = dict(xgb_params or (_MULTICLASS_XGB_PARAMS if is_multiclass else _DEFAULT_XGB_PARAMS))

    valid = dataset.dropna(subset=[target]).copy()
    if valid.empty:
        logger.error("walk_forward_no_valid_rows", target=target)
        return BaselineReport(
            model_name=model_name,
            target=target,
            feature_set="neighbor" if include_neighbors else "single",
        )

    valid["_date"] = pd.to_datetime(valid["date"]).dt.date
    all_dates = sorted(valid["_date"].unique())

    min_required = train_days + test_days
    if len(all_dates) < min_required:
        logger.error(
            "walk_forward_insufficient_dates",
            required=min_required,
            available=len(all_dates),
        )
        return BaselineReport(
            model_name=model_name,
            target=target,
            feature_set="neighbor" if include_neighbors else "single",
        )

    feature_set_name = "neighbor" if include_neighbors else "single"
    report = BaselineReport(
        model_name=model_name,
        target=target,
        feature_set=feature_set_name,
    )

    start = 0
    window_id = 0

    while start + train_days + test_days <= len(all_dates):
        train_date_list = all_dates[start : start + train_days]
        test_date_list = all_dates[start + train_days : start + train_days + test_days]

        train_mask = valid["_date"].isin(set(train_date_list))
        test_mask = valid["_date"].isin(set(test_date_list))

        train_df = valid[train_mask]
        test_df = valid[test_mask]

        if train_df.empty or test_df.empty:
            start += step_days
            window_id += 1
            continue

        X_train = train_df[feature_cols].copy()
        y_train = train_df[target].copy()
        X_test = test_df[feature_cols].copy()
        y_test = test_df[target].copy()

        if is_multiclass:
            y_train = y_train.astype(int) + 1
            y_test = y_test.astype(int) + 1

        X_train = X_train.fillna(-999)
        X_test = X_test.fillna(-999)

        t0 = time.time()
        model = xgb.XGBClassifier(**params)
        model.fit(
            X_train, y_train,
            eval_set=[(X_test, y_test)],
            verbose=False,
        )
        train_time = time.time() - t0

        y_pred = model.predict(X_test)

        acc = accuracy_score(y_test, y_pred) * 100
        prec = precision_score(y_test, y_pred, average="binary" if not is_multiclass else "macro", zero_division=0) * 100
        rec = recall_score(y_test, y_pred, average="binary" if not is_multiclass else "macro", zero_division=0) * 100
        f1 = f1_score(y_test, y_pred, average="binary" if not is_multiclass else "macro", zero_division=0)

        auc_val = 0.0
        try:
            if is_multiclass:
                y_proba = model.predict_proba(X_test)
                auc_val = roc_auc_score(y_test, y_proba, multi_class="ovr", average="macro")
            else:
                y_proba = model.predict_proba(X_test)[:, 1]
                auc_val = roc_auc_score(y_test, y_proba)
        except (ValueError, IndexError):
            pass

        importance = dict(zip(feature_cols, model.feature_importances_))

        result = ModelResult(
            window_id=window_id,
            train_start=str(train_date_list[0]),
            train_end=str(train_date_list[-1]),
            test_start=str(test_date_list[0]),
            test_end=str(test_date_list[-1]),
            train_rows=len(train_df),
            test_rows=len(test_df),
            accuracy=acc,
            precision=prec,
            recall=rec,
            f1=f1,
            auc=auc_val,
            feature_importance=importance,
        )
        report.windows.append(result)

        logger.info(
            "walk_forward_window",
            window=window_id,
            test_start=str(test_date_list[0]),
            test_end=str(test_date_list[-1]),
            train_rows=len(train_df),
            test_rows=len(test_df),
            accuracy=round(acc, 1),
            auc=round(auc_val, 4),
            train_time_s=round(train_time, 1),
        )

        start += step_days
        window_id += 1

    return report


def train_production_model(
    dataset: pd.DataFrame,
    target: str = "csp_win_5d",
    feature_cols: list[str] | None = None,
    xgb_params: dict | None = None,
    data_dir: str = "data",
) -> "xgb.XGBClassifier | None":
    """Train a final production model on the full dataset and persist it.

    Unlike walk-forward evaluation (which holds out test windows), this
    trains on ALL available data to maximise the model's knowledge before
    deployment.  Walk-forward metrics from a prior ``run_all_baselines``
    call are attached to the saved metadata.

    Returns:
        The trained XGBClassifier, or None if ML deps are missing.
    """
    if not _ML_AVAILABLE:
        raise ImportError(
            "XGBoost and scikit-learn are required. "
            "Install with: pip install -e '.[ml]'"
        )

    from tyche.ml.model_store import save_model

    if feature_cols is None:
        feature_cols = get_feature_columns(include_neighbors=False)

    available_cols = [c for c in feature_cols if c in dataset.columns]
    feature_cols = available_cols

    valid = dataset.dropna(subset=[target]).copy()
    if valid.empty:
        logger.error("train_production_no_valid_rows", target=target)
        return None

    params = dict(xgb_params or _DEFAULT_XGB_PARAMS)

    X = valid[feature_cols].fillna(-999)
    y = valid[target]

    logger.info(
        "train_production_start",
        target=target,
        features=len(feature_cols),
        rows=len(X),
    )

    model = xgb.XGBClassifier(**params)
    model.fit(X, y, verbose=False)

    save_model(
        model,
        target=target,
        feature_cols=feature_cols,
        data_dir=data_dir,
        train_rows=len(X),
        xgb_params=params,
    )

    logger.info(
        "train_production_complete",
        target=target,
        rows=len(X),
    )
    return model


def run_all_baselines(
    dataset: pd.DataFrame,
    targets: list[str] | None = None,
    train_days: int = 126,
    test_days: int = 63,
    output_dir: str | Path | None = None,
) -> list[BaselineReport]:
    """Run baseline evaluation for all target/feature-set combinations.

    Args:
        dataset: Full assembled dataset.
        targets: Label columns to evaluate. Defaults to core targets.
        train_days: Walk-forward train window.
        test_days: Walk-forward test window.
        output_dir: Optional directory to save reports.

    Returns:
        List of BaselineReport objects.
    """
    if targets is None:
        targets = [
            "csp_win_5d",
            "csp_win_14d",
            "direction_5d",
            "direction_10d",
            "pullback_recovery_5d",
        ]

    available_targets = [t for t in targets if t in dataset.columns]
    if not available_targets:
        logger.error("no_valid_targets", requested=targets, columns=list(dataset.columns))
        return []

    reports: list[BaselineReport] = []

    for target in available_targets:
        for include_neighbors in [False, True]:
            variant = "neighbor" if include_neighbors else "single"
            model_name = f"xgb_{variant}_{target}"

            logger.info(
                "baseline_starting",
                model=model_name,
                target=target,
                feature_set=variant,
            )

            report = walk_forward_evaluate(
                dataset=dataset,
                target=target,
                include_neighbors=include_neighbors,
                train_days=train_days,
                test_days=test_days,
                model_name=model_name,
            )
            report.print_report()
            reports.append(report)

    if output_dir:
        _save_reports(reports, output_dir)

    return reports


ALPHA_TARGETS: list[str] = [
    "big_move_up_25pct_40d",
    "big_move_up_40pct_60d",
    "big_move_up_60pct_120d",
]


def run_alpha_baselines(
    dataset: pd.DataFrame,
    targets: list[str] | None = None,
    train_days: int = 252,
    test_days: int = 63,
    output_dir: str | Path | None = None,
) -> list[BaselineReport]:
    """Walk-forward evaluation for the directional big-move targets.

    Runs each target twice — without momentum features (baseline) and with
    them (momentum) — so the incremental lift of the Alpha feature group can
    be measured directly. Longer default train window (252d ~ 1yr) because the
    big-move horizons are longer than the CSP/pullback targets.

    Returns:
        List of BaselineReport objects (baseline + momentum per target).
    """
    if targets is None:
        targets = ALPHA_TARGETS

    available_targets = [t for t in targets if t in dataset.columns]
    if not available_targets:
        logger.error(
            "no_valid_alpha_targets",
            requested=targets,
            columns=[c for c in dataset.columns if c.startswith("big_move")],
        )
        return []

    base_cols = get_feature_columns(include_momentum=False)
    momentum_cols = get_feature_columns(include_momentum=True)

    reports: list[BaselineReport] = []

    for target in available_targets:
        for use_momentum in [False, True]:
            variant = "momentum" if use_momentum else "baseline"
            feature_cols = momentum_cols if use_momentum else base_cols
            model_name = f"alpha_{variant}_{target}"

            logger.info(
                "alpha_baseline_starting",
                model=model_name,
                target=target,
                feature_set=variant,
            )

            report = walk_forward_evaluate(
                dataset=dataset,
                target=target,
                feature_cols=feature_cols,
                train_days=train_days,
                test_days=test_days,
                model_name=model_name,
            )
            report.feature_set = variant
            report.print_report()
            reports.append(report)

    if output_dir:
        _save_reports(reports, output_dir)

    return reports


def _save_reports(
    reports: list[BaselineReport],
    output_dir: str | Path,
) -> None:
    """Persist all reports to JSON."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    summary = {
        "n_reports": len(reports),
        "reports": [r.to_dict() for r in reports],
    }
    path = out / "baseline_results.json"
    path.write_text(json.dumps(summary, indent=2, default=str))
    logger.info("baseline_results_saved", path=str(path))

    comparison = []
    for r in reports:
        comparison.append({
            "model": r.model_name,
            "target": r.target,
            "features": r.feature_set,
            "accuracy": round(r.mean_accuracy, 2),
            "auc": round(r.mean_auc, 4),
            "precision": round(r.mean_precision, 2),
            "recall": round(r.mean_recall, 2),
            "f1": round(r.mean_f1, 4),
            "std_accuracy": round(r.std_accuracy, 2),
        })

    if comparison:
        comp_df = pd.DataFrame(comparison)
        comp_path = out / "baseline_comparison.csv"
        comp_df.to_csv(comp_path, index=False)

        print(f"\n{'=' * 90}")
        print("BASELINE COMPARISON SUMMARY")
        print(f"{'=' * 90}")
        print(comp_df.to_string(index=False))
