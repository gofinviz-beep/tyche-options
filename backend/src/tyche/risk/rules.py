"""Individual risk rule implementations."""

from __future__ import annotations

from dataclasses import dataclass

from tyche.config import TycheSettings
from tyche.risk.engine import OrderCandidate, PortfolioContext, RuleResult


@dataclass
class KillSwitchRule:
    """Blocks all orders when preview-only mode is active."""

    name: str = "kill_switch"

    def __init__(self, settings: TycheSettings) -> None:
        self.name = "kill_switch"
        self._preview_only = settings.preview_only_mode

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if self._preview_only:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason="Preview-only mode is active — live trading disabled",
            )
        return RuleResult(passed=True, rule_name=self.name, reason="Live trading enabled")


@dataclass
class CashCollateralRule:
    """Verifies sufficient cash to secure a CSP (strike x 100 x contracts)."""

    name: str = "cash_collateral"

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if candidate.strategy != "csp":
            return RuleResult(
                passed=True, rule_name=self.name, reason="Not a CSP — rule not applicable"
            )

        required = candidate.strike * 100 * candidate.quantity
        available = context.balance.buying_power

        if required > available:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=(
                    f"Insufficient cash collateral: need ${required:,.2f} "
                    f"(${candidate.strike} x 100 x {candidate.quantity}), "
                    f"available ${available:,.2f}"
                ),
                details={"required": required, "available": available},
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Cash collateral OK: ${required:,.2f} of ${available:,.2f} available",
            details={"required": required, "available": available, "utilization_pct": round(required / available * 100, 1)},
        )


@dataclass
class MaxContractsRule:
    """Caps the number of contracts per position."""

    name: str = "max_contracts"

    def __init__(self, max_contracts: int = 40) -> None:
        self.name = "max_contracts"
        self._max = max_contracts

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if candidate.quantity > self._max:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=f"Exceeds max contracts per position: {candidate.quantity} > {self._max}",
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Contracts OK: {candidate.quantity} <= {self._max}",
        )


@dataclass
class MaxConcentrationRule:
    """Checks if the order would create excessive single-ticker concentration."""

    name: str = "max_concentration"

    def __init__(self, max_pct: float = 25.0) -> None:
        self.name = "max_concentration"
        self._max_pct = max_pct

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        total_value = context.balance.net_liquidation_value
        if total_value <= 0:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason="Cannot calculate concentration — net liq value is zero",
            )

        existing_exposure = sum(
            abs(p.market_value)
            for p in context.positions
            if p.symbol == candidate.symbol
        )

        if candidate.strategy == "csp":
            new_exposure = candidate.strike * 100 * candidate.quantity
        else:
            new_exposure = candidate.limit_price * 100 * candidate.quantity

        total_exposure = existing_exposure + new_exposure
        concentration_pct = (total_exposure / total_value) * 100

        if concentration_pct > self._max_pct:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=(
                    f"Concentration {concentration_pct:.1f}% exceeds max {self._max_pct}% "
                    f"for {candidate.symbol}"
                ),
                details={"concentration_pct": concentration_pct, "max_pct": self._max_pct},
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Concentration OK: {concentration_pct:.1f}% <= {self._max_pct}%",
            details={"concentration_pct": concentration_pct},
        )


@dataclass
class MaxOpenPositionsRule:
    """Limits the number of open positions."""

    name: str = "max_open_positions"

    def __init__(self, max_positions: int = 8) -> None:
        self.name = "max_open_positions"
        self._max = max_positions

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        current = len(context.positions)
        if current >= self._max:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=f"At max open positions: {current} >= {self._max}",
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Positions OK: {current} < {self._max}",
        )


@dataclass
class MaxDailyTradesRule:
    """Limits new trades per day."""

    name: str = "max_daily_trades"

    def __init__(self, max_trades: int = 3) -> None:
        self.name = "max_daily_trades"
        self._max = max_trades

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if context.trades_today >= self._max:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=f"Daily trade limit reached: {context.trades_today} >= {self._max}",
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Daily trades OK: {context.trades_today} < {self._max}",
        )


@dataclass
class StrategyWhitelistRule:
    """Only allows approved strategy types."""

    name: str = "strategy_whitelist"
    ALLOWED: frozenset[str] = frozenset(
        {"csp", "covered_call", "long_call", "long_put", "vertical_spread"}
    )

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if candidate.strategy not in self.ALLOWED:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=f"Strategy '{candidate.strategy}' not in whitelist: {sorted(self.ALLOWED)}",
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Strategy '{candidate.strategy}' is allowed",
        )


@dataclass
class EarningsProximityRule:
    """Flags if earnings falls within the option's DTE window."""

    name: str = "earnings_proximity"

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if not candidate.earnings_within_dte:
            return RuleResult(
                passed=True,
                rule_name=self.name,
                reason="No earnings within DTE window",
            )

        if candidate.earnings_acknowledged:
            return RuleResult(
                passed=True,
                rule_name=self.name,
                reason="Earnings within DTE — user acknowledged the risk",
                details={"earnings_within_dte": True, "acknowledged": True},
            )

        return RuleResult(
            passed=False,
            rule_name=self.name,
            reason=(
                f"Earnings for {candidate.symbol} falls within the option's DTE window. "
                "Acknowledge earnings risk to proceed."
            ),
            details={"earnings_within_dte": True, "acknowledged": False},
        )


@dataclass
class AssignmentExposureRule:
    """Checks if being assigned would create excessive single-stock exposure."""

    name: str = "assignment_exposure"

    def __init__(self, max_pct: float = 25.0) -> None:
        self.name = "assignment_exposure"
        self._max_pct = max_pct

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult:
        if candidate.strategy != "csp":
            return RuleResult(
                passed=True, rule_name=self.name, reason="Not a CSP — rule not applicable"
            )

        total_value = context.balance.net_liquidation_value
        if total_value <= 0:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason="Cannot calculate — net liq value is zero",
            )

        assignment_value = candidate.strike * 100 * candidate.quantity
        existing_shares_value = sum(
            abs(p.market_value)
            for p in context.positions
            if p.symbol == candidate.symbol
        )
        total_if_assigned = existing_shares_value + assignment_value
        pct = (total_if_assigned / total_value) * 100

        if pct > self._max_pct:
            return RuleResult(
                passed=False,
                rule_name=self.name,
                reason=(
                    f"If assigned, {candidate.symbol} would be {pct:.1f}% of portfolio "
                    f"(max {self._max_pct}%)"
                ),
                details={"if_assigned_pct": pct, "max_pct": self._max_pct},
            )
        return RuleResult(
            passed=True,
            rule_name=self.name,
            reason=f"Assignment exposure OK: {pct:.1f}% <= {self._max_pct}%",
            details={"if_assigned_pct": pct},
        )


def build_default_rules(settings: TycheSettings) -> list:
    """Build the default risk rule pipeline from settings."""
    return [
        KillSwitchRule(settings),
        StrategyWhitelistRule(),
        CashCollateralRule(),
        MaxContractsRule(settings.max_contracts_per_position),
        MaxConcentrationRule(settings.max_concentration_per_ticker_pct),
        AssignmentExposureRule(settings.max_concentration_per_ticker_pct),
        MaxOpenPositionsRule(settings.max_open_positions),
        MaxDailyTradesRule(settings.max_new_trades_per_day),
        EarningsProximityRule(),
    ]
