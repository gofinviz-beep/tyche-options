"""Risk validation orchestrator — runs all rules as a pipeline."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import structlog

from tyche.broker.base import AccountBalance, BrokerOrder, BrokerPosition

logger = structlog.get_logger()


@dataclass
class PortfolioContext:
    """Snapshot of portfolio state needed for risk evaluation."""

    balance: AccountBalance
    positions: list[BrokerPosition]
    open_orders: list[BrokerOrder]
    trades_today: int = 0


@dataclass(frozen=True)
class RuleResult:
    """Outcome of a single risk rule evaluation."""

    passed: bool
    rule_name: str
    reason: str
    details: dict[str, Any] | None = None


@dataclass
class OrderCandidate:
    """Proposed order to validate against risk rules."""

    symbol: str
    strategy: str  # csp, covered_call, long_call, long_put, vertical_spread
    side: str  # sell_to_open, buy_to_open, etc.
    quantity: int  # number of contracts
    strike: float = 0.0
    limit_price: float = 0.0
    intent: str = "income"  # income, exit_position, entry
    option_type: str = ""  # put, call
    underlying_price: float = 0.0
    earnings_within_dte: bool = False
    earnings_acknowledged: bool = False


class RiskRule(Protocol):
    """Protocol for individual risk rules."""

    name: str

    def evaluate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RuleResult: ...


@dataclass
class RiskValidationResult:
    """Aggregate result of running all risk rules."""

    results: list[RuleResult] = field(default_factory=list)

    @property
    def all_passed(self) -> bool:
        return all(r.passed for r in self.results)

    @property
    def failures(self) -> list[RuleResult]:
        return [r for r in self.results if not r.passed]

    @property
    def warnings(self) -> list[str]:
        return [r.reason for r in self.results if not r.passed]


class RiskEngine:
    """Orchestrates risk rule evaluation as a pipeline.

    All rules must pass for an order to proceed.
    """

    def __init__(self, rules: list[RiskRule] | None = None) -> None:
        self.rules: list[RiskRule] = rules or []

    def add_rule(self, rule: RiskRule) -> None:
        self.rules.append(rule)

    def validate(
        self, candidate: OrderCandidate, context: PortfolioContext
    ) -> RiskValidationResult:
        results: list[RuleResult] = []
        for rule in self.rules:
            result = rule.evaluate(candidate, context)
            results.append(result)
            logger.debug(
                "risk_rule_evaluated",
                rule=rule.name,
                passed=result.passed,
                reason=result.reason,
            )

        validation = RiskValidationResult(results=results)
        if not validation.all_passed:
            logger.warning(
                "risk_validation_failed",
                symbol=candidate.symbol,
                strategy=candidate.strategy,
                failures=[f.rule_name for f in validation.failures],
            )
        return validation
