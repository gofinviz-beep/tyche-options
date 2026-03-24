"""Wheel lifecycle state machine — tracks CSP -> assignment -> CC cycles."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import structlog

logger = structlog.get_logger()

# Valid state transitions
TRANSITIONS: dict[str, list[str]] = {
    "csp_pending": ["csp_open", "cancelled"],
    "csp_open": ["premium_collected", "assigned"],
    "premium_collected": ["completed"],
    "assigned": ["holding_shares"],
    "holding_shares": ["cc_pending", "sold_at_market", "sold_at_limit"],
    "cc_pending": ["cc_open", "cancelled"],
    "cc_open": ["cc_premium_collected", "called_away"],
    "cc_premium_collected": ["holding_shares", "completed"],
    "called_away": ["completed"],
    "sold_at_market": ["completed"],
    "sold_at_limit": ["completed"],
    "cancelled": ["completed"],
    "completed": [],
}


class WheelStateError(Exception):
    """Invalid state transition."""


def validate_transition(current_state: str, new_state: str) -> bool:
    """Check if a state transition is valid."""
    allowed = TRANSITIONS.get(current_state, [])
    return new_state in allowed


def transition(
    cycle: dict[str, Any],
    new_state: str,
    **kwargs: Any,
) -> dict[str, Any]:
    """Apply a state transition to a wheel cycle dict.

    Args:
        cycle: Mutable dict representing the wheel cycle state.
        new_state: Target state.
        **kwargs: Additional fields to update on the cycle.

    Returns:
        Updated cycle dict.

    Raises:
        WheelStateError: If the transition is invalid.
    """
    current = cycle.get("state", "")
    if not validate_transition(current, new_state):
        raise WheelStateError(
            f"Invalid transition: {current} -> {new_state}. "
            f"Allowed: {TRANSITIONS.get(current, [])}"
        )

    cycle["state"] = new_state
    cycle.update(kwargs)

    now = datetime.now(timezone.utc)

    match new_state:
        case "csp_open":
            cycle.setdefault("csp_filled_at", now)
        case "premium_collected":
            csp_premium = cycle.get("csp_premium_received", 0)
            cycle["total_premium_collected"] = (
                cycle.get("total_premium_collected", 0) + csp_premium
            )
            cycle["total_realized_pl"] = cycle.get("total_realized_pl", 0) + csp_premium
        case "assigned":
            cycle["assigned_at"] = now
            shares = cycle.get("csp_contracts", 0) * 100
            cycle["assigned_shares"] = shares
            cycle["assignment_cost_basis"] = cycle.get("csp_strike", 0) * shares
        case "holding_shares":
            pass
        case "cc_open":
            cycle["cc_rounds"] = cycle.get("cc_rounds", 0) + 1
        case "cc_premium_collected":
            cc_premium = kwargs.get("cc_premium", 0)
            cycle["cc_total_premium_received"] = (
                cycle.get("cc_total_premium_received", 0) + cc_premium
            )
            cycle["total_premium_collected"] = (
                cycle.get("total_premium_collected", 0) + cc_premium
            )
            cycle["total_realized_pl"] = cycle.get("total_realized_pl", 0) + cc_premium
        case "called_away":
            sale_price = kwargs.get("shares_sold_at", 0)
            cost_basis = cycle.get("assignment_cost_basis", 0)
            shares = cycle.get("assigned_shares", 0)
            capital_gain = (sale_price * shares) - cost_basis if shares else 0
            cycle["shares_sold_at"] = sale_price
            cycle["shares_sold_method"] = "called_away"
            cycle["total_realized_pl"] = (
                cycle.get("total_realized_pl", 0) + capital_gain
            )
            cycle["completed_at"] = now
        case "sold_at_market" | "sold_at_limit":
            sale_price = kwargs.get("shares_sold_at", 0)
            cost_basis = cycle.get("assignment_cost_basis", 0)
            shares = cycle.get("assigned_shares", 0)
            capital_gain = (sale_price * shares) - cost_basis if shares else 0
            cycle["shares_sold_at"] = sale_price
            cycle["shares_sold_method"] = new_state
            cycle["total_realized_pl"] = (
                cycle.get("total_realized_pl", 0) + capital_gain
            )
            cycle["completed_at"] = now
        case "completed":
            cycle["completed_at"] = now

    logger.info(
        "wheel_transition",
        symbol=cycle.get("symbol"),
        transition=f"{current} -> {new_state}",
        total_pl=cycle.get("total_realized_pl", 0),
    )
    return cycle
