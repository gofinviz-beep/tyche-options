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

# Anti-chase: the most a fully over-extended (parabolic/overbought) name can be
# penalised. 0.55 means a maximally stretched setup keeps only 55% of its raw
# composite — enough to demote already-run names below earlier-stage demand
# without zeroing them out. Applied multiplicatively: composite *= (1 - (1-floor)*overext).
_OVEREXTENSION_FLOOR = 0.55

# Regime sub-models. The router sends each ticker to one of two scorers:
#   - "revenue": established revenue business — demand quality read off
#     fundamentals + estimate revisions (D-FUND / D-EST dominate).
#   - "narrative": pre-revenue / thematic name — fundamentals are sparse, so
#     demand is read off catalysts, policy tailwinds, squeeze pressure, and
#     early relative strength (D-CAT / D-POL / D-TECH dominate).
# Each regime maps its net signed demand evidence (-1..1) to a multiplier on the
# ML/factor composite via: mult = 1 + _DEMAND_SENSITIVITY * net, clamped to
# [_DEMAND_MULT_FLOOR, _DEMAND_MULT_CEIL]. With no demand data the net is 0 and
# the multiplier is exactly 1.0 (v1-identical), so the split is backward-compatible.
REGIME_REVENUE = "revenue"
REGIME_NARRATIVE = "narrative"
_DEMAND_SENSITIVITY = 0.30
_DEMAND_MULT_FLOOR = 0.70
_DEMAND_MULT_CEIL = 1.30
# Router thresholds: a name is a revenue business when fundamentals are present
# (recent filing coverage) and it carries positive trailing revenue growth data.
_REVENUE_MAX_QUARTERS_STALE = 6.0


