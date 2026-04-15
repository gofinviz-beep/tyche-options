"""Pullback and oversold alert detection — surfaces EMA pullbacks and deep dips.

Consumes ConvictionSignal objects produced by the engine and generates
PullbackAlert dataclasses when a stock is pulling back to an EMA in a
confirmed uptrend, or has dipped significantly below EMAs (oversold).
These alerts drive stock buy recommendations, covered call strategies,
and email notifications.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Literal

import structlog

from tyche.conviction.dip_classifier import DipCatalystClassifier, DipClassification
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
    """An actionable pullback or oversold alert for a single ticker."""

    ticker: str
    alert_type: Literal["pullback_8ema", "pullback_21ema", "oversold_21ema", "oversold_50ema"]
    severity: Literal["info", "high"]
    trend_state: TrendState
    conviction_level: str
    last_close: float
    ema_8: float
    ema_21: float
    ema_8_slope: float
    ema_21_slope: float
    ema_50: float
    ema_50_slope: float
    rsi_14: float
    volume_declining: bool
    institutional_pct: float | None
    institutional_label: str
    suggested_action: str
    position_size_hint: Literal["standard", "large"]
    stop_loss_level: float
    prior_streak: int = 0
    iv_rank: float | None = None
    iv_percentile: float | None = None
    atm_iv: float | None = None
    vrp: float | None = None
    conviction_score: float = 0.0
    dip_classification: DipClassification | None = None
    detected_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "alert_type": self.alert_type,
            "severity": self.severity,
            "trend_state": self.trend_state.value,
            "conviction_level": self.conviction_level,
            "conviction_score": round(self.conviction_score, 3),
            "last_close": round(self.last_close, 2),
            "ema_8": round(self.ema_8, 4),
            "ema_21": round(self.ema_21, 4),
            "ema_8_slope": round(self.ema_8_slope, 6),
            "ema_21_slope": round(self.ema_21_slope, 6),
            "ema_50": round(self.ema_50, 4),
            "ema_50_slope": round(self.ema_50_slope, 6),
            "rsi_14": round(self.rsi_14, 2),
            "iv_rank": round(self.iv_rank, 1) if self.iv_rank is not None else None,
            "iv_percentile": round(self.iv_percentile, 1) if self.iv_percentile is not None else None,
            "atm_iv": round(self.atm_iv, 4) if self.atm_iv is not None else None,
            "vrp": round(self.vrp, 4) if self.vrp is not None else None,
            "volume_declining": self.volume_declining,
            "institutional_pct": round(self.institutional_pct, 4) if self.institutional_pct is not None else None,
            "institutional_label": self.institutional_label,
            "suggested_action": self.suggested_action,
            "position_size_hint": self.position_size_hint,
            "prior_streak": self.prior_streak,
            "stop_loss_level": round(self.stop_loss_level, 2),
            "dip_classification": self.dip_classification.to_dict() if self.dip_classification else None,
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
    dip_classifier: DipCatalystClassifier | None = None,
    news_signals: dict[str, dict] | None = None,
    filing_signals: dict[str, dict] | None = None,
) -> list[PullbackAlert]:
    """Detect pullback and oversold alerts from conviction signals.

    Args:
        signals: ConvictionSignal objects keyed by ticker (dict) or as a list.
        institutional_map: Ticker -> institutional ownership (0-1 scale).
        min_institutional_pct: Minimum institutional % for stock buy alerts.
        dip_classifier: Optional DipCatalystClassifier for oversold entries.
            When provided, oversold alerts are classified and non-actionable
            dips (high/extreme risk) are filtered out.
        news_signals: Ticker -> news signal dict (from news_signals table).
        filing_signals: Ticker -> filing signal dict (from filing_signals table).

    Returns:
        List of PullbackAlert objects, sorted by severity (high first).
    """
    institutional_map = institutional_map or {}
    news_signals = news_signals or {}
    filing_signals = filing_signals or {}
    alerts: list[PullbackAlert] = []

    signal_list: list[ConvictionSignal]
    if isinstance(signals, dict):
        signal_list = list(signals.values())
    else:
        signal_list = signals

    for sig in signal_list:
        is_pullback = sig.trend_state in (
            TrendState.PULLBACK_TO_8EMA,
            TrendState.PULLBACK_TO_21EMA,
        )
        is_oversold = sig.trend_state in (
            TrendState.OVERSOLD_21EMA,
            TrendState.OVERSOLD_50EMA,
        )

        if not is_pullback and not is_oversold:
            continue

        if is_pullback and (sig.ema_8_slope <= 0 or sig.ema_21_slope <= 0):
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
        is_oversold_50 = sig.trend_state == TrendState.OVERSOLD_50EMA
        is_oversold_21 = sig.trend_state == TrendState.OVERSOLD_21EMA

        if is_oversold_50:
            severity: Literal["info", "high"] = "high"
            position_size_hint: Literal["standard", "large"] = "large"
            suggested_action = (
                "Deep dip below 50-EMA — oversold recovery candidate. "
                "Quality large-cap with prior uptrend. "
                "Consider buying + covered call strategy."
            )
        elif is_oversold_21:
            severity = "high" if sig.prior_streak >= 10 else "info"
            position_size_hint = "large" if sig.prior_streak >= 10 else "standard"
            suggested_action = (
                "Dip below 21-EMA — potential recovery entry. "
                "Verify news catalyst is not fundamental. "
                "Consider buying + covered call strategy."
            )
        elif is_21ema and sig.volume_declining_on_pullback:
            severity = "high"
            position_size_hint = "large"
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

        alert_type: Literal["pullback_8ema", "pullback_21ema", "oversold_21ema", "oversold_50ema"]
        if is_oversold_50:
            alert_type = "oversold_50ema"
        elif is_oversold_21:
            alert_type = "oversold_21ema"
        elif is_21ema:
            alert_type = "pullback_21ema"
        else:
            alert_type = "pullback_8ema"

        dip_class: DipClassification | None = None
        if is_oversold and dip_classifier is not None:
            dip_pct = abs(getattr(sig, "price_to_50ema_pct", 0.0)) if is_oversold_50 else abs(sig.price_to_21ema_pct)
            dip_class = dip_classifier.classify(
                sig.ticker,
                dip_pct=dip_pct,
                prior_streak=sig.prior_streak,
                rsi=sig.rsi_14,
                news_signal=news_signals.get(sig.ticker),
                filing_signal=filing_signals.get(sig.ticker),
            )
            if not dip_class.actionable:
                logger.info(
                    "oversold_alert_blocked_by_classifier",
                    ticker=sig.ticker,
                    risk_level=dip_class.risk_level.value,
                    catalyst=dip_class.catalyst.value,
                )
                continue

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
            ema_50=sig.ema_50,
            ema_50_slope=sig.ema_50_slope,
            rsi_14=sig.rsi_14,
            iv_rank=sig.iv_rank,
            iv_percentile=sig.iv_percentile,
            atm_iv=sig.atm_iv,
            vrp=sig.vrp,
            prior_streak=sig.prior_streak,
            conviction_score=getattr(sig, "conviction_score", 0.0),
            volume_declining=sig.volume_declining_on_pullback,
            institutional_pct=inst_pct,
            institutional_label=_institutional_label(inst_pct),
            suggested_action=suggested_action,
            position_size_hint=position_size_hint,
            stop_loss_level=_compute_stop_loss(alert_type, sig.ema_21),
            dip_classification=dip_class,
        ))

    alerts.sort(key=lambda a: (0 if a.severity == "high" else 1, a.ticker))

    logger.info(
        "pullback_alerts_detected",
        total=len(alerts),
        high=sum(1 for a in alerts if a.severity == "high"),
        info=sum(1 for a in alerts if a.severity == "info"),
    )
    return alerts
