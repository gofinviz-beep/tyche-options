"""CSP-vs-stock overlap exposure policy.

Enforces explicit rules when a stock buy recommendation overlaps
with an active Cash-Secured Put on the same ticker.  Produces
machine-readable decision states instead of text-only recommendations.

Three decision states:

* ``add_standard``  — no overlap concern; proceed with standard position.
* ``add_small``     — overlap exists but conditions favour a small add
                      (e.g. CSP is deep OTM, conviction is high).
* ``defer``         — overlap creates excess exposure; wait for CSP
                      to expire or be closed first.

The policy also enforces a per-ticker net exposure cap to prevent
the combined CSP assignment-equivalent + stock shares from exceeding
a configurable percentage of portfolio value.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

import structlog

logger = structlog.get_logger()

OverlapDecision = Literal["add_standard", "add_small", "defer"]


@dataclass(frozen=True)
class OverlapResult:
    """Machine-readable result of the overlap policy evaluation."""

    ticker: str
    decision: OverlapDecision
    has_active_csp: bool
    csp_strike: float | None
    stock_shares_held: int
    csp_assigned_equivalent_shares: int
    net_exposure_pct: float
    net_exposure_cap_pct: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "decision": self.decision,
            "has_active_csp": self.has_active_csp,
            "csp_strike": self.csp_strike,
            "stock_shares_held": self.stock_shares_held,
            "csp_assigned_equivalent_shares": self.csp_assigned_equivalent_shares,
            "net_exposure_pct": round(self.net_exposure_pct, 2),
            "net_exposure_cap_pct": self.net_exposure_cap_pct,
            "reason": self.reason,
        }


class OverlapPolicy:
    """Evaluates CSP-vs-stock exposure overlap for a single ticker.

    Decision logic:

    1. No active CSP → ``add_standard``.
    2. Active CSP + net exposure would exceed cap → ``defer``.
    3. Active CSP + high conviction + CSP strike > 5% below entry → ``add_small``.
    4. Active CSP + otherwise → ``defer``.
    """

    def __init__(
        self,
        net_exposure_cap_pct: float = 25.0,
        small_add_max_pct: float = 15.0,
    ) -> None:
        self._cap_pct = net_exposure_cap_pct
        self._small_add_max_pct = small_add_max_pct

    def evaluate(
        self,
        ticker: str,
        entry_price: float,
        conviction: str,
        positions: list[dict[str, Any]] | None,
        portfolio_value: float = 100_000.0,
    ) -> OverlapResult:
        """Evaluate overlap policy for a single stock buy candidate.

        Args:
            ticker: Stock ticker.
            entry_price: Proposed buy price.
            conviction: Conviction level (high/medium/low/none).
            positions: Current broker positions (dicts with symbol, option_type,
                       strike, quantity fields).
            portfolio_value: Total portfolio value for exposure calculation.
        """
        has_csp, csp_strike, csp_contracts = self._find_csp(ticker, positions)
        stock_shares = self._find_stock_shares(ticker, positions)
        csp_assigned_shares = csp_contracts * 100

        total_shares = stock_shares + csp_assigned_shares
        total_value = total_shares * entry_price
        net_pct = (total_value / portfolio_value * 100) if portfolio_value > 0 else 0.0

        if not has_csp:
            return OverlapResult(
                ticker=ticker,
                decision="add_standard",
                has_active_csp=False,
                csp_strike=None,
                stock_shares_held=stock_shares,
                csp_assigned_equivalent_shares=0,
                net_exposure_pct=net_pct,
                net_exposure_cap_pct=self._cap_pct,
                reason="No active CSP — standard position",
            )

        proposed_add_value = 100 * entry_price
        new_net_pct = ((total_value + proposed_add_value) / portfolio_value * 100) if portfolio_value > 0 else 0.0

        if new_net_pct > self._cap_pct:
            return OverlapResult(
                ticker=ticker,
                decision="defer",
                has_active_csp=True,
                csp_strike=csp_strike,
                stock_shares_held=stock_shares,
                csp_assigned_equivalent_shares=csp_assigned_shares,
                net_exposure_pct=net_pct,
                net_exposure_cap_pct=self._cap_pct,
                reason=(
                    f"Adding shares would push {ticker} exposure to {new_net_pct:.1f}% "
                    f"(cap {self._cap_pct}%). Wait for CSP at ${csp_strike:.2f} to expire."
                ),
            )

        csp_distance_pct = abs(entry_price - csp_strike) / entry_price * 100 if entry_price > 0 else 0
        is_deep_otm = csp_distance_pct > 5.0
        is_high_conviction = conviction == "high"

        if is_high_conviction and is_deep_otm and new_net_pct <= self._small_add_max_pct:
            return OverlapResult(
                ticker=ticker,
                decision="add_small",
                has_active_csp=True,
                csp_strike=csp_strike,
                stock_shares_held=stock_shares,
                csp_assigned_equivalent_shares=csp_assigned_shares,
                net_exposure_pct=net_pct,
                net_exposure_cap_pct=self._cap_pct,
                reason=(
                    f"Active CSP at ${csp_strike:.2f} ({csp_distance_pct:.1f}% below entry). "
                    f"High conviction — small add OK (exposure {new_net_pct:.1f}%)."
                ),
            )

        return OverlapResult(
            ticker=ticker,
            decision="defer",
            has_active_csp=True,
            csp_strike=csp_strike,
            stock_shares_held=stock_shares,
            csp_assigned_equivalent_shares=csp_assigned_shares,
            net_exposure_pct=net_pct,
            net_exposure_cap_pct=self._cap_pct,
            reason=(
                f"Active CSP at ${csp_strike:.2f} on {ticker}. "
                f"Defer additional stock buy until CSP expires or is closed."
            ),
        )

    def _find_csp(
        self, ticker: str, positions: list[dict[str, Any]] | None,
    ) -> tuple[bool, float | None, int]:
        """Return (has_csp, nearest_strike, total_contracts)."""
        if not positions:
            return False, None, 0

        strikes: list[float] = []
        total_contracts = 0
        for pos in positions:
            sym = pos.get("symbol", "")
            opt_type = pos.get("option_type", "")
            strike = pos.get("strike")
            qty = abs(int(pos.get("quantity", 0)))
            if sym.upper() == ticker.upper() and opt_type in ("put", "short_put") and strike:
                strikes.append(float(strike))
                total_contracts += qty

        if not strikes:
            return False, None, 0
        return True, min(strikes), total_contracts

    def _find_stock_shares(
        self, ticker: str, positions: list[dict[str, Any]] | None,
    ) -> int:
        """Return total shares held in the ticker (equity positions only)."""
        if not positions:
            return 0
        total = 0
        for pos in positions:
            sym = pos.get("symbol", "")
            opt_type = pos.get("option_type", "")
            qty = int(pos.get("quantity", 0))
            if sym.upper() == ticker.upper() and not opt_type:
                total += qty
        return total