@dataclass
class DemandDimensions:
    """Per-dimension demand sub-scores surfaced for the UI / debugging.

    ``fund`` / ``est`` are 0-1 demand-quality reads; ``catalyst`` / ``policy``
    are signed (-1..1) so a headwind shows negative; ``squeeze`` is 0-1.
    ``net`` is the regime-weighted signed evidence actually applied.
    """

    fund: float | None = None
    est: float | None = None
    catalyst: float | None = None
    policy: float | None = None
    squeeze: float | None = None
    net: float = 0.0


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
    # Anti-chase: 0 (early/fresh) .. 1 (parabolic/over-extended). The penalty is
    # the multiplier actually applied to the composite (1.0 = none).
    overextension_score: float | None = None
    overextension_penalty: float | None = None
    # Regime router output + per-dimension demand breakdown.
    regime: str = REGIME_NARRATIVE
    demand: DemandDimensions = field(default_factory=DemandDimensions)
    demand_multiplier: float | None = None
    as_of_date: date | None = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["factors"] = {k: round(v, 3) for k, v in asdict(self.factors).items()}
        d["demand"] = {
            k: (round(v, 4) if v is not None else None)
            for k, v in asdict(self.demand).items()
        }
        d["alpha_score"] = round(self.alpha_score, 1)
        for k in ("breakout_prob_swing", "breakout_prob_trend", "breakout_prob_thematic"):
            if d[k] is not None:
                d[k] = round(d[k], 4)
        for k in ("return_63d", "return_126d", "return_252d", "rs_126d",
                  "pct_off_52w_high", "volume_thrust_ratio",
                  "overextension_score", "overextension_penalty",
                  "demand_multiplier"):
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

            # Anti-chase: demote parabolic / over-extended setups so the score
            # ranks true demand over "already ran". Penalty multiplier in
            # [_OVEREXTENSION_FLOOR, 1].
            overext = self._overextension(row)
            penalty = 1.0 - (1.0 - _OVEREXTENSION_FLOOR) * overext
            composite *= penalty

            # Regime router + demand sub-model: scale the composite by the
            # regime-appropriate net demand evidence. Defaults to 1.0 when no
            # demand data is present (v1-identical).
            regime = self._classify_regime(row)
            dims = self._demand_dimensions(row, regime)
            demand_mult = max(
                _DEMAND_MULT_FLOOR,
                min(_DEMAND_MULT_CEIL, 1.0 + _DEMAND_SENSITIVITY * dims.net),
            )
            composite = min(1.0, composite * demand_mult)

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
                overextension_score=round(overext, 4),
                overextension_penalty=round(penalty, 4),
                regime=regime,
                demand=dims,
                demand_multiplier=round(demand_mult, 4),
                as_of_date=as_of if isinstance(as_of, date) else None,
            ))

        return signals

    @staticmethod
    def _classify_regime(row: pd.Series) -> str:
        """Route a ticker to the revenue or narrative demand sub-model.

        Revenue business: recent fundamentals coverage (a filing within
        ``_REVENUE_MAX_QUARTERS_STALE`` quarters) AND a non-null trailing
        revenue-growth read. Everything else (sparse fundamentals, pre-revenue,
        narrative names) is scored on catalysts/policy/technicals.
        """
        q_stale = row.get("f_quarters_since_filing")
        rev_growth = row.get("f_rev_growth_yoy")
        has_cov = (
            q_stale is not None
            and not (isinstance(q_stale, float) and np.isnan(q_stale))
            and float(q_stale) <= _REVENUE_MAX_QUARTERS_STALE
        )
        has_rev = rev_growth is not None and not (
            isinstance(rev_growth, float) and np.isnan(rev_growth)
        )
        return REGIME_REVENUE if (has_cov and has_rev) else REGIME_NARRATIVE

    def _demand_dimensions(self, row: pd.Series, regime: str) -> DemandDimensions:
        """Per-dimension demand reads + the regime-weighted net evidence (-1..1)."""
        fund = self._fund_quality(row)
        est = self._est_quality(row)
        catalyst = _opt(row.get("cat_demand_score"))
        policy = _opt(row.get("cat_policy_score"))
        squeeze = self._squeeze_pressure(row)
        # D-GRAPH: upstream-customer demand cascade (0..1, present only when the
        # ticker has customers in the supply-chain graph). Signed for fusion.
        graph_prop = _opt(row.get("graph_demand_propagation"))
        graph_signed = _signed_val(graph_prop) if graph_prop not in (None, 0.0) else None

        if regime == REGIME_REVENUE:
            # Fundamentals + estimates drive; catalyst/policy/graph confirm.
            signed = [
                _signed(fund, weight=1.0),
                _signed(est, weight=1.0),
                (catalyst, 0.5),
                (policy, 0.4),
                (graph_signed, 0.4),
            ]
        else:
            # Narrative: catalyst/policy/graph/squeeze/early-RS drive.
            rs_early = _signed_ramp(row.get("rs_63d"), -0.10, 0.20)
            signed = [
                (catalyst, 1.0),
                (policy, 0.7),
                (graph_signed, 0.7),
                (_signed_val(squeeze), 0.4),
                (rs_early, 0.4),
                _signed(fund, weight=0.3),
            ]
        net = _weighted_mean_present(signed)
        return DemandDimensions(
            fund=fund,
            est=est,
            catalyst=catalyst,
            policy=policy,
            squeeze=squeeze,
            net=round(net, 4),
        )

    @staticmethod
    def _fund_quality(row: pd.Series) -> float | None:
        """0-1 fundamental demand quality (None when no coverage)."""
        yoy = row.get("f_rev_growth_yoy")
        if yoy is None or (isinstance(yoy, float) and np.isnan(yoy)):
            return None
        rev = _ramp(yoy, -0.10, 0.40)
        accel = 1.0 if (row.get("f_rev_accel") or 0) > 0 else 0.0
        gm_trend = _ramp(row.get("f_gross_margin_trend"), -0.02, 0.04)
        eps = _ramp(row.get("f_eps_growth_yoy"), -0.10, 0.40)
        fcf = 1.0 if (row.get("f_fcf_positive") or 0) > 0 else 0.0
        return _clip01(0.40 * rev + 0.20 * accel + 0.15 * gm_trend + 0.15 * eps + 0.10 * fcf)

    @staticmethod
    def _est_quality(row: pd.Series) -> float | None:
        """0-1 estimate-momentum quality (None when no analyst coverage)."""
        rev90 = row.get("e_eps_revision_90d")
        rec = row.get("e_rec_score")
        pt = row.get("e_price_target_upside")
        if all(
            v is None or (isinstance(v, float) and np.isnan(v))
            for v in (rev90, rec, pt)
        ):
            return None
        eps_rev = _ramp(rev90, -0.05, 0.10)
        rev_rev = _ramp(row.get("e_rev_revision_90d"), -0.05, 0.10)
        rec_q = _clip01(((rec or 0.0) + 1.0) / 2.0)  # -1..1 -> 0..1
        surprise = _ramp(row.get("e_eps_surprise_avg4"), -0.05, 0.10)
        pt_up = _ramp(pt, 0.0, 0.40)
        return _clip01(
            0.30 * eps_rev + 0.20 * rev_rev + 0.25 * rec_q + 0.10 * surprise + 0.15 * pt_up
        )

    @staticmethod
    def _squeeze_pressure(row: pd.Series) -> float | None:
        """0-1 short-squeeze potential from days-to-cover / SI ratio (None if absent)."""
        dtc = row.get("si_days_to_cover")
        ratio = row.get("si_ratio")
        if (dtc is None or (isinstance(dtc, float) and np.isnan(dtc))) and (
            ratio is None or (isinstance(ratio, float) and np.isnan(ratio))
        ):
            return None
        return _clip01(max(_ramp(dtc, 3.0, 10.0), _ramp(ratio, 3.0, 10.0)))

    @staticmethod
    def _overextension(row: pd.Series) -> float:
        """Return a 0..1 over-extension score for the row.

        Prefers the precomputed ``overextension_score`` feature; otherwise
        derives it from RSI, the recent 21d run, and distance above the
        200-EMA so the penalty still works on older feature rows.
        """
        pre = row.get("overextension_score")
        if pre is not None and not (isinstance(pre, float) and np.isnan(pre)):
            return _clip01(float(pre))

        rsi_ob = _ramp(row.get("rsi_14"), 70.0, 100.0)
        parabolic = _ramp(row.get("parabolic_21d"), 0.0, 0.50)
        dist_200 = _ramp(row.get("price_to_200ema_pct"), 0.0, 100.0)
        return _clip01(0.5 * parabolic + 0.3 * rsi_ob + 0.2 * dist_200)

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


