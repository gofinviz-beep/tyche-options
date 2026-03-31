"""Pullback alert detection — surfaces 8/21 EMA pullbacks as actionable alerts.

Consumes ConvictionSignal objects produced by the engine and generates
PullbackAlert dataclasses when a stock is pulling back to an EMA in a
confirmed uptrend. These alerts drive stock buy recommendations and
email notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import structlog

from tyche.conviction.engine import ConvictionSignal, TrendState

logger = structlog.get_logger()

INSTITUTIONAL_LABELS: list[tuple[float, str]] = [
    (0.70, "Strong institutional backing"),
    (0.50, "Adequate institutional backing"),
    (0.40, "Moderate institutional backing — caution"),
]


def _institutional_label(pct: float | None) -> str:
    if pct is None:
        return "Unknown"
    for threshold, label in INSTITUTIONAL_LABELS:
        if pct >= threshold:
            return label
    return "Low institutional backing"


@dataclass
class PullbackAlert:
    """An actionable pullback alert for a single ticker."""

    ticker: str
    alert_type: Literal["pullback_8ema", "pullback_21ema"]
    severity: Literal["info", "high"]
    trend_state: TrendState
    conviction_level: str
    last_close: float
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    volume_declining: bool
    institutional_pct: float | None
    institutional_label: str
    suggested_action: str
    position_size_hint: Literal["standard", "large"]
    stop_loss_level: float
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "trend_state": self.trend_state.value,
            "conviction_level": self.conviction_level,
            "last_close": round(self.last_close, 2),
            "ema_8": round(self.ema_8, 4),
            "ema_21": round(self.ema_21, 4),
            "ema_8_slope": round(self.ema_8_slope, 6),
            "ema_21_slope": round(self.ema_21_slope, 6),
            "volume_declining": self.volume_declining,
            "institutional_pct": round(self.institutional_pct, 4) if self.institutional_pct is not None else None,
            "institutional_label": self.institutional_label,
            "suggested_action": self.suggested_action,
            "position_size_hint": self.position_size_hint,
            "stop_loss_level": round(self.stop_loss_level, 2),
            "detected_at": self.detected_at.isoformat(),
        }


def _compute_stop_loss(alert_type: str, ema_21: float) -> float:
    """Compute stop-loss level based on pullback type.

    8-EMA entries: stop below the 21-EMA (next support level).
    21-EMA entries: stop 2% below the 21-EMA (decisive break).
    """
    if alert_type == "pullback_8ema":
        return round(ema_21 * 0.99, 2)
    return round(ema_21 * 0.98, 2)


def detect_pullback_alerts(
    signals: dict[str, ConvictionSignal] | list[ConvictionSignal],
    institutional_map: dict[str, float] | None = None,
    min_institutional_pct: float = 0.50,
) -> list[PullbackAlert]:
    """Detect pullback alerts from conviction signals.

    Args:
        signals: ConvictionSignal objects keyed by ticker (dict) or as a list.
        institutional_map: Ticker -> institutional ownership (0-1 scale).
        min_institutional_pct: Minimum institutional % for stock buy alerts.

    Returns:
        List of PullbackAlert objects, sorted by severity (high first).
    """
    institutional_map = institutional_map or {}
    alerts: list[PullbackAlert] = []

    signal_list: list[ConvictionSignal]
    if isinstance(signals, dict):
        signal_list = list(signals.values())
    else:
        signal_list = signals

    for sig in signal_list:
        if sig.trend_state not in (
            TrendState.PULLBACK_TO_8EMA,
            TrendState.PULLBACK_TO_21EMA,
        ):
            continue

        if sig.ema_8_slope <= 0 or sig.ema_21_slope <= 0:
            logger.debug(
                "pullback_skipped_negative_slope",
                ticker=sig.ticker,
                ema_8_slope=sig.ema_8_slope,
                ema_21_slope=sig.ema_21_slope,
            )
            continue

        inst_pct = institutional_map.get(sig.ticker)
        if inst_pct is not None and inst_pct < min_institutional_pct:
            logger.debug(
                "pullback_skipped_low_institutional",
                ticker=sig.ticker,
                pct=inst_pct,
                min_pct=min_institutional_pct,
            )
            continue

        is_21ema = sig.trend_state == TrendState.PULLBACK_TO_21EMA

        if is_21ema and sig.volume_declining_on_pullback:
            severity: Literal["info", "high"] = "high"
            position_size_hint: Literal["standard", "large"] = "large"
            suggested_action = (
                "High-conviction entry zone — institutional defense at 21-EMA "
                "with declining volume. Consider larger position."
            )
        elif is_21ema:
            severity = "high"
            position_size_hint = "large"
            suggested_action = (
                "Pullback to 21-EMA — institutional defense zone. "
                "Volume not yet declining; watch for confirmation."
            )
        else:
            severity = "info"
            position_size_hint = "standard"
            if sig.volume_declining_on_pullback:
                suggested_action = (
                    "Pullback to 8-EMA with declining volume — "
                    "consider standard position entry."
                )
            else:
                suggested_action = (
                    "Pullback to 8-EMA — lighter entry, "
                    "wait for volume confirmation if cautious."
                )

        alert_type: Literal["pullback_8ema", "pullback_21ema"] = (
            "pullback_21ema" if is_21ema else "pullback_8ema"
        )

        alerts.append(PullbackAlert(
            ticker=sig.ticker,
            alert_type=alert_type,
            severity=severity,
            trend_state=sig.trend_state,
            conviction_level=sig.raw_conviction,
            last_close=sig.last_close,
            ema_8=sig.ema_8,
            ema_21=sig.ema_21,
            ema_8_slope=sig.ema_8_slope,
            ema_21_slope=sig.ema_21_slope,
            volume_declining=sig.volume_declining_on_pullback,
            institutional_pct=inst_pct,
            institutional_label=_institutional_label(inst_pct),
            suggested_action=suggested_action,
            position_size_hint=position_size_hint,
            stop_loss_level=_compute_stop_loss(alert_type, sig.ema_21),
        ))

    alerts.sort(key=lambda a: (0 if a.severity == "high" else 1, a.ticker))

    logger.info(
        "pullback_alerts_detected",
        total=len(alerts),
        high=sum(1 for a in alerts if a.severity == "high"),
        info=sum(1 for a in alerts if a.severity == "info"),
    )
    return alerts
