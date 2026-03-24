"""Tests for the risk engine and individual rules."""

from __future__ import annotations

import pytest

from tyche.broker.base import AccountBalance, BrokerPosition
from tyche.config import TycheSettings
from tyche.risk.engine import OrderCandidate, PortfolioContext, RiskEngine
from tyche.risk.rules import (
    AssignmentExposureRule,
    CashCollateralRule,
    EarningsProximityRule,
    KillSwitchRule,
    MaxConcentrationRule,
    MaxContractsRule,
    MaxDailyTradesRule,
    MaxOpenPositionsRule,
    StrategyWhitelistRule,
    build_default_rules,
)


@pytest.fixture
def balance() -> AccountBalance:
    return AccountBalance(
        cash=100000.0,
        buying_power=100000.0,
        net_liquidation_value=112000.0,
        market_value=12000.0,
        total_equity=112000.0,
    )


@pytest.fixture
def context(balance: AccountBalance) -> PortfolioContext:
    return PortfolioContext(
        balance=balance,
        positions=[],
        open_orders=[],
        trades_today=0,
    )


@pytest.fixture
def pl_csp() -> OrderCandidate:
    """Modeled after the user's actual PL CSP trade: 40 contracts at $23 strike."""
    return OrderCandidate(
        symbol="PL",
        strategy="csp",
        side="sell_to_open",
        quantity=40,
        strike=23.0,
        limit_price=1.80,
        intent="income",
        option_type="put",
        underlying_price=24.50,
    )


# --- Kill Switch ---


