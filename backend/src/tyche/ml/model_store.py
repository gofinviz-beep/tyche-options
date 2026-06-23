"""Persist and load trained XGBoost model artifacts.

Stores each model as a pair of files under ``data/ml/models/``:
- ``{target}.json``  — XGBoost native JSON (portable, human-readable)
- ``{target}_meta.json`` — ordered feature columns, training stats, timestamp

Designed for graceful degradation: ``load_model`` returns ``None`` when
no artifact exists so the inference path can skip ML scoring silently.
"""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    from tyche.storage.paths import StorageContext

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


def _model_rel_paths(target: str) -> tuple[str, str]:
    return (
        f"ml/models/{target}.json",
        f"ml/models/{target}_meta.json",
    )


@contextmanager
def _local_model_path(path: str | Path):
    """Yield a local filesystem path for ``xgboost.load_model``."""
    from tyche.storage.paths import is_gcs_path, get_gcs_filesystem

    path_str = str(path)
    if not is_gcs_path(path_str):
        yield Path(path_str)
        return

    import tempfile

    fs = get_gcs_filesystem()
    with tempfile.NamedTemporaryFile(suffix=".json", delete=False) as tmp:
        tmp_path = Path(tmp.name)
    try:
        fs.get(path_str, str(tmp_path))
        yield tmp_path
    finally:
        if tmp_path.exists():
            tmp_path.unlink()


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
    *,
    ctx: StorageContext | None = None,
) -> tuple["xgb.XGBClassifier", ModelMeta] | None:
    """Load a persisted XGBoost model and its metadata.

    When *ctx* is omitted, resolves artifacts under ``data_dir`` on the local
    filesystem. Pass a :class:`StorageContext` with ``backend='gcs'`` so Cloud
    Run jobs can load models from ``gs://`` without a prior sync step.

    Returns:
        (model, meta) tuple, or ``None`` if no artifact exists or
        XGBoost is not installed.
    """
    if not _XGB_AVAILABLE:
        logger.debug("ml_model_skip_load", reason="xgboost_not_installed")
        return None

    from tyche.storage import exists as storage_exists
    from tyche.storage.json_io import read_json
    from tyche.storage.paths import StorageContext, coerce_storage_path

    if ctx is None:
        ctx = StorageContext(backend="local", local_root=Path(data_dir))

    model_rel, meta_rel = _model_rel_paths(target)
    if not storage_exists(model_rel, ctx=ctx) or not storage_exists(meta_rel, ctx=ctx):
        logger.info(
            "ml_model_not_found",
            target=target,
            backend=ctx.backend,
            path=model_rel,
        )
        return None

    try:
        meta_raw = read_json(meta_rel, ctx=ctx)
        meta = ModelMeta(**{k: v for k, v in meta_raw.items() if k != "schema_version"})
        meta.schema_version = meta_raw.get("schema_version", 1)

        model = xgb.XGBClassifier()
        with _local_model_path(coerce_storage_path(model_rel, ctx=ctx)) as local_path:
            model.load_model(str(local_path))

        logger.info(
            "ml_model_loaded",
            target=target,
            trained_at=meta.trained_at,
            features=len(meta.feature_cols),
            auc=meta.mean_auc,
            backend=ctx.backend,
        )
        return model, meta

    except Exception:
        logger.exception("ml_model_load_failed", target=target)
        return None
