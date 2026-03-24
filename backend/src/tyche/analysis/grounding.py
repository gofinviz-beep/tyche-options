"""Grounding validation — ensures LLM inputs and outputs are honest."""

from __future__ import annotations

from typing import Any

import structlog

from tyche.broker.base import AccountBalance, BrokerPosition, Quote
from tyche.exceptions import GroundingViolation
from tyche.schemas.analysis import CSPAnalysis, OrderMonitorAnalysis

logger = structlog.get_logger()


def validate_csp_output(
    analysis: CSPAnalysis,
    known_symbols: set[str],
    available_cash: float,
) -> list[str]:
    """Validate a CSP analysis output against known data.

    Returns:
        List of warning messages (empty if clean).

    Raises:
        GroundingViolation: If the output contains fabricated data.
    """
    warnings: list[str] = []

    if analysis.ticker not in known_symbols:
        raise GroundingViolation(
            f"LLM recommended ticker '{analysis.ticker}' which is not in the watchlist"
        )

    if analysis.collateral_required > available_cash * 1.1:
        warnings.append(
            f"Recommended collateral ${analysis.collateral_required:,.2f} "
            f"exceeds available cash ${available_cash:,.2f}"
        )

    if analysis.annualized_return_pct > 500:
        warnings.append(
            f"Annualized return {analysis.annualized_return_pct}% seems unrealistically high"
        )

    if analysis.suggested_contracts < 0:
        raise GroundingViolation("LLM suggested negative contracts")

    return warnings


def validate_order_monitor_output(
    analysis: OrderMonitorAnalysis,
    known_order_ids: set[str],
) -> list[str]:
    """Validate order monitor output against known orders."""
    warnings: list[str] = []

    if analysis.order_id not in known_order_ids:
        raise GroundingViolation(
            f"LLM referenced order '{analysis.order_id}' which doesn't exist"
        )

    if analysis.reprice_suggestion is not None and analysis.reprice_suggestion < 0:
        raise GroundingViolation("LLM suggested negative reprice")

    return warnings
