"""Tests for the wheel lifecycle state machine."""

from __future__ import annotations

import pytest

from tyche.workflow.wheel_tracker import (
    WheelStateError,
    transition,
    validate_transition,
)


def _new_cycle() -> dict:
    return {
        "symbol": "PL",
        "state": "csp_pending",
        "csp_strike": 23.0,
        "csp_contracts": 40,
        "csp_premium_received": 7200.0,
        "total_premium_collected": 0.0,
        "total_realized_pl": 0.0,
    }


class TestValidateTransition:
    def test_valid_transitions(self) -> None:
        assert validate_transition("csp_pending", "csp_open")
        assert validate_transition("csp_open", "premium_collected")
        assert validate_transition("csp_open", "assigned")
        assert validate_transition("assigned", "holding_shares")
        assert validate_transition("holding_shares", "cc_pending")
        assert validate_transition("holding_shares", "sold_at_market")
        assert validate_transition("cc_open", "called_away")

    def test_invalid_transitions(self) -> None:
        assert not validate_transition("csp_pending", "assigned")
        assert not validate_transition("completed", "csp_open")
        assert not validate_transition("csp_open", "holding_shares")


class TestTransition:
    def test_happy_path_premium_collected(self) -> None:
        """CSP expires OTM — premium collected, cycle complete."""
        cycle = _new_cycle()

        cycle = transition(cycle, "csp_open")
        assert cycle["state"] == "csp_open"

        cycle = transition(cycle, "premium_collected")
        assert cycle["state"] == "premium_collected"
        assert cycle["total_premium_collected"] == 7200.0
        assert cycle["total_realized_pl"] == 7200.0

        cycle = transition(cycle, "completed")
        assert cycle["state"] == "completed"
        assert cycle["completed_at"] is not None

    def test_full_wheel_with_assignment(self) -> None:
        """CSP assigned -> hold shares -> CC -> called away."""
        cycle = _new_cycle()

        cycle = transition(cycle, "csp_open")
        cycle = transition(cycle, "assigned")
        assert cycle["assigned_shares"] == 4000
        assert cycle["assignment_cost_basis"] == 92000.0

        cycle = transition(cycle, "holding_shares")
        cycle = transition(cycle, "cc_pending")
        cycle["cc_current_strike"] = 25.0
        cycle = transition(cycle, "cc_open")
        assert cycle["cc_rounds"] == 1

        cycle = transition(cycle, "cc_premium_collected", cc_premium=3000.0)
        assert cycle["cc_total_premium_received"] == 3000.0
        assert cycle["total_premium_collected"] == 3000.0

        # Second CC round — must go back to holding_shares first
        cycle = transition(cycle, "holding_shares")
        cycle = transition(cycle, "cc_pending")
        cycle = transition(cycle, "cc_open")
        assert cycle["cc_rounds"] == 2

        cycle = transition(cycle, "called_away", shares_sold_at=25.0)
        assert cycle["shares_sold_method"] == "called_away"
        # Capital gain: (25 * 4000) - 92000 = 8000
        # Total P&L: CSP premium (0 since not collected path) + CC premium (3000) + capital gain (8000)
        assert cycle["total_realized_pl"] == 3000.0 + 8000.0

    def test_direct_share_sale(self) -> None:
        """Assigned shares sold at market instead of writing CC."""
        cycle = _new_cycle()

        cycle = transition(cycle, "csp_open")
        cycle = transition(cycle, "assigned")
        cycle = transition(cycle, "holding_shares")
        cycle = transition(cycle, "sold_at_market", shares_sold_at=24.0)

        assert cycle["shares_sold_method"] == "sold_at_market"
        # Capital gain: (24 * 4000) - 92000 = 4000
        assert cycle["total_realized_pl"] == 4000.0

    def test_invalid_transition_raises(self) -> None:
        cycle = _new_cycle()
        with pytest.raises(WheelStateError, match="Invalid transition"):
            transition(cycle, "assigned")

    def test_cancelled_cycle(self) -> None:
        cycle = _new_cycle()
        cycle = transition(cycle, "cancelled")
        assert cycle["state"] == "cancelled"
        cycle = transition(cycle, "completed")
        assert cycle["state"] == "completed"
