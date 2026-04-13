"""Persist and load trained XGBoost model artifacts.

Stores each model as a pair of files under ``data/ml/models/``:
- ``{target}.json``  — XGBoost native JSON (portable, human-readable)
- ``{target}_meta.json`` — ordered feature columns, training stats, timestamp

Designed for graceful degradation: ``load_model`` returns ``None`` when
no artifact exists so the inference path can skip ML scoring silently.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import structlog

logger = structlog.get_logger()

try:
    import xgboost as xgb

    _XGB_AVAILABLE = True
except ImportError:
    _XGB_AVAILABLE = False


@dataclass
class ModelMeta:
    """Metadata sidecar for a persisted XGBoost model."""

    target: str
    feature_cols: list[str]
    trained_at: str = ""
    train_rows: int = 0
    mean_auc: float = 0.0
    mean_accuracy: float = 0.0
    xgb_params: dict = field(default_factory=dict)
    schema_version: int = 1


def _models_dir(data_dir: str) -> Path:
    return Path(data_dir) / "ml" / "models"


def save_model(
    model: "xgb.XGBClassifier",
    target: str,
    feature_cols: list[str],
    *,
    data_dir: str = "data",
    train_rows: int = 0,
    mean_auc: float = 0.0,
    mean_accuracy: float = 0.0,
    xgb_params: dict | None = None,
) -> Path:
    """Persist an XGBoost classifier and its metadata sidecar.

    Returns:
        Path to the saved model JSON file.
    """
    out = _models_dir(data_dir)
    out.mkdir(parents=True, exist_ok=True)

    model_path = out / f"{target}.json"
    model.save_model(str(model_path))

    meta = ModelMeta(
        target=target,
        feature_cols=list(feature_cols),
        trained_at=datetime.now(timezone.utc).isoformat(),
        train_rows=train_rows,
        mean_auc=round(mean_auc, 4),
        mean_accuracy=round(mean_accuracy, 4),
        xgb_params=xgb_params or {},
    )
    meta_path = out / f"{target}_meta.json"
    meta_path.write_text(json.dumps(asdict(meta), indent=2))

    logger.info(
        "ml_model_saved",
        target=target,
        path=str(model_path),
        features=len(feature_cols),
        train_rows=train_rows,
        auc=round(mean_auc, 4),
    )
    return model_path


def load_model(
    target: str,
    data_dir: str = "data",
) -> tuple["xgb.XGBClassifier", ModelMeta] | None:
    """Load a persisted XGBoost model and its metadata.

    Returns:
        (model, meta) tuple, or ``None`` if no artifact exists or
        XGBoost is not installed.
    """
    if not _XGB_AVAILABLE:
        logger.debug("ml_model_skip_load", reason="xgboost_not_installed")
        return None

    out = _models_dir(data_dir)
    model_path = out / f"{target}.json"
    meta_path = out / f"{target}_meta.json"

    if not model_path.exists() or not meta_path.exists():
        logger.info("ml_model_not_found", target=target, path=str(model_path))
        return None

    try:
        meta_raw = json.loads(meta_path.read_text())
        meta = ModelMeta(**{k: v for k, v in meta_raw.items() if k != "schema_version"})
        meta.schema_version = meta_raw.get("schema_version", 1)

        model = xgb.XGBClassifier()
        model.load_model(str(model_path))

        logger.info(
            "ml_model_loaded",
            target=target,
            trained_at=meta.trained_at,
            features=len(meta.feature_cols),
            auc=meta.mean_auc,
        )
        return model, meta

    except Exception:
        logger.exception("ml_model_load_failed", target=target)
        return None
