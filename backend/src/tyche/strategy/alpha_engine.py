"""Directional Alpha scoring engine.

Combines deterministic technical factor sub-scores (momentum, relative
strength, trend quality, breakout, volume thrust) with the ML breakout
probabilities from ``BreakoutPredictor`` into a single 0-100 AlphaScore,
classifies each setup's horizon (swing / trend / thematic), and emits a
directional signal (strong_buy / buy / watch / avoid).

Design notes:
- Scoring operates on a feature DataFrame already produced by
  ``ml.features.extract_ticker_features`` (+ relative-strength augmentation),
  so it shares the exact feature definitions used to train the models.
- Fully degrades to a rules-only score when no ML model is available.
- Pure and stateless: ``score_from_features`` has no I/O, so it is trivially
  testable and reusable by both the nightly batch and on-demand scans.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()

# Horizon -> the big-move model target whose probability best represents it.
HORIZON_TARGETS: dict[str, str] = {
    "swing": "big_move_up_25pct_40d",
    "trend": "big_move_up_40pct_60d",
    "thematic": "big_move_up_60pct_120d",
}

# Composite weighting between the ML probability and the deterministic factor
# blend. When ML is unavailable the factor blend takes the full weight.
_ML_WEIGHT = 0.55
_FACTOR_WEIGHT = 0.45


@dataclass
class AlphaFactors:
    """Deterministic 0-1 technical factor sub-scores."""

    momentum: float = 0.0
    relative_strength: float = 0.0
    trend_quality: float = 0.0
    breakout: float = 0.0
    volume_thrust: float = 0.0

    def blended(self) -> float:
        """Weighted blend of factor sub-scores (0-1)."""
        return (
            0.28 * self.momentum
            + 0.24 * self.relative_strength
            + 0.24 * self.trend_quality
            + 0.16 * self.breakout
            + 0.08 * self.volume_thrust
        )


@dataclass
class AlphaSignal:
    """Directional alpha assessment for a single ticker."""

    ticker: str
    alpha_score: float = 0.0
    signal: str = "avoid"
    horizon: str = "none"
    factors: AlphaFactors = field(default_factory=AlphaFactors)

    breakout_prob_swing: float | None = None
    breakout_prob_trend: float | None = None
    breakout_prob_thematic: float | None = None

    last_close: float = 0.0
    return_63d: float | None = None
    return_126d: float | None = None
    return_252d: float | None = None
    rs_126d: float | None = None
    pct_off_52w_high: float | None = None
    ema_stack_score: int = 0
    volume_thrust_ratio: float | None = None
    market_cap: float | None = None
    institutional_pct: float | None = None
    as_of_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["factors"] = {k: round(v, 3) for k, v in asdict(self.factors).items()}
        d["alpha_score"] = round(self.alpha_score, 1)
        for k in ("breakout_prob_swing", "breakout_prob_trend", "breakout_prob_thematic"):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        for k in ("return_63d", "return_126d", "return_252d", "rs_126d",
                  "pct_off_52w_high", "volume_thrust_ratio"):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        if d["institutional_pct"] is not None:
            d["institutional_pct"] = round(d["institutional_pct"], 4)
        d["last_close"] = round(self.last_close, 2)
        d["as_of_date"] = self.as_of_date.isoformat() if self.as_of_date else None
        return d


def _clip01(x: float) -> float:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0.0
    return float(min(1.0, max(0.0, x)))


def _ramp(value: float | None, lo: float, hi: float) -> float:
    """Linear ramp: <=lo -> 0, >=hi -> 1."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return 0.0
    if hi == lo:
        return 0.0
    return _clip01((float(value) - lo) / (hi - lo))