def _signed_ramp(value: float | None, lo: float, hi: float) -> float | None:
    """Signed ramp in [-1, 1]: <=lo -> -1, midpoint -> 0, >=hi -> 1."""
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return None
    if hi == lo:
        return None
    return max(-1.0, min(1.0, 2.0 * (float(value) - lo) / (hi - lo) - 1.0))


def _signed_val(quality: float | None) -> float | None:
    """Map a 0-1 quality read to signed [-1, 1] (0.5 -> 0)."""
    if quality is None:
        return None
    return max(-1.0, min(1.0, 2.0 * float(quality) - 1.0))


def _signed(quality: float | None, weight: float) -> tuple[float | None, float]:
    """Helper: pair a signed-mapped 0-1 quality read with its weight."""
    return _signed_val(quality), weight


def _weighted_mean_present(
    pairs: list[tuple[float | None, float]],
) -> float:
    """Weighted mean of the present (non-None) signed signals; 0 when none."""
    num = 0.0
    den = 0.0
    for val, w in pairs:
        if val is None:
            continue
        num += float(val) * w
        den += w
    if den <= 0:
        return 0.0
    return max(-1.0, min(1.0, num / den))


def _market_cap_from_row(row: pd.Series) -> float | None:
    """Recover raw market cap (USD) from the log_market_cap feature."""
    raw = row.get("market_cap")
    if raw is not None and not (isinstance(raw, float) and np.isnan(raw)):
        return float(raw)
    log_mc = row.get("log_market_cap")
    if log_mc is None or (isinstance(log_mc, float) and np.isnan(log_mc)):
        return None
    return float(np.expm1(log_mc))
