"""Active order & position monitor — aggressive real-time tracking.

Tracks two scenarios:
1. PENDING orders: monitors stock trend while waiting for fill,
   suggests hiking premium, changing strike, or cancelling.
2. FILLED positions: monitors P&L, proximity to strike,
   suggests buy-to-close, roll, or hold.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from enum import Enum
from typing import Any

import structlog

from tyche.broker.base import BrokerClient, Quote

logger = structlog.get_logger()


class AlertSeverity(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class TrendDirection(str, Enum):
    UP = "up"
    DOWN = "down"
    FLAT = "flat"


@dataclass
class PriceSnapshot:
    timestamp: datetime
    price: float
    bid: float
    ask: float
    volume: int


@dataclass
class IntradayTrend:
    direction: TrendDirection
    velocity_per_min: float
    price_change_pct: float
    samples: int
    window_minutes: float


@dataclass
class SuggestedAction:
    action: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class MonitorAlert:
    severity: AlertSeverity
    alert_type: str
    symbol: str
    message: str
    suggested_actions: list[SuggestedAction] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "severity": self.severity.value,
            "alert_type": self.alert_type,
            "symbol": self.symbol,
            "message": self.message,
            "suggested_actions": [
                {"action": a.action, "reason": a.reason, "details": a.details}
                for a in self.suggested_actions
            ],
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
        }


@dataclass
class TrackedPosition:
    """A filled position being actively monitored."""

    symbol: str
    option_symbol: str
    position_type: str  # "short_put", "short_call"
    strike: float
    expiration: date
    entry_price: float
    contracts: int
    entry_date: date
    underlying_at_entry: float


@dataclass
class PositionStatus:
    """Real-time status of a tracked position."""

    position: TrackedPosition
    underlying_price: float
    option_bid: float
    option_ask: float
    option_mid: float
    delta: float
    theta: float
    pnl_per_contract: float
    total_pnl: float
    distance_to_strike_pct: float
    dte: int
    trend: IntradayTrend
    alerts: list[MonitorAlert] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.position.symbol,
            "option_symbol": self.position.option_symbol,
            "position_type": self.position.position_type,
            "strike": self.position.strike,
            "expiration": self.position.expiration.isoformat(),
            "entry_price": self.position.entry_price,
            "contracts": self.position.contracts,
            "underlying_at_entry": self.position.underlying_at_entry,
            "underlying_price": self.underlying_price,
            "option_bid": self.option_bid,
            "option_ask": self.option_ask,
            "option_mid": self.option_mid,
            "delta": self.delta,
            "theta": self.theta,
            "pnl_per_contract": round(self.pnl_per_contract, 2),
            "total_pnl": round(self.total_pnl, 2),
            "distance_to_strike_pct": round(self.distance_to_strike_pct, 2),
            "dte": self.dte,
            "trend": {
                "direction": self.trend.direction.value,
                "velocity_per_min": round(self.trend.velocity_per_min, 4),
                "price_change_pct": round(self.trend.price_change_pct, 2),
                "samples": self.trend.samples,
                "window_minutes": round(self.trend.window_minutes, 1),
            },
            "alerts": [a.to_dict() for a in self.alerts],
        }


_TREND_WINDOW = 20  # max price samples to keep per symbol
_FLAT_THRESHOLD_PCT = 0.05  # < 0.05% change = flat


class ActiveMonitor:
    """Aggressive real-time monitor for pending orders and filled positions."""

    def __init__(self, broker: BrokerClient) -> None:
        self._broker = broker
        self._price_history: dict[str, deque[PriceSnapshot]] = {}
        self._tracked_positions: dict[str, TrackedPosition] = {}

    def track_position(self, position: TrackedPosition) -> None:
        self._tracked_positions[position.option_symbol] = position
        logger.info(
            "position_tracked",
            symbol=position.symbol,
            strike=position.strike,
            expiration=position.expiration.isoformat(),
            contracts=position.contracts,
            entry_price=position.entry_price,
        )

    def untrack_position(self, option_symbol: str) -> None:
        self._tracked_positions.pop(option_symbol, None)

    def get_tracked_symbols(self) -> list[str]:
        return list({p.symbol for p in self._tracked_positions.values()})

    async def poll_price(self, symbol: str) -> Quote:
        """Fetch and record a price snapshot."""
        quote = await self._broker.get_quote(symbol)
        snap = PriceSnapshot(
            timestamp=datetime.now(timezone.utc),
            price=quote.last,
            bid=quote.bid,
            ask=quote.ask,
            volume=quote.volume,
        )
        if symbol not in self._price_history:
            self._price_history[symbol] = deque(maxlen=_TREND_WINDOW)
        self._price_history[symbol].append(snap)
        return quote

    def compute_trend(self, symbol: str) -> IntradayTrend:
        """Compute intraday trend from price history."""
        history = self._price_history.get(symbol, deque())
        if len(history) < 2:
            return IntradayTrend(
                direction=TrendDirection.FLAT,
                velocity_per_min=0.0,
                price_change_pct=0.0,
                samples=len(history),
                window_minutes=0.0,
            )

        first = history[0]
        last = history[-1]
        elapsed = (last.timestamp - first.timestamp).total_seconds()
        window_min = elapsed / 60 if elapsed > 0 else 0.01

        price_change = last.price - first.price
        price_change_pct = (price_change / first.price) * 100 if first.price > 0 else 0
        velocity = price_change / window_min if window_min > 0 else 0

        if abs(price_change_pct) < _FLAT_THRESHOLD_PCT:
            direction = TrendDirection.FLAT
        elif price_change > 0:
            direction = TrendDirection.UP
        else:
            direction = TrendDirection.DOWN

        return IntradayTrend(
            direction=direction,
            velocity_per_min=velocity,
            price_change_pct=price_change_pct,
            samples=len(history),
            window_minutes=window_min,
        )

    async def check_position(self, option_symbol: str) -> PositionStatus | None:
        """Full real-time check of a tracked position."""
        pos = self._tracked_positions.get(option_symbol)
        if not pos:
            return None

        quote = await self.poll_price(pos.symbol)
        trend = self.compute_trend(pos.symbol)

        chain = await self._broker.get_options_chain(
            pos.symbol, pos.expiration.strftime("%Y-%m-%d")
        )

        put_or_call = "put" if "put" in pos.position_type else "call"
        contract = next(
            (c for c in chain.contracts if c.option_symbol == pos.option_symbol), None
        )
        if not contract:
            contract = next(
                (c for c in chain.contracts
                 if abs(c.strike - pos.strike) < 0.01
                 and c.option_type == put_or_call),
                None,
            )

        today = date.today()
        dte = (pos.expiration - today).days

        if not contract:
            if pos.strike > 0:
                if pos.position_type == "short_put":
                    fallback_distance = ((quote.last - pos.strike) / pos.strike) * 100
                else:
                    fallback_distance = ((pos.strike - quote.last) / pos.strike) * 100
            else:
                fallback_distance = 0.0

            chain_empty = len(chain.contracts) == 0
            if chain_empty:
                msg = (
                    "Options chain returned empty — market is likely closed. "
                    "Live pricing will resume when market opens."
                )
            else:
                msg = (
                    f"No matching {put_or_call} contract at ${pos.strike:.2f} strike "
                    f"in the {pos.expiration.isoformat()} chain. "
                    "Verify strike and expiration are correct."
                )

            return PositionStatus(
                position=pos,
                underlying_price=quote.last,
                option_bid=0.0,
                option_ask=0.0,
                option_mid=0.0,
                delta=0.0,
                theta=0.0,
                pnl_per_contract=0.0,
                total_pnl=0.0,
                distance_to_strike_pct=fallback_distance,
                dte=dte,
                trend=trend,
                alerts=[MonitorAlert(
                    severity=AlertSeverity.INFO,
                    alert_type="contract_not_found",
                    symbol=pos.symbol,
                    message=msg,
                )],
            )

        if pos.position_type == "short_put":
            pnl_per = (pos.entry_price - contract.mid) * 100
            distance_pct = ((quote.last - pos.strike) / pos.strike) * 100
        else:
            pnl_per = (pos.entry_price - contract.mid) * 100
            distance_pct = ((pos.strike - quote.last) / pos.strike) * 100

        total_pnl = pnl_per * pos.contracts

        alerts = self._generate_position_alerts(
            pos, quote, contract, trend, distance_pct, pnl_per, dte
        )

        return PositionStatus(
            position=pos,
            underlying_price=quote.last,
            option_bid=contract.bid,
            option_ask=contract.ask,
            option_mid=contract.mid,
            delta=contract.delta,
            theta=contract.theta,
            pnl_per_contract=pnl_per,
            total_pnl=total_pnl,
            distance_to_strike_pct=distance_pct,
            dte=dte,
            trend=trend,
            alerts=alerts,
        )

    def _generate_position_alerts(
        self,
        pos: TrackedPosition,
        quote: Quote,
        contract: Any,
        trend: IntradayTrend,
        distance_pct: float,
        pnl_per: float,
        dte: int,
    ) -> list[MonitorAlert]:
        alerts: list[MonitorAlert] = []

        profit_pct = (pnl_per / (pos.entry_price * 100)) * 100 if pos.entry_price > 0 else 0

        # 80% profit — strong buy-to-close signal
        if profit_pct >= 80:
            alerts.append(MonitorAlert(
                severity=AlertSeverity.INFO,
                alert_type="profit_target_80",
                symbol=pos.symbol,
                message=f"80%+ profit captured ({profit_pct:.0f}%). Consider buy-to-close.",
                suggested_actions=[SuggestedAction(
                    action="buy_to_close",
                    reason=f"Lock in {profit_pct:.0f}% of max premium. Free up ${pos.strike * 100 * pos.contracts:,.0f} collateral.",
                    details={"buy_at": contract.ask, "cost": contract.ask * 100 * pos.contracts},
                )],
            ))

        # 50% profit milestone
        elif profit_pct >= 50:
            alerts.append(MonitorAlert(
                severity=AlertSeverity.INFO,
                alert_type="profit_target_50",
                symbol=pos.symbol,
                message=f"50%+ profit captured ({profit_pct:.0f}%). Good exit point to redeploy capital.",
                suggested_actions=[
                    SuggestedAction(
                        action="buy_to_close",
                        reason=f"Capture {profit_pct:.0f}% and redeploy into fresh CSP with better theta.",
                        details={"buy_at": contract.ask},
                    ),
                    SuggestedAction(action="hold", reason=f"{dte} DTE remaining, theta decay accelerating."),
                ],
            ))

        # Stock approaching strike
        if distance_pct < 2:
            severity = AlertSeverity.CRITICAL if distance_pct < 0 else AlertSeverity.WARNING
            itm_note = "IN THE MONEY" if distance_pct < 0 else f"only {distance_pct:.1f}% from strike"

            if pos.position_type == "short_put":
                actions = [
                    SuggestedAction(
                        action="roll_down_and_out",
                        reason="Roll to lower strike and later expiration to collect additional premium and reduce assignment risk.",
                    ),
                    SuggestedAction(
                        action="buy_to_close",
                        reason=f"Close at ${contract.ask:.2f} to limit loss. Cost: ${contract.ask * 100 * pos.contracts:,.0f}",
                        details={"buy_at": contract.ask},
                    ),
                    SuggestedAction(
                        action="accept_assignment",
                        reason=f"If assigned, cost basis = ${pos.strike - pos.entry_price:.2f}/share. Then sell covered calls.",
                    ),
                ]
            else:
                actions = [
                    SuggestedAction(
                        action="roll_up_and_out",
                        reason="Roll to higher strike and later expiration to collect additional premium and avoid assignment.",
                    ),
                    SuggestedAction(
                        action="buy_to_close",
                        reason=f"Close at ${contract.ask:.2f} to avoid assignment. Cost: ${contract.ask * 100 * pos.contracts:,.0f}",
                        details={"buy_at": contract.ask},
                    ),
                    SuggestedAction(
                        action="accept_assignment",
                        reason=f"If assigned, shares called away at ${pos.strike:.2f}/share. Net = strike + premium collected.",
                    ),
                ]

            alerts.append(MonitorAlert(
                severity=severity,
                alert_type="approaching_strike",
                symbol=pos.symbol,
                message=f"Stock at ${quote.last:.2f}, {itm_note} (strike ${pos.strike}).",
                suggested_actions=actions,
            ))

        # Adverse intraday trend
        adverse_put = pos.position_type == "short_put" and trend.direction == TrendDirection.DOWN and trend.price_change_pct < -1.5
        adverse_call = pos.position_type == "short_call" and trend.direction == TrendDirection.UP and trend.price_change_pct > 1.5
        if adverse_put or adverse_call:
            if adverse_put:
                msg = f"Stock dropping {trend.price_change_pct:.1f}% (${trend.velocity_per_min:.2f}/min). Monitor closely."
            else:
                msg = f"Stock rising +{trend.price_change_pct:.1f}% (+${trend.velocity_per_min:.2f}/min). Call strike at risk."
            alerts.append(MonitorAlert(
                severity=AlertSeverity.WARNING,
                alert_type="adverse_trend",
                symbol=pos.symbol,
                message=msg,
                data={
                    "trend_direction": trend.direction.value,
                    "velocity": trend.velocity_per_min,
                    "change_pct": trend.price_change_pct,
                },
            ))

        # Position is losing money
        if profit_pct < -50:
            if pos.position_type == "short_put":
                loss_actions = [
                    SuggestedAction(
                        action="roll_down_and_out",
                        reason="Roll to collect more premium and reduce cost basis if still bullish.",
                    ),
                    SuggestedAction(
                        action="buy_to_close",
                        reason=f"Cut losses. Cost: ${contract.ask * 100 * pos.contracts:,.0f}",
                    ),
                ]
            else:
                loss_actions = [
                    SuggestedAction(
                        action="roll_up_and_out",
                        reason="Roll to higher strike and later expiration to collect more premium.",
                    ),
                    SuggestedAction(
                        action="buy_to_close",
                        reason=f"Cut losses. Cost: ${contract.ask * 100 * pos.contracts:,.0f}",
                    ),
                ]
            alerts.append(MonitorAlert(
                severity=AlertSeverity.CRITICAL,
                alert_type="significant_loss",
                symbol=pos.symbol,
                message=f"Position down {abs(profit_pct):.0f}% from entry. Evaluate exit vs hold.",
                suggested_actions=loss_actions,
            ))

        return alerts

    async def check_all_positions(self) -> list[PositionStatus]:
        """Check all tracked positions and return status updates.

        When real-time data is unavailable (market closed, API error),
        returns a minimal status so the position still appears in the UI.
        """
        results: list[PositionStatus] = []
        for opt_sym in list(self._tracked_positions.keys()):
            try:
                status = await self.check_position(opt_sym)
                if status:
                    results.append(status)
            except Exception:
                logger.warning("position_check_failed", option_symbol=opt_sym, exc_info=True)
                pos = self._tracked_positions.get(opt_sym)
                if pos:
                    dte = (pos.expiration - date.today()).days
                    results.append(PositionStatus(
                        position=pos,
                        underlying_price=pos.underlying_at_entry,
                        option_bid=0.0,
                        option_ask=0.0,
                        option_mid=0.0,
                        delta=0.0,
                        theta=0.0,
                        pnl_per_contract=0.0,
                        total_pnl=0.0,
                        distance_to_strike_pct=0.0,
                        dte=dte,
                        trend=IntradayTrend(
                            direction=TrendDirection.FLAT,
                            velocity_per_min=0.0,
                            price_change_pct=0.0,
                            samples=0,
                            window_minutes=0.0,
                        ),
                        alerts=[MonitorAlert(
                            severity=AlertSeverity.INFO,
                            alert_type="data_unavailable",
                            symbol=pos.symbol,
                            message="Real-time data unavailable (market may be closed)",
                        )],
                    ))
        return results

    async def check_pending_order(
        self,
        symbol: str,
        strike: float,
        limit_price: float,
        side: str,
        expiration: str,
    ) -> MonitorAlert | None:
        """Check a pending (unfilled) order against current market conditions.

        Detects if the stock is trending against the order and suggests
        concrete actions: hike premium, change strike, or cancel.
        """
        quote = await self.poll_price(symbol)
        trend = self.compute_trend(symbol)

        chain = await self._broker.get_options_chain(symbol, expiration)
        contract = next(
            (c for c in chain.puts if c.strike == strike), None
        )

        if side.startswith("sell") and "put" in side.lower():
            return self._assess_pending_sell_put(
                symbol, strike, limit_price, quote, trend, contract
            )
        return None

    def _assess_pending_sell_put(
        self,
        symbol: str,
        strike: float,
        limit_price: float,
        quote: Quote,
        trend: IntradayTrend,
        contract: Any | None,
    ) -> MonitorAlert | None:
        current_bid = contract.bid if contract else 0
        current_ask = contract.ask if contract else 0

        actions: list[SuggestedAction] = []

        if trend.direction == TrendDirection.DOWN and trend.price_change_pct < -1.0:
            # Stock dropping → put premiums rising → opportunity or danger
            if current_bid > limit_price:
                actions.append(SuggestedAction(
                    action="hike_limit_price",
                    reason=f"Put bid now ${current_bid:.2f} (your limit ${limit_price:.2f}). "
                           f"Stock down {trend.price_change_pct:.1f}%, raise to ${current_bid:.2f} for more premium.",
                    details={"new_price": current_bid, "premium_increase": (current_bid - limit_price) * 100},
                ))

            distance_pct = ((quote.last - strike) / strike) * 100
            if distance_pct < 3:
                actions.append(SuggestedAction(
                    action="lower_strike",
                    reason=f"Stock at ${quote.last:.2f}, only {distance_pct:.1f}% above ${strike} strike. "
                           f"Consider lowering strike for more safety margin.",
                    details={"current_distance_pct": distance_pct},
                ))

            if distance_pct < 1 or trend.price_change_pct < -3:
                actions.append(SuggestedAction(
                    action="cancel_order",
                    reason=f"Stock in sharp decline ({trend.price_change_pct:.1f}%). "
                           f"Assignment risk high. Cancel and reassess.",
                ))

            return MonitorAlert(
                severity=AlertSeverity.WARNING if trend.price_change_pct > -2 else AlertSeverity.CRITICAL,
                alert_type="pending_order_adverse_trend",
                symbol=symbol,
                message=(
                    f"{symbol} dropping {trend.price_change_pct:.1f}% while your sell-put order is pending. "
                    f"Stock ${quote.last:.2f}, strike ${strike}, put bid now ${current_bid:.2f}."
                ),
                suggested_actions=actions,
                data={
                    "underlying_price": quote.last,
                    "strike": strike,
                    "your_limit": limit_price,
                    "current_bid": current_bid,
                    "current_ask": current_ask,
                    "trend": trend.direction.value,
                    "velocity": trend.velocity_per_min,
                },
            )

        return None
