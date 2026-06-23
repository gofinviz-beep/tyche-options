"""Live inference bridge: FeatureSignal → XGBoost → csp_safety_prob.

``CSPSafetyPredictor`` loads a persisted XGBoost model once and produces
a P(CSP expires worthless) probability for each ticker during the
conviction scan.  It reuses fields already on ``FeatureSignal`` and
computes the handful of missing features from the raw OHLCV tail.

Designed for graceful degradation: when no model artifact exists, the
predictor reports ``is_available = False`` and ``predict()`` returns
``None``; the conviction pipeline proceeds unchanged.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd
import structlog

from tyche.conviction.features import FeatureSignal, TrendState
from tyche.ml.model_store import ModelMeta, load_model

logger = structlog.get_logger()

_TREND_STATE_ORD: dict[str, int] = {
    TrendState.DOWNTREND.value: 0,
    TrendState.INSUFFICIENT_DATA.value: 0,
    TrendState.CONSOLIDATION.value: 1,
    TrendState.UPTREND.value: 2,
    TrendState.PULLBACK_TO_8EMA.value: 3,
    TrendState.PULLBACK_TO_21EMA.value: 4,
    TrendState.STRONG_UPTREND.value: 5,
}

_SENTINEL = -999


class CSPSafetyPredictor:
    """Bridges the conviction pipeline to a trained XGBoost model."""

    def __init__(self, data_dir: str = "data", *, ctx: object | None = None) -> None:
        self._model = None
        self._meta: ModelMeta | None = None
        self._feature_cols: list[str] = []

        result = load_model("csp_win_5d", data_dir=data_dir, ctx=ctx)
        if result is not None:
            self._model, self._meta = result
            self._feature_cols = list(self._meta.feature_cols)

    @property
    def is_available(self) -> bool:
        return self._model is not None

    @property
    def model_info(self) -> dict[str, Any] | None:
        if self._meta is None:
            return None
        return {
            "target": self._meta.target,
            "trained_at": self._meta.trained_at,
            "train_rows": self._meta.train_rows,
            "mean_auc": self._meta.mean_auc,
            "mean_accuracy": self._meta.mean_accuracy,
            "features": len(self._meta.feature_cols),
        }

    def predict(
        self,
        signal: FeatureSignal,
        ohlcv_df: pd.DataFrame,
        *,
        derived_row: dict[str, Any] | None = None,
        market_cap: float | None = None,
        institutional_pct: float | None = None,
        sector_encoded: int = 0,
    ) -> float | None:
        """Produce P(CSP expires worthless) for a single ticker.

        Args:
            signal: FeatureSignal from ConvictionFeatureEngine.
            ohlcv_df: Full OHLCV DataFrame for the ticker (used to derive
                returns, volatility, volume ratio from the tail).
            derived_row: Optional dict with ``rv_20d`` from DerivedMetricsStore.
            market_cap: Static market cap from TickerMetaStore.
            institutional_pct: Static institutional ownership pct.
            sector_encoded: Integer sector encoding.

        Returns:
            Probability in [0, 1], or None if model is unavailable.
        """
        if self._model is None:
            return None

        try:
            features = self._bridge_features(
                signal, ohlcv_df,
                derived_row=derived_row,
                market_cap=market_cap,
                institutional_pct=institutional_pct,
                sector_encoded=sector_encoded,
            )
            row = pd.DataFrame([features])[self._feature_cols].fillna(_SENTINEL)
            proba = self._model.predict_proba(row)[:, 1][0]
            return float(np.clip(proba, 0.0, 1.0))

        except Exception:
            logger.warning(
                "csp_safety_predict_failed",
                ticker=signal.ticker,
                exc_info=True,
            )
            return None

    def predict_batch(
        self,
        items: list[
            tuple[FeatureSignal, pd.DataFrame, dict[str, Any] | None, dict[str, Any] | None]
        ],
    ) -> dict[str, float | None]:
        """Vectorised batch prediction for efficiency during conviction scans.

        Each item is (signal, ohlcv_df, derived_row, meta_dict) where
        meta_dict has ``market_cap``, ``institutional_pct``, ``sector_encoded``.
        """
        if self._model is None:
            return {}

        results: dict[str, float | None] = {}
        rows: list[dict[str, Any]] = []
        tickers: list[str] = []

        for signal, ohlcv_df, derived_row, meta in items:
            try:
                features = self._bridge_features(
                    signal, ohlcv_df,
                    derived_row=derived_row,
                    market_cap=(meta or {}).get("market_cap"),
                    institutional_pct=(meta or {}).get("institutional_pct"),
                    sector_encoded=(meta or {}).get("sector_encoded", 0),
                )
                rows.append(features)
                tickers.append(signal.ticker)
            except Exception:
                logger.warning("csp_safety_bridge_failed", ticker=signal.ticker, exc_info=True)
                results[signal.ticker] = None

        if not rows:
            return results

        X = pd.DataFrame(rows)
        for col in self._feature_cols:
            if col not in X.columns:
                X[col] = _SENTINEL
        X = X[self._feature_cols].fillna(_SENTINEL)

        try:
            probas = self._model.predict_proba(X)[:, 1]
            for ticker, p in zip(tickers, probas):
                results[ticker] = float(np.clip(p, 0.0, 1.0))
        except Exception:
            logger.warning("csp_safety_batch_predict_failed", exc_info=True)
            for ticker in tickers:
                results[ticker] = None

        return results

    def _bridge_features(
        self,
        signal: FeatureSignal,
        ohlcv_df: pd.DataFrame,
        *,
        derived_row: dict[str, Any] | None = None,
        market_cap: float | None = None,
        institutional_pct: float | None = None,
        sector_encoded: int = 0,
    ) -> dict[str, Any]:
        """Map FeatureSignal + OHLCV tail → ML feature dict."""
        close = ohlcv_df["close"].astype(float) if not ohlcv_df.empty else pd.Series(dtype=float)
        volume = ohlcv_df["volume"].astype(float) if not ohlcv_df.empty else pd.Series(dtype=float)

        price_to_50ema_pct = (
            (signal.last_close - signal.ema_50) / signal.ema_50 * 100
            if signal.ema_50 > 0 else np.nan
        )

        avg_vol_20 = volume.tail(20).mean() if len(volume) >= 20 else np.nan
        volume_ratio = (
            float(signal.latest_volume / avg_vol_20)
            if avg_vol_20 and avg_vol_20 > 0 else np.nan
        )

        volume_declining = 0
        if len(close) >= 15:
            recent_avg = volume.tail(5).mean()
            prior_avg = volume.iloc[-15:-5].mean()
            if recent_avg < prior_avg and signal.last_close < signal.ema_8:
                volume_declining = 1

        return_1d = float(close.pct_change(1).iloc[-1]) if len(close) >= 2 else np.nan
        return_5d = float(close.pct_change(5).iloc[-1]) if len(close) >= 6 else np.nan
        return_10d = float(close.pct_change(10).iloc[-1]) if len(close) >= 11 else np.nan
        return_20d = float(close.pct_change(20).iloc[-1]) if len(close) >= 21 else np.nan

        if len(close) >= 21:
            log_rets = np.log(close / close.shift(1)).dropna()
            volatility_20d = float(log_rets.tail(20).std() * np.sqrt(252))
        else:
            volatility_20d = np.nan

        trend_state_ord = _TREND_STATE_ORD.get(signal.trend_state.value, 0)

        rv_20d = (derived_row or {}).get("rv_20d", np.nan)

        log_market_cap = np.log1p(market_cap) if market_cap and market_cap > 0 else np.nan

        return {
            "ema_8": signal.ema_8,
            "ema_21": signal.ema_21,
            "ema_50": signal.ema_50,
            "price_to_8ema_pct": signal.price_to_8ema_pct,
            "price_to_21ema_pct": signal.price_to_21ema_pct,
            "price_to_50ema_pct": price_to_50ema_pct,
            "ema_8_slope": signal.ema_8_slope,
            "ema_21_slope": signal.ema_21_slope,
            "ema_50_slope": signal.ema_50_slope,
            "rsi_14": signal.rsi_14,
            "days_above_both_emas": signal.days_above_both_emas,
            "prior_streak": signal.prior_streak,
            "trend_state_ord": trend_state_ord,
            "volume_ratio": volume_ratio,
            "volume_declining": volume_declining,
            "return_1d": return_1d,
            "return_5d": return_5d,
            "return_10d": return_10d,
            "return_20d": return_20d,
            "volatility_20d": volatility_20d,
            "iv_rank": signal.iv_rank,
            "iv_percentile": signal.iv_percentile,
            "atm_iv": signal.atm_iv,
            "vrp": signal.vrp,
            "rv_20d": rv_20d,
            "log_market_cap": log_market_cap,
            "institutional_pct": institutional_pct if institutional_pct else np.nan,
            "sector_encoded": sector_encoded,
        }
