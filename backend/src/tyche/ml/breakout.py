"""Live inference for the directional Alpha engine.

``BreakoutPredictor`` loads the persisted XGBoost big-move models (one per
horizon, e.g. ``big_move_up_25pct_40d``) and produces P(large upside move)
probabilities for the universe during the nightly alpha batch.

It consumes a feature DataFrame already assembled by the AlphaScoreEngine
(the same ``extract_ticker_features`` + augmentations used at training time),
so train/serve feature parity is guaranteed — there is no hand-rolled feature
bridge to drift out of sync.

Graceful degradation: when no model artifacts exist the predictor reports
``is_available = False`` and returns empty results; the deterministic factor
score still works, so the Alpha page degrades to a rules-only ranking.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import structlog

from tyche.ml.model_store import ModelMeta, load_model
from tyche.ml.xgb_baseline import ALPHA_TARGETS, add_missingness_indicators

logger = structlog.get_logger()

_SENTINEL = -999


class BreakoutPredictor:
    """Bridges the alpha pipeline to trained XGBoost big-move models."""

    def __init__(self, data_dir: str = "data", targets: list[str] | None = None) -> None:
        self._models: dict[str, object] = {}
        self._metas: dict[str, ModelMeta] = {}

        for target in targets or ALPHA_TARGETS:
            result = load_model(target, data_dir=data_dir)
            if result is not None:
                model, meta = result
                self._models[target] = model
                self._metas[target] = meta

    @property
    def is_available(self) -> bool:
        return bool(self._models)

    @property
    def targets(self) -> list[str]:
        return list(self._models.keys())

    @property
    def model_info(self) -> dict[str, object] | None:
        if not self._metas:
            return None
        return {
            target: {
                "trained_at": meta.trained_at,
                "train_rows": meta.train_rows,
                "mean_auc": meta.mean_auc,
                "features": len(meta.feature_cols),
            }
            for target, meta in self._metas.items()
        }

    def predict_proba_batch(
        self, features: pd.DataFrame,
    ) -> dict[str, np.ndarray]:
        """Return P(big move) per target for each row of *features*.

        Args:
            features: DataFrame with one row per ticker, containing (at least)
                the feature columns each model was trained on. Missing columns
                are filled with the XGBoost sentinel.

        Returns:
            Mapping of target name -> probability array aligned with ``features``
            row order. Empty dict if no models are loaded or input is empty.
        """
        if not self._models or features.empty:
            return {}

        results: dict[str, np.ndarray] = {}
        for target, model in self._models.items():
            meta = self._metas[target]
            X = features.copy()
            for col in meta.feature_cols:
                if col not in X.columns:
                    if col.endswith("__isna"):
                        X[col] = 1
                    else:
                        X[col] = _SENTINEL
            indicator_bases = {
                c[: -len("__isna")]
                for c in meta.feature_cols
                if c.endswith("__isna")
            }
            if indicator_bases:
                X = add_missingness_indicators(X, sorted(indicator_bases))
            X = X[meta.feature_cols].fillna(_SENTINEL)
            try:
                results[target] = model.predict_proba(X)[:, 1]
            except Exception:
                logger.warning("breakout_predict_failed", target=target, exc_info=True)
                results[target] = np.full(len(features), np.nan)

        return results