class AlphaScoreEngine:
    """Computes AlphaSignal objects from a feature DataFrame + ML probabilities."""

    def __init__(
        self,
        strong_buy_threshold: float = 72.0,
        buy_threshold: float = 58.0,
        watch_threshold: float = 44.0,
    ) -> None:
        self._strong_buy = strong_buy_threshold
        self._buy = buy_threshold
        self._watch = watch_threshold

    def score_from_features(
        self,
        features: pd.DataFrame,
        breakout_probs: dict[str, np.ndarray] | None = None,
    ) -> list[AlphaSignal]:
        """Score every row of *features* into an AlphaSignal.

        Args:
            features: One row per ticker. Must include a ``ticker`` column and
                the momentum/RS feature columns. ``date`` optional.
            breakout_probs: Optional mapping of model target -> probability
                array aligned with ``features`` row order.

        Returns:
            AlphaSignal list in the same order as ``features`` rows.
        """
        if features.empty:
            return []

        probs = breakout_probs or {}
        n = len(features)

        def col(name: str) -> pd.Series:
            return features[name] if name in features.columns else pd.Series([np.nan] * n)

        signals: list[AlphaSignal] = []
        feats = features.reset_index(drop=True)

        for i in range(n):
            row = feats.iloc[i]
            factors = self._compute_factors(row)

            p_swing = self._prob_at(probs, HORIZON_TARGETS["swing"], i)
            p_trend = self._prob_at(probs, HORIZON_TARGETS["trend"], i)
            p_thematic = self._prob_at(probs, HORIZON_TARGETS["thematic"], i)

            ml_blend = self._ml_blend(p_swing, p_trend, p_thematic)
            factor_blend = factors.blended()

            if ml_blend is not None:
                composite = _ML_WEIGHT * ml_blend + _FACTOR_WEIGHT * factor_blend
            else:
                composite = factor_blend

            alpha_score = round(100.0 * composite, 1)
            horizon = self._classify_horizon(
                row, p_swing, p_trend, p_thematic, factor_blend,
            )
            signal = self._classify_signal(alpha_score)

            as_of = row.get("date") if "date" in feats.columns else None
            if isinstance(as_of, str):
                try:
                    as_of = date.fromisoformat(as_of[:10])
                except ValueError:
                    as_of = None
            elif hasattr(as_of, "date"):
                as_of = as_of.date()

            signals.append(AlphaSignal(
                ticker=str(row.get("ticker", "")),
                alpha_score=alpha_score,
                signal=signal,
                horizon=horizon,
                factors=factors,
                breakout_prob_swing=p_swing,
                breakout_prob_trend=p_trend,
                breakout_prob_thematic=p_thematic,
                last_close=float(row.get("close", row.get("last_close", 0.0)) or 0.0),
                return_63d=_opt(row.get("return_63d")),
                return_126d=_opt(row.get("return_126d")),
                return_252d=_opt(row.get("return_252d")),
                rs_126d=_opt(row.get("rs_126d")),
                pct_off_52w_high=_opt(row.get("pct_off_52w_high")),
                ema_stack_score=int(row.get("ema_stack_score", 0) or 0),
                volume_thrust_ratio=_opt(row.get("volume_thrust_ratio")),
                market_cap=_market_cap_from_row(row),
                institutional_pct=_opt(row.get("institutional_pct")),
                as_of_date=as_of if isinstance(as_of, date) else None,
            ))

        return signals

    def _compute_factors(self, row: pd.Series) -> AlphaFactors:
        # Momentum: blend of 3/6/12-month trailing returns.
        mom = (
            0.45 * _ramp(row.get("return_126d"), 0.0, 0.50)
            + 0.35 * _ramp(row.get("return_252d"), 0.0, 0.80)
            + 0.20 * _ramp(row.get("return_63d"), 0.0, 0.25)
        )

        # Relative strength: excess return vs SPY (3/6mo).
        rs = (
            0.6 * _ramp(row.get("rs_126d"), 0.0, 0.30)
            + 0.4 * _ramp(row.get("rs_63d"), 0.0, 0.15)
        )

        # Trend quality: EMA stack alignment + acceleration + above 200-EMA.
        stack = float(row.get("ema_stack_score", 0) or 0) / 3.0
        accel = 1.0 if (row.get("slope_accel") or 0) > 0 else 0.0
        above_200 = 1.0 if (row.get("price_to_200ema_pct") or -1) > 0 else 0.0
        trend = 0.5 * stack + 0.25 * accel + 0.25 * above_200

        # Breakout: new-high proximity + recent breakout flags.
        near_high = _ramp(row.get("pct_off_52w_high"), -25.0, 0.0)
        bo = max(
            float(row.get("breakout_20d", 0) or 0),
            float(row.get("breakout_63d", 0) or 0),
        )
        breakout = 0.6 * near_high + 0.4 * bo

        # Volume thrust: recent volume vs 50-day average.
        vt = _ramp(row.get("volume_thrust_ratio"), 1.0, 2.0)

        return AlphaFactors(
            momentum=round(_clip01(mom), 4),
            relative_strength=round(_clip01(rs), 4),
            trend_quality=round(_clip01(trend), 4),
            breakout=round(_clip01(breakout), 4),
            volume_thrust=round(_clip01(vt), 4),
        )

    @staticmethod
    def _prob_at(
        probs: dict[str, np.ndarray], target: str, i: int,
    ) -> float | None:
        arr = probs.get(target)
        if arr is None or i >= len(arr):
            return None
        val = arr[i]
        if val is None or (isinstance(val, float) and np.isnan(val)):
            return None
        return float(val)

    @staticmethod
    def _ml_blend(
        p_swing: float | None,
        p_trend: float | None,
        p_thematic: float | None,
    ) -> float | None:
        vals = [p for p in (p_swing, p_trend, p_thematic) if p is not None]
        if not vals:
            return None
        # Reward the best opportunity across horizons, but keep the average as
        # a stabiliser so a single noisy model can't dominate.
        return 0.6 * max(vals) + 0.4 * (sum(vals) / len(vals))

    def _classify_horizon(
        self,
        row: pd.Series,
        p_swing: float | None,
        p_trend: float | None,
        p_thematic: float | None,
        factor_blend: float,
    ) -> str:
        # Prefer ML: pick the horizon whose model is most confident.
        ml = {"swing": p_swing, "trend": p_trend, "thematic": p_thematic}
        ml_present = {k: v for k, v in ml.items() if v is not None}
        if ml_present and factor_blend > 0.0:
            best = max(ml_present, key=lambda k: ml_present[k])
            if ml_present[best] >= 0.15:
                return best

        # Fallback to trailing-return profile.
        r63 = row.get("return_63d") or 0.0
        r126 = row.get("return_126d") or 0.0
        r252 = row.get("return_252d") or 0.0
        scores = {"swing": r63 / 0.25, "trend": r126 / 0.50, "thematic": r252 / 0.80}
        best = max(scores, key=lambda k: scores[k])
        return best if scores[best] > 0.4 else "none"

    def _classify_signal(self, alpha_score: float) -> str:
        if alpha_score >= self._strong_buy:
            return "strong_buy"
        if alpha_score >= self._buy:
            return "buy"
        if alpha_score >= self._watch:
            return "watch"
        return "avoid"


def _opt(val: Any) -> float | None:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return None
    return float(val)


def _market_cap_from_row(row: pd.Series) -> float | None:
    """Recover raw market cap (USD) from the log_market_cap feature."""
    raw = row.get("market_cap")
    if raw is not None and not (isinstance(raw, float) and np.isnan(raw)):
        return float(raw)
    log_mc = row.get("log_market_cap")
    if log_mc is None or (isinstance(log_mc, float) and np.isnan(log_mc)):
        return None
    return float(np.expm1(log_mc))
