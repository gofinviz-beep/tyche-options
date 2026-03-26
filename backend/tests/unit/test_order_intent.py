"""Tests for the OrderIntent model."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from tyche.models.order_intent import OrderIntent


class TestOrderIntentModel:

    def test_creation_with_explicit_fields(self):
        intent = OrderIntent(
            id="test-id",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            status="pending",
            symbol="PL",
            side="sell_to_open",
            strategy="csp",
            quantity=40,
            estimated_premium=0.0,
            collateral_required=0.0,
            risk_passed=False,
            conviction_level="none",
            trend_state="unknown",
        )
        assert intent.status == "pending"
        assert intent.estimated_premium == 0.0
        assert intent.collateral_required == 0.0
        assert intent.risk_passed is False
        assert intent.conviction_level == "none"
        assert intent.approved_at is None
        assert intent.executed_at is None

    def test_full_lifecycle(self):
        now = datetime.now(timezone.utc)
        intent = OrderIntent(
            id="lifecycle-test",
            created_at=now,
            updated_at=now,
            status="pending",
            symbol="AAPL",
            side="sell_to_open",
            strategy="csp",
            quantity=10,
            strike=175.0,
            expiration="2026-04-03",
            limit_price=1.50,
            estimated_premium=1500.0,
            collateral_required=175_000.0,
            annualized_return_pct=22.5,
            conviction_level="high",
            trend_state="strong_uptrend",
            thesis="AAPL in strong uptrend above both EMAs",
            risk_passed=True,
        )
        assert intent.status == "pending"

        intent.status = "approved"
        intent.approved_at = now
        assert intent.status == "approved"

        intent.status = "executed"
        intent.executed_at = now
        intent.actual_fill_price = 1.45
        intent.actual_quantity = 10
        intent.actual_premium = 1450.0
        assert intent.status == "executed"
        assert intent.actual_fill_price == 1.45

    def test_conviction_fields(self):
        intent = OrderIntent(
            id="conv-test",
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
            status="pending",
            symbol="NVDA",
            side="sell_to_open",
            strategy="csp",
            quantity=5,
            conviction_level="medium",
            trend_state="pullback_to_21ema",
        )
        assert intent.conviction_level == "medium"
        assert intent.trend_state == "pullback_to_21ema"
