"""Tests for tyche.risk.overlap — CSP-vs-stock overlap exposure policy."""

from __future__ import annotations

import pytest

from tyche.risk.overlap import OverlapPolicy, OverlapResult


# ── OverlapPolicy ────────────────────────────────────────────────────────

class TestOverlapPolicy:
    @pytest.fixture
    def policy(self) -> OverlapPolicy:
        return OverlapPolicy(net_exposure_cap_pct=25.0, small_add_max_pct=15.0)

    # --- No active CSP ---

    def test_no_csp_returns_add_standard(self, policy: OverlapPolicy):
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="high",
            positions=[], portfolio_value=100_000,
        )
        assert result.decision == "add_standard"
        assert not result.has_active_csp
        assert result.csp_strike is None

    def test_no_positions_returns_add_standard(self, policy: OverlapPolicy):
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="high",
            positions=None, portfolio_value=100_000,
        )
        assert result.decision == "add_standard"

    # --- Active CSP, defer due to exposure cap ---

    def test_csp_exceeds_cap_defers(self, policy: OverlapPolicy):
        positions = [
            {"symbol": "AAPL", "option_type": "put", "strike": 180.0, "quantity": 5},
            {"symbol": "AAPL", "option_type": "", "strike": None, "quantity": 200},
        ]
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="high",
            positions=positions, portfolio_value=100_000,
        )
        assert result.decision == "defer"
        assert result.has_active_csp
        assert result.csp_assigned_equivalent_shares == 500

    # --- Active CSP, add_small conditions met ---

    def test_csp_high_conviction_deep_otm_add_small(self, policy: OverlapPolicy):
        positions = [
            {"symbol": "PL", "option_type": "put", "strike": 20.0, "quantity": 1},
        ]
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="high",
            positions=positions, portfolio_value=100_000,
        )
        assert result.decision == "add_small"
        assert result.has_active_csp
        assert result.csp_strike == 20.0

    # --- Active CSP, medium conviction defers ---

    def test_csp_medium_conviction_defers(self, policy: OverlapPolicy):
        positions = [
            {"symbol": "PL", "option_type": "put", "strike": 20.0, "quantity": 1},
        ]
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="medium",
            positions=positions, portfolio_value=100_000,
        )
        assert result.decision == "defer"

    # --- Active CSP, close strike defers even with high conviction ---

    def test_csp_close_strike_defers_even_high_conviction(self, policy: OverlapPolicy):
        positions = [
            {"symbol": "PL", "option_type": "put", "strike": 24.0, "quantity": 1},
        ]
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="high",
            positions=positions, portfolio_value=100_000,
        )
        assert result.decision == "defer"

    # --- Multiple CSPs pick nearest strike ---

    def test_multiple_csps_uses_nearest_strike(self, policy: OverlapPolicy):
        positions = [
            {"symbol": "AAPL", "option_type": "put", "strike": 170.0, "quantity": 1},
            {"symbol": "AAPL", "option_type": "put", "strike": 180.0, "quantity": 1},
        ]
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="high",
            positions=positions, portfolio_value=500_000,
        )
        assert result.csp_strike == 170.0
        assert result.csp_assigned_equivalent_shares == 200

    # --- to_dict ---

    def test_to_dict_structure(self, policy: OverlapPolicy):
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="high",
            positions=[], portfolio_value=100_000,
        )
        d = result.to_dict()
        assert "decision" in d
        assert "has_active_csp" in d
        assert "net_exposure_pct" in d
        assert "reason" in d


# ── Exposure Calculations ────────────────────────────────────────────────

class TestExposureCalculation:
    def test_stock_only_exposure(self):
        policy = OverlapPolicy(net_exposure_cap_pct=25.0)
        positions = [
            {"symbol": "AAPL", "option_type": "", "strike": None, "quantity": 100},
        ]
        result = policy.evaluate(
            ticker="AAPL", entry_price=200.0, conviction="medium",
            positions=positions, portfolio_value=100_000,
        )
        assert result.stock_shares_held == 100
        assert result.csp_assigned_equivalent_shares == 0
        assert result.net_exposure_pct == pytest.approx(20.0, abs=0.1)

    def test_csp_assigned_equivalent(self):
        policy = OverlapPolicy(net_exposure_cap_pct=50.0)
        positions = [
            {"symbol": "PL", "option_type": "put", "strike": 23.0, "quantity": 3},
        ]
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="medium",
            positions=positions, portfolio_value=100_000,
        )
        assert result.csp_assigned_equivalent_shares == 300

    def test_zero_portfolio_value_no_crash(self):
        policy = OverlapPolicy()
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="high",
            positions=[], portfolio_value=0.0,
        )
        assert result.decision == "add_standard"
        assert result.net_exposure_pct == 0.0


# ── Edge Cases ───────────────────────────────────────────────────────────

class TestOverlapEdgeCases:
    def test_case_insensitive_ticker_match(self):
        policy = OverlapPolicy()
        positions = [
            {"symbol": "aapl", "option_type": "put", "strike": 180.0, "quantity": 1},
        ]
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="medium",
            positions=positions, portfolio_value=500_000,
        )
        assert result.has_active_csp

    def test_non_csp_positions_ignored(self):
        policy = OverlapPolicy()
        positions = [
            {"symbol": "AAPL", "option_type": "call", "strike": 200.0, "quantity": 5},
        ]
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="high",
            positions=positions, portfolio_value=100_000,
        )
        assert result.decision == "add_standard"
        assert not result.has_active_csp

    def test_different_ticker_csp_ignored(self):
        policy = OverlapPolicy()
        positions = [
            {"symbol": "MSFT", "option_type": "put", "strike": 350.0, "quantity": 2},
        ]
        result = policy.evaluate(
            ticker="AAPL", entry_price=190.0, conviction="high",
            positions=positions, portfolio_value=100_000,
        )
        assert result.decision == "add_standard"
        assert not result.has_active_csp

    def test_short_put_option_type_recognized(self):
        policy = OverlapPolicy()
        positions = [
            {"symbol": "PL", "option_type": "short_put", "strike": 20.0, "quantity": 1},
        ]
        result = policy.evaluate(
            ticker="PL", entry_price=25.0, conviction="medium",
            positions=positions, portfolio_value=100_000,
        )
        assert result.has_active_csp
