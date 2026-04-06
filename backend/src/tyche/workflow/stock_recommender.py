"""Stock buy recommendation engine — converts pullback alerts to actionable recs.

Layered on top of the PullbackAlertDetector, this module enriches alerts with
CSP cross-references (active CSPs on the same ticker), position sizing hints,
and risk/reward context to help the user decide between direct stock buying
and CSP-based exposure.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

import structlog

from tyche.conviction.alerts import PullbackAlert, _compute_stop_loss, _institutional_label
from tyche.conviction.engine import ConvictionSignal
from tyche.models.conviction import ConvictionSnapshot
from tyche.risk.overlap import OverlapDecision, OverlapPolicy, OverlapResult

logger = structlog.get_logger()


@dataclass
class StockBuyRecommendation:
    """An actionable stock buy recommendation derived from a pullback alert."""

    ticker: str
    entry_type: Literal["pullback_8ema", "pullback_21ema"]
    entry_price: float
    target_ema_value: float
    stop_loss: float
    conviction: str
    institutional_pct: float | None
    institutional_label: str
    volume_confirmation: bool
    position_size_hint: Literal["standard", "large"]
    days_above_emas: int
    ema_8_slope: float
    ema_21_slope: float
    ema_50_slope: float
    rsi_14: float
    related_csp_strike: float | None
    has_active_csp: bool
    recommendation: str
    risk_reward_note: str
    overlap_decision: OverlapDecision = "add_standard"
    overlap_reason: str = ""
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "entry_type": self.entry_type,
            "entry_price": round(self.entry_price, 2),
            "target_ema_value": round(self.target_ema_value, 4),
            "stop_loss": round(self.stop_loss, 2),
            "conviction": self.conviction,
            "institutional_pct": (
                round(self.institutional_pct, 4)
                if self.institutional_pct is not None
                else None
            ),
            "institutional_label": self.institutional_label,
            "volume_confirmation": self.volume_confirmation,
            "position_size_hint": self.position_size_hint,
            "days_above_emas": self.days_above_emas,
            "ema_8_slope": round(self.ema_8_slope, 6),
            "ema_21_slope": round(self.ema_21_slope, 6),
            "ema_50_slope": round(self.ema_50_slope, 6),
            "rsi_14": round(self.rsi_14, 2),
            "related_csp_strike": (
                round(self.related_csp_strike, 2)
                if self.related_csp_strike is not None
                else None
            ),
            "has_active_csp": self.has_active_csp,
            "recommendation": self.recommendation,
            "risk_reward_note": self.risk_reward_note,
            "overlap_decision": self.overlap_decision,
            "overlap_reason": self.overlap_reason,
            "created_at": self.created_at.isoformat(),
        }


def _find_active_csp(
    ticker: str,
    positions: list[dict[str, Any]] | None,
) -> tuple[bool, float | None]:
    """Check if there's an active short put (CSP) on this ticker.

    Args:
        ticker: The stock ticker.
        positions: List of position dicts with symbol, option_type, strike fields.

    Returns:
        (has_active_csp, nearest_strike_or_None)
    """
    if not positions:
        return False, None

    csp_strikes: list[float] = []
    for pos in positions:
        sym = pos.get("symbol", "")
        opt_type = pos.get("option_type", "")
        strike = pos.get("strike")
        if sym.upper() == ticker.upper() and opt_type in ("put", "short_put") and strike:
            csp_strikes.append(float(strike))

    if not csp_strikes:
        return False, None

    return True, min(csp_strikes)


def _build_recommendation_text(
    alert: PullbackAlert,
    has_active_csp: bool,
    csp_strike: float | None,
) -> str:
    """Generate the human-readable recommendation string."""
    if has_active_csp and csp_strike is not None:
        return (
            f"Active CSP at ${csp_strike:.2f} on {alert.ticker}. "
            f"If assigned, you'll own shares near the pullback level. "
            f"No additional stock buy needed unless adding to position."
        )

    if alert.alert_type == "pullback_21ema":
        return (
            f"Buy {alert.ticker} near ${alert.last_close:.2f} — "
            f"21-EMA institutional defense zone. "
            f"Larger position recommended. Stop below ${alert.stop_loss_level:.2f}."
        )

    return (
        f"Buy {alert.ticker} near ${alert.last_close:.2f} — "
        f"8-EMA pullback entry. Standard position size. "
        f"Stop below ${alert.stop_loss_level:.2f}."
    )


def _build_risk_reward_note(
    alert: PullbackAlert,
    has_active_csp: bool,
) -> str:
    """Generate risk/reward context note."""
    parts: list[str] = []

    risk_pct = abs(alert.last_close - alert.stop_loss_level) / alert.last_close * 100
    parts.append(f"Risk to stop: {risk_pct:.1f}%")

    if alert.volume_declining:
        parts.append("Volume declining on pullback (bullish)")
    else:
        parts.append("Volume not declining — watch for confirmation")

    if has_active_csp:
        parts.append("CSP already provides downside exposure via assignment")

    if alert.alert_type == "pullback_21ema":
        parts.append("21-EMA = institutional defense line, higher conviction")

    return ". ".join(parts) + "."


def generate_stock_recommendations(
    alerts: list[PullbackAlert],
    conviction_signals: dict[str, ConvictionSignal] | None = None,
    positions: list[dict[str, Any]] | None = None,
    overlap_policy: OverlapPolicy | None = None,
    portfolio_value: float = 100_000.0,
) -> list[StockBuyRecommendation]:
    """Convert pullback alerts into stock buy recommendations.

    Args:
        alerts: PullbackAlert objects from the detector.
        conviction_signals: Full conviction data for additional context.
        positions: Current broker positions for CSP cross-reference.
        overlap_policy: Optional policy for CSP-vs-stock overlap decisions.
            When None, overlap fields default to ``add_standard`` (legacy).
        portfolio_value: Total portfolio value for overlap exposure calc.

    Returns:
        List of StockBuyRecommendation objects.
    """
    conviction_signals = conviction_signals or {}
    recs: list[StockBuyRecommendation] = []

    for alert in alerts:
        has_csp, csp_strike = _find_active_csp(alert.ticker, positions)
        sig = conviction_signals.get(alert.ticker)

        target_ema = alert.ema_21 if alert.alert_type == "pullback_21ema" else alert.ema_8

        overlap_decision: OverlapDecision = "add_standard"
        overlap_reason = ""
        if overlap_policy is not None:
            ov = overlap_policy.evaluate(
                ticker=alert.ticker,
                entry_price=alert.last_close,
                conviction=alert.conviction_level,
                positions=positions,
                portfolio_value=portfolio_value,
            )
            overlap_decision = ov.decision
            overlap_reason = ov.reason

        recs.append(StockBuyRecommendation(
            ticker=alert.ticker,
            entry_type=alert.alert_type,
            entry_price=alert.last_close,
            target_ema_value=target_ema,
            stop_loss=alert.stop_loss_level,
            conviction=alert.conviction_level,
            institutional_pct=alert.institutional_pct,
            institutional_label=alert.institutional_label,
            volume_confirmation=alert.volume_declining,
            position_size_hint=alert.position_size_hint,
            days_above_emas=sig.days_above_both_emas if sig else 0,
            ema_8_slope=alert.ema_8_slope,
            ema_21_slope=alert.ema_21_slope,
            ema_50_slope=alert.ema_50_slope,
            rsi_14=alert.rsi_14,
            related_csp_strike=csp_strike,
            has_active_csp=has_csp,
            recommendation=_build_recommendation_text(alert, has_csp, csp_strike),
            risk_reward_note=_build_risk_reward_note(alert, has_csp),
            overlap_decision=overlap_decision,
            overlap_reason=overlap_reason,
        ))

    logger.info(
        "stock_recommendations_generated",
        total=len(recs),
        with_active_csp=sum(1 for r in recs if r.has_active_csp),
        pullback_21ema=sum(1 for r in recs if r.entry_type == "pullback_21ema"),
        deferred=sum(1 for r in recs if r.overlap_decision == "defer"),
    )
    return recs


def generate_recommendations_from_snapshots(
    snapshots: list[ConvictionSnapshot],
    institutional_map: dict[str, float] | None = None,
    positions: list[dict[str, Any]] | None = None,
) -> list[StockBuyRecommendation]:
    """Build stock buy recommendations directly from DB snapshots.

    Unlike `generate_stock_recommendations` (which requires PullbackAlert objects
    from a live engine run), this reads all data from persisted ConvictionSnapshot
    rows — no engine re-run needed.

    Args:
        snapshots: ConvictionSnapshot objects (already filtered to pullback states).
        institutional_map: ticker → institutional ownership fraction.
        positions: Current broker positions for CSP cross-reference.

    Returns:
        List of StockBuyRecommendation objects.
    """
    institutional_map = institutional_map or {}
    recs: list[StockBuyRecommendation] = []

    for snap in snapshots:
        is_21ema = snap.trend_state == "pullback_to_21ema"
        entry_type: Literal["pullback_8ema", "pullback_21ema"] = (
            "pullback_21ema" if is_21ema else "pullback_8ema"
        )
        target_ema = snap.ema_21 if is_21ema else snap.ema_8
        stop_loss = _compute_stop_loss(entry_type, snap.ema_21)
        inst_pct = institutional_map.get(snap.ticker)

        raw_conv = getattr(snap, "raw_conviction", None)
        if not raw_conv or raw_conv == "none":
            raw_conv = snap.conviction_level

        has_csp, csp_strike = _find_active_csp(snap.ticker, positions)

        rec_text = _build_recommendation_text_from_snapshot(
            snap, entry_type, stop_loss, has_csp, csp_strike,
        )
        risk_note = _build_risk_reward_note_from_snapshot(
            snap, entry_type, stop_loss, has_csp,
        )

        recs.append(StockBuyRecommendation(
            ticker=snap.ticker,
            entry_type=entry_type,
            entry_price=snap.last_close,
            target_ema_value=target_ema,
            stop_loss=stop_loss,
            conviction=raw_conv,
            institutional_pct=inst_pct,
            institutional_label=_institutional_label(inst_pct),
            volume_confirmation=snap.volume_declining,
            position_size_hint="large" if is_21ema else "standard",
            days_above_emas=snap.days_above_both_emas or 0,
            ema_8_slope=snap.ema_8_slope,
            ema_21_slope=snap.ema_21_slope,
            ema_50_slope=getattr(snap, "ema_50_slope", 0.0) or 0.0,
            rsi_14=getattr(snap, "rsi_14", 0.0) or 0.0,
            related_csp_strike=csp_strike,
            has_active_csp=has_csp,
            recommendation=rec_text,
            risk_reward_note=risk_note,
        ))

    logger.info(
        "stock_recommendations_from_snapshots",
        total=len(recs),
        with_active_csp=sum(1 for r in recs if r.has_active_csp),
        pullback_21ema=sum(1 for r in recs if r.entry_type == "pullback_21ema"),
    )
    return recs


def _build_recommendation_text_from_snapshot(
    snap: ConvictionSnapshot,
    entry_type: str,
    stop_loss: float,
    has_active_csp: bool,
    csp_strike: float | None,
) -> str:
    if has_active_csp and csp_strike is not None:
        return (
            f"Active CSP at ${csp_strike:.2f} on {snap.ticker}. "
            f"If assigned, you'll own shares near the pullback level. "
            f"No additional stock buy needed unless adding to position."
        )
    if entry_type == "pullback_21ema":
        return (
            f"Buy {snap.ticker} near ${snap.last_close:.2f} — "
            f"21-EMA institutional defense zone. "
            f"Larger position recommended. Stop below ${stop_loss:.2f}."
        )
    return (
        f"Buy {snap.ticker} near ${snap.last_close:.2f} — "
        f"8-EMA pullback entry. Standard position size. "
        f"Stop below ${stop_loss:.2f}."
    )


def _build_risk_reward_note_from_snapshot(
    snap: ConvictionSnapshot,
    entry_type: str,
    stop_loss: float,
    has_active_csp: bool,
) -> str:
    parts: list[str] = []
    risk_pct = abs(snap.last_close - stop_loss) / snap.last_close * 100
    parts.append(f"Risk to stop: {risk_pct:.1f}%")

    if snap.volume_declining:
        parts.append("Volume declining on pullback (bullish)")
    else:
        parts.append("Volume not declining — watch for confirmation")

    if has_active_csp:
        parts.append("CSP already provides downside exposure via assignment")

    if entry_type == "pullback_21ema":
        parts.append("21-EMA = institutional defense line, higher conviction")

    return ". ".join(parts) + "."