class TestKillSwitch:
    def test_blocks_when_preview_only(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        settings = TycheSettings(
            tradier_api_token="t", tradier_account_id="a", gemini_api_key="g",
            preview_only_mode=True,
        )
        rule = KillSwitchRule(settings)
        result = rule.evaluate(pl_csp, context)
        assert not result.passed
        assert "preview-only" in result.reason.lower()

    def test_passes_when_live(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        settings = TycheSettings(
            tradier_api_token="t", tradier_account_id="a", gemini_api_key="g",
            preview_only_mode=False,
        )
        rule = KillSwitchRule(settings)
        result = rule.evaluate(pl_csp, context)
        assert result.passed


# --- Cash Collateral ---


class TestCashCollateral:
    def test_sufficient_cash(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        rule = CashCollateralRule()
        result = rule.evaluate(pl_csp, context)
        assert result.passed
        assert result.details
        assert result.details["required"] == 92000.0  # $23 x 100 x 40

    def test_insufficient_cash(self, pl_csp: OrderCandidate) -> None:
        context = PortfolioContext(
            balance=AccountBalance(
                cash=50000.0, buying_power=50000.0,
                net_liquidation_value=50000.0, market_value=0.0,
            ),
            positions=[], open_orders=[],
        )
        rule = CashCollateralRule()
        result = rule.evaluate(pl_csp, context)
        assert not result.passed
        assert "92,000" in result.reason

    def test_skips_non_csp(self, context: PortfolioContext) -> None:
        candidate = OrderCandidate(
            symbol="AAPL", strategy="long_call", side="buy_to_open",
            quantity=5, strike=190.0, limit_price=3.50,
        )
        rule = CashCollateralRule()
        result = rule.evaluate(candidate, context)
        assert result.passed


# --- Max Contracts ---


class TestMaxContracts:
    def test_within_limit(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        rule = MaxContractsRule(max_contracts=40)
        result = rule.evaluate(pl_csp, context)
        assert result.passed

    def test_exceeds_limit(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        pl_csp.quantity = 50
        rule = MaxContractsRule(max_contracts=40)
        result = rule.evaluate(pl_csp, context)
        assert not result.passed


# --- Max Concentration ---


class TestMaxConcentration:
    def test_no_existing_positions(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        rule = MaxConcentrationRule(max_pct=85.0)
        result = rule.evaluate(pl_csp, context)
        assert result.passed

    def test_exceeds_with_existing(self, pl_csp: OrderCandidate) -> None:
        context = PortfolioContext(
            balance=AccountBalance(
                cash=100000.0, buying_power=100000.0,
                net_liquidation_value=112000.0, market_value=12000.0,
            ),
            positions=[
                BrokerPosition(
                    symbol="PL", quantity=4000.0,
                    cost_basis=80000.0, market_value=80000.0,
                ),
            ],
            open_orders=[],
        )
        rule = MaxConcentrationRule(max_pct=25.0)
        result = rule.evaluate(pl_csp, context)
        assert not result.passed


# --- Assignment Exposure ---


class TestAssignmentExposure:
    def test_acceptable(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        rule = AssignmentExposureRule(max_pct=90.0)
        result = rule.evaluate(pl_csp, context)
        assert result.passed

    def test_too_concentrated_if_assigned(self, pl_csp: OrderCandidate) -> None:
        context = PortfolioContext(
            balance=AccountBalance(
                cash=100000.0, buying_power=100000.0,
                net_liquidation_value=100000.0, market_value=0.0,
            ),
            positions=[], open_orders=[],
        )
        rule = AssignmentExposureRule(max_pct=50.0)
        result = rule.evaluate(pl_csp, context)
        assert not result.passed
        assert "92.0%" in result.reason


# --- Earnings Proximity ---


class TestEarningsProximity:
    def test_no_earnings(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        pl_csp.earnings_within_dte = False
        rule = EarningsProximityRule()
        result = rule.evaluate(pl_csp, context)
        assert result.passed

    def test_earnings_not_acknowledged(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        pl_csp.earnings_within_dte = True
        pl_csp.earnings_acknowledged = False
        rule = EarningsProximityRule()
        result = rule.evaluate(pl_csp, context)
        assert not result.passed
        assert "acknowledge" in result.reason.lower()

    def test_earnings_acknowledged(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        pl_csp.earnings_within_dte = True
        pl_csp.earnings_acknowledged = True
        rule = EarningsProximityRule()
        result = rule.evaluate(pl_csp, context)
        assert result.passed


# --- Strategy Whitelist ---


class TestStrategyWhitelist:
    def test_allowed_strategies(self, context: PortfolioContext) -> None:
        rule = StrategyWhitelistRule()
        for strategy in ["csp", "covered_call", "long_call", "long_put", "vertical_spread"]:
            candidate = OrderCandidate(
                symbol="X", strategy=strategy, side="sell_to_open", quantity=1,
            )
            assert rule.evaluate(candidate, context).passed

    def test_disallowed_strategy(self, context: PortfolioContext) -> None:
        rule = StrategyWhitelistRule()
        candidate = OrderCandidate(
            symbol="X", strategy="naked_call", side="sell_to_open", quantity=1,
        )
        result = rule.evaluate(candidate, context)
        assert not result.passed


# --- Max Open Positions ---


class TestMaxOpenPositions:
    def test_within_limit(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        rule = MaxOpenPositionsRule(max_positions=8)
        result = rule.evaluate(pl_csp, context)
        assert result.passed

    def test_at_limit(self, pl_csp: OrderCandidate) -> None:
        positions = [
            BrokerPosition(symbol=f"SYM{i}", quantity=100, cost_basis=1000)
            for i in range(8)
        ]
        context = PortfolioContext(
            balance=AccountBalance(cash=100000.0, buying_power=100000.0, net_liquidation_value=112000.0),
            positions=positions, open_orders=[],
        )
        rule = MaxOpenPositionsRule(max_positions=8)
        result = rule.evaluate(pl_csp, context)
        assert not result.passed


# --- Max Daily Trades ---


class TestMaxDailyTrades:
    def test_within_limit(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        rule = MaxDailyTradesRule(max_trades=3)
        result = rule.evaluate(pl_csp, context)
        assert result.passed

    def test_at_limit(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        context.trades_today = 3
        rule = MaxDailyTradesRule(max_trades=3)
        result = rule.evaluate(pl_csp, context)
        assert not result.passed


# --- Full Pipeline ---


class TestRiskEngine:
    def test_full_pipeline_passes(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        settings = TycheSettings(
            tradier_api_token="t", tradier_account_id="a", gemini_api_key="g",
            preview_only_mode=False,
            max_concentration_per_ticker_pct=90.0,
        )
        rules = build_default_rules(settings)
        engine = RiskEngine(rules)
        result = engine.validate(pl_csp, context)
        assert result.all_passed, f"Failures: {result.warnings}"

    def test_pipeline_blocks_on_kill_switch(self, pl_csp: OrderCandidate, context: PortfolioContext) -> None:
        settings = TycheSettings(
            tradier_api_token="t", tradier_account_id="a", gemini_api_key="g",
            preview_only_mode=True,
        )
        rules = build_default_rules(settings)
        engine = RiskEngine(rules)
        result = engine.validate(pl_csp, context)
        assert not result.all_passed
        assert any("preview-only" in f.reason.lower() for f in result.failures)

    def test_pipeline_catches_multiple_violations(self) -> None:
        settings = TycheSettings(
            tradier_api_token="t", tradier_account_id="a", gemini_api_key="g",
            preview_only_mode=True,
            max_contracts_per_position=10,
        )
        rules = build_default_rules(settings)
        engine = RiskEngine(rules)

        candidate = OrderCandidate(
            symbol="PL", strategy="csp", side="sell_to_open",
            quantity=50, strike=23.0, limit_price=1.80, option_type="put",
        )
        context = PortfolioContext(
            balance=AccountBalance(cash=10000.0, buying_power=10000.0, net_liquidation_value=10000.0),
            positions=[], open_orders=[], trades_today=5,
        )
        result = engine.validate(candidate, context)
        assert not result.all_passed
        assert len(result.failures) >= 3  # kill switch + cash + max contracts + daily trades
