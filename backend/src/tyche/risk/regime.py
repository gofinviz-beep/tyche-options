"""Lightweight market regime detector and risk-scaling policy.

Classifies the broad market into one of three regimes based on a
reference index's trend and volatility:

* ``risk_on``  — uptrend + low/normal volatility.
* ``neutral``  — mixed signals or transition.
* ``risk_off`` — downtrend or elevated volatility.

Each regime maps to a scaling dict that adjusts risk parameters:

* ``max_positions_scale``        — multiplier on max open positions.
* ``concentration_cap_scale``    — multiplier on per-ticker concentration cap.
* ``min_conviction_override``    — minimum conviction level for inclusion.

The detector is intentionally *lightweight*: it uses only the reference
index's OHLCV data (e.g. SPY/QQQ) to compute a 20-day realised vol
and compare the 8/21 EMA alignment.  No external API calls required.

Regime detection can be fully disabled via ``TYCHE_REGIME_SCALING_ENABLED=false``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import structlog

logger = structlog.get_logger()


class RegimeState:
    RISK_ON = "risk_on"
    NEUTRAL = "neutral"
    RISK_OFF = "risk_off"


@dataclass(frozen=True)
class RegimeScaling:
    """Per-regime risk parameter adjustments."""

    max_positions_scale: float = 1.0
    concentration_cap_scale: float = 1.0
    min_conviction: str = "medium"


_DEFAULT_SCALING: dict[str, RegimeScaling] = {
    RegimeState.RISK_ON: RegimeScaling(
        max_positions_scale=1.0,
        concentration_cap_scale=1.0,
        min_conviction="medium",
    ),
    RegimeState.NEUTRAL: RegimeScaling(
        max_positions_scale=0.75,
        concentration_cap_scale=0.8,
        min_conviction="medium",
    ),
    RegimeState.RISK_OFF: RegimeScaling(
        max_positions_scale=0.5,
        concentration_cap_scale=0.6,
        min_conviction="high",
    ),
}


@dataclass
class RegimeResult:
    """Output of regime detection."""

    state: str
    scaling: RegimeScaling
    trend_signal: str
    realised_vol: float
    vol_percentile: str
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "trend_signal": self.trend_signal,
            "realised_vol": round(self.realised_vol, 4),
            "vol_percentile": self.vol_percentile,
            "scaling": {
                "max_positions_scale": self.scaling.max_positions_scale,
                "concentration_cap_scale": self.scaling.concentration_cap_scale,
                "min_conviction": self.scaling.min_conviction,
            },
            **self.details,
        }


class RegimeDetector:
    """Detects market regime from reference index OHLCV data.

    Detection logic:
    1. Compute 8-EMA and 21-EMA of close prices.
    2. If close > both EMAs and 8-EMA > 21-EMA → uptrend.
       If close < both EMAs and 8-EMA < 21-EMA → downtrend.
       Otherwise → mixed.
    3. Compute 20-day realised volatility (annualised).
    4. Combine: uptrend + vol < threshold → risk_on,
       downtrend or vol > high_threshold → risk_off,
       else → neutral.
    """

    def __init__(
        self,
        vol_normal_threshold: float = 0.20,
        vol_high_threshold: float = 0.30,
        scaling_map: dict[str, RegimeScaling] | None = None,
    ) -> None:
        self._vol_normal = vol_normal_threshold
        self._vol_high = vol_high_threshold
        self._scaling = scaling_map or dict(_DEFAULT_SCALING)

    def detect(self, ohlcv: pd.DataFrame) -> RegimeResult:
        """Classify regime from index OHLCV data.

        Args:
            ohlcv: DataFrame with at least 'close' column and >= 30 rows.

        Returns:
            RegimeResult with state, scaling, and diagnostic details.
        """
        if ohlcv is None or len(ohlcv) < 30:
            return self._insufficient_data()

        close = ohlcv["close"].astype(float)
        ema_8 = close.ewm(span=8, adjust=False).mean()
        ema_21 = close.ewm(span=21, adjust=False).mean()

        last_close = float(close.iloc[-1])
        last_ema8 = float(ema_8.iloc[-1])
        last_ema21 = float(ema_21.iloc[-1])

        above_both = last_close > last_ema8 and last_close > last_ema21
        below_both = last_close < last_ema8 and last_close < last_ema21
        ema_aligned_up = last_ema8 > last_ema21
        ema_aligned_down = last_ema8 < last_ema21

        if above_both and ema_aligned_up:
            trend_signal = "uptrend"
        elif below_both and ema_aligned_down:
            trend_signal = "downtrend"
        else:
            trend_signal = "mixed"

        vol = self._realised_vol(close)

        if vol > self._vol_high:
            vol_label = "high"
        elif vol > self._vol_normal:
            vol_label = "elevated"
        else:
            vol_label = "normal"

        if trend_signal == "uptrend" and vol_label == "normal":
            state = RegimeState.RISK_ON
        elif trend_signal == "downtrend" or vol_label == "high":
            state = RegimeState.RISK_OFF
        else:
            state = RegimeState.NEUTRAL

        scaling = self._scaling.get(state, RegimeScaling())

        result = RegimeResult(
            state=state,
            scaling=scaling,
            trend_signal=trend_signal,
            realised_vol=vol,
            vol_percentile=vol_label,
            details={
                "last_close": round(last_close, 2),
                "ema_8": round(last_ema8, 2),
                "ema_21": round(last_ema21, 2),
                "vol_normal_threshold": self._vol_normal,
                "vol_high_threshold": self._vol_high,
            },
        )

        logger.info(
            "regime_detected",
            state=state,
            trend=trend_signal,
            vol=round(vol, 4),
            vol_label=vol_label,
        )
        return result

    def _realised_vol(self, close: pd.Series, window: int = 20) -> float:
        """Annualised close-to-close realised volatility."""
        if len(close) < window + 1:
            return 0.25
        log_returns = np.log(close / close.shift(1)).dropna()
        recent = log_returns.iloc[-window:]
        if len(recent) < 2:
            return 0.25
        return float(recent.std()) * math.sqrt(252)

    def _insufficient_data(self) -> RegimeResult:
        return RegimeResult(
            state=RegimeState.NEUTRAL,
            scaling=self._scaling.get(RegimeState.NEUTRAL, RegimeScaling()),
            trend_signal="insufficient_data",
            realised_vol=0.0,
            vol_percentile="unknown",
            details={"reason": "Not enough data for regime detection"},
        )


def apply_regime_scaling(
    regime: RegimeResult,
    max_positions: int,
    concentration_cap_pct: float,
) -> tuple[int, float]:
    """Apply regime scaling to risk parameters.

    Args:
        regime: Current regime result.
        max_positions: Base max positions from config.
        concentration_cap_pct: Base concentration cap from config.

    Returns:
        (scaled_max_positions, scaled_concentration_cap_pct)
    """
    scaled_pos = max(1, int(max_positions * regime.scaling.max_positions_scale))
    scaled_conc = concentration_cap_pct * regime.scaling.concentration_cap_scale
    return scaled_pos, round(scaled_conc, 2)


def filter_by_min_conviction(
    candidates: list[Any],
    min_conviction: str,
    signals: dict[str, Any],
) -> list[Any]:
    """Remove candidates below the regime's minimum conviction level.

    Only filters when min_conviction is 'high' (regime risk_off).
    When 'medium' or lower, all candidates pass (legacy behaviour).
    """
    if min_conviction != "high":
        return candidates

    result = []
    for c in candidates:
        sig = signals.get(c.symbol)
        if sig is None:
            continue
        level = getattr(sig, "conviction_level", "none")
        if level == "high":
            result.append(c)

    filtered = len(candidates) - len(result)
    if filtered > 0:
        logger.info(
            "regime_conviction_filter",
            min_conviction=min_conviction,
            before=len(candidates),
            after=len(result),
            filtered=filtered,
        )
    return result
