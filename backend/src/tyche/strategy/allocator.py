"""Portfolio allocator — MILP optimizer for optimal CSP + CC capital allocation.

Given a set of scored option candidates (puts and calls), available capital,
and risk constraints, solve a Mixed Integer Linear Program to maximize
risk-adjusted premium income across the portfolio.

Uses scipy.optimize.milp (HiGHS solver) with a greedy fallback.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any

import numpy as np
import structlog

from tyche.conviction.engine import ConvictionSignal
from tyche.strategy.strategies.base import ScoredCandidate

logger = structlog.get_logger()

CONVICTION_WEIGHTS = {"high": 1.0, "medium": 0.7, "low": 0.3, "none": 0.1}


@dataclass
class AllocatedTrade:
    """A single trade in the optimal allocation."""

    symbol: str
    option_type: str
    option_symbol: str
    strike: float
    expiration: date
    dte: int
    contracts: int
    bid: float
    premium_per_contract: float
    total_premium: float
    collateral: float
    annualized_return_pct: float
    score: float
    conviction: str = ""
    extension_pct: float = 0.0
    market_cap: float = 0.0
    strategy: str = ""


@dataclass
class AllocationResult:
    """Output of the portfolio optimizer."""

    trades: list[AllocatedTrade] = field(default_factory=list)
    total_premium: float = 0.0
    total_collateral: float = 0.0
    capital_utilization_pct: float = 0.0
    positions_used: int = 0
    solver_status: str = "not_run"
    available_capital: float = 0.0

    @property
    def summary(self) -> dict[str, Any]:
        return {
            "trades": len(self.trades),
            "total_premium": round(self.total_premium, 2),
            "total_collateral": round(self.total_collateral, 2),
            "capital_utilization_pct": round(self.capital_utilization_pct, 1),
            "positions_used": self.positions_used,
            "solver_status": self.solver_status,
        }


def _compute_risk_weight(
    candidate: ScoredCandidate,
    conviction_signals: dict[str, ConvictionSignal] | None = None,
    max_extension_pct: float = 3.0,
) -> float:
    """Compute a composite risk weight for a candidate.

    Combines:
    - Conviction multiplier (high=1.0, medium=0.7, low=0.3)
    - Extension proximity (closer to 8-EMA = safer)
    - Liquidity factor (open interest)
    - Assignment safety (lower |delta| = less likely to be assigned)

    Lower delta (more OTM) is preferred because the Wheel Strategy's
    primary engine is repeated premium collection with fast capital
    recycling, not assignment into shares.
    """
    conviction = "high"
    extension_pct = 0.0

    if conviction_signals and candidate.symbol in conviction_signals:
        sig = conviction_signals[candidate.symbol]
        conviction = sig.conviction_level
        extension_pct = abs(sig.price_to_8ema_pct)

    conv_w = CONVICTION_WEIGHTS.get(conviction, 0.5)

    ext_w = 1.0 - (extension_pct / max_extension_pct) * 0.3 if max_extension_pct > 0 else 1.0
    ext_w = max(0.5, min(1.0, ext_w))

    oi_threshold = 1000 if candidate.strategy == "csp" else 500
    liq_w = min(1.0, candidate.open_interest / oi_threshold)

    abs_delta = min(abs(candidate.delta), 1.0)
    delta_w = 1.0 - abs_delta * 0.6
    delta_w = max(0.4, delta_w)

    return conv_w * ext_w * liq_w * delta_w


class PortfolioAllocator:
    """MILP-based portfolio optimizer for CSP + CC trades.

    Formulation:
        Variables:
            x_i  = number of contracts for candidate i (integer >= 0)
            y_i  = binary indicator (1 if x_i > 0)

        Objective: maximize  sum(x_i * premium_i * risk_weight_i)

        Constraints:
            1. Capital:  sum(x_i * collateral_i) <= available_capital  [CSPs only]
            2. Concentration: per-symbol collateral <= max_concentration * capital
            3. Max positions: sum(y_i) <= max_positions
            4. Linking: x_i <= M * y_i
            5. Max contracts: x_i <= max_contracts_per_position
            6. CC shares: x_i <= shares_held // 100  [CCs only]
    """

    def __init__(
        self,
        max_positions: int = 8,
        max_contracts_per_position: int = 40,
        max_concentration_pct: float = 25.0,
        max_extension_pct: float = 3.0,
    ) -> None:
        self._max_positions = max_positions
        self._max_contracts = max_contracts_per_position
        self._max_conc_pct = max_concentration_pct
        self._max_ext_pct = max_extension_pct

    def optimize(
        self,
        csp_candidates: list[ScoredCandidate],
        cc_candidates: list[ScoredCandidate] | None = None,
        available_capital: float = 100_000.0,
        conviction_signals: dict[str, ConvictionSignal] | None = None,
        held_shares: dict[str, int] | None = None,
    ) -> AllocationResult:
        """Find the optimal allocation across all candidates.

        Args:
            csp_candidates: Scored CSP (put) candidates.
            cc_candidates: Scored CC (call) candidates on held shares.
            available_capital: Cash available for CSP collateral.
            conviction_signals: Conviction data for risk weighting.
            held_shares: Symbol -> shares held (for CC contract limits).

        Returns:
            AllocationResult with the optimal trade list.
        """
        cc_candidates = cc_candidates or []
        held_shares = held_shares or {}
        all_candidates = list(csp_candidates) + list(cc_candidates)

        if not all_candidates:
            return AllocationResult(
                solver_status="no_candidates",
                available_capital=available_capital,
            )

        n = len(all_candidates)

        premiums = np.array([c.premium_per_contract for c in all_candidates])
        risk_weights = np.array([
            _compute_risk_weight(c, conviction_signals, self._max_ext_pct)
            for c in all_candidates
        ])

        is_csp = np.array([c.strategy == "csp" for c in all_candidates])
        collateral_per = np.array([
            c.strike * 100 if c.strategy == "csp" else 0.0
            for c in all_candidates
        ])

        upper_bounds = np.zeros(n)
        for i, c in enumerate(all_candidates):
            if c.strategy == "csp":
                max_by_capital = int(available_capital // collateral_per[i]) if collateral_per[i] > 0 else 0
                upper_bounds[i] = min(self._max_contracts, max_by_capital)
            else:
                shares = held_shares.get(c.symbol, 0)
                upper_bounds[i] = min(self._max_contracts, shares // 100)

        try:
            result = self._solve_milp(
                all_candidates, premiums, risk_weights,
                collateral_per, is_csp, upper_bounds,
                available_capital, conviction_signals,
            )
            if result.solver_status == "optimal":
                return result
            logger.warning("milp_suboptimal", status=result.solver_status)
        except Exception:
            logger.warning("milp_solver_failed", exc_info=True)

        logger.info("falling_back_to_greedy")
        return self._solve_greedy(
            all_candidates, premiums, risk_weights,
            collateral_per, is_csp, upper_bounds,
            available_capital, conviction_signals,
        )

    def _solve_milp(
        self,
        candidates: list[ScoredCandidate],
        premiums: np.ndarray,
        risk_weights: np.ndarray,
        collateral_per: np.ndarray,
        is_csp: np.ndarray,
        upper_bounds: np.ndarray,
        available_capital: float,
        conviction_signals: dict[str, ConvictionSignal] | None = None,
    ) -> AllocationResult:
        """Solve the allocation as a Mixed Integer Linear Program."""
        from scipy.optimize import LinearConstraint, milp
        from scipy.sparse import eye as speye

        n = len(candidates)

        # Decision variables: [x_0..x_{n-1}, y_0..y_{n-1}]
        # x_i = contracts (integer), y_i = binary indicator
        num_vars = 2 * n

        # Objective: maximize sum(x_i * premium_i * risk_weight_i)
        # milp minimizes, so negate the objective
        c_obj = np.zeros(num_vars)
        c_obj[:n] = -(premiums * risk_weights)

        # Integrality: all variables are integers (x=general integer, y=binary)
        integrality = np.ones(num_vars, dtype=int)

        # Bounds
        lb = np.zeros(num_vars)
        ub = np.concatenate([upper_bounds, np.ones(n)])

        constraints = []

        # 1. Capital constraint: sum(x_i * collateral_i) <= available_capital [CSPs]
        A_cap = np.zeros(num_vars)
        A_cap[:n] = collateral_per * is_csp
        constraints.append(LinearConstraint(A_cap.reshape(1, -1), 0, available_capital))

        # 2. Concentration: for each symbol, sum(x_i * collateral_i) <= max% * capital
        max_conc_value = self._max_conc_pct / 100.0 * available_capital
        symbols = list({c.symbol for c in candidates})
        if len(symbols) > 1:
            A_conc = np.zeros((len(symbols), num_vars))
            for s_idx, sym in enumerate(symbols):
                for i, c in enumerate(candidates):
                    if c.symbol == sym:
                        A_conc[s_idx, i] = collateral_per[i]
            constraints.append(LinearConstraint(A_conc, 0, max_conc_value))

        # 3. Max positions: sum(y_i) <= max_positions
        A_pos = np.zeros(num_vars)
        A_pos[n:] = 1.0
        constraints.append(LinearConstraint(A_pos.reshape(1, -1), 0, self._max_positions))

        # 4. Linking: x_i <= M * y_i  =>  x_i - M * y_i <= 0
        A_link = np.zeros((n, num_vars))
        for i in range(n):
            A_link[i, i] = 1.0
            A_link[i, n + i] = -float(upper_bounds[i]) if upper_bounds[i] > 0 else -1.0
        constraints.append(LinearConstraint(A_link, -np.inf, 0))

        from scipy.optimize import Bounds as ScipyBounds
        bounds = ScipyBounds(lb=lb, ub=ub)

        res = milp(
            c=c_obj,
            constraints=constraints,
            integrality=integrality,
            bounds=bounds,
        )

        if not res.success:
            return AllocationResult(
                solver_status=f"failed: {res.message}",
                available_capital=available_capital,
            )

        x_vals = np.round(res.x[:n]).astype(int)
        return self._build_result(
            candidates, x_vals, collateral_per, available_capital,
            "optimal", conviction_signals,
        )

    def _solve_greedy(
        self,
        candidates: list[ScoredCandidate],
        premiums: np.ndarray,
        risk_weights: np.ndarray,
        collateral_per: np.ndarray,
        is_csp: np.ndarray,
        upper_bounds: np.ndarray,
        available_capital: float,
        conviction_signals: dict[str, ConvictionSignal] | None = None,
    ) -> AllocationResult:
        """Greedy fallback: sort by risk-adjusted premium, allocate sequentially."""
        n = len(candidates)
        adjusted_scores = premiums * risk_weights
        order = np.argsort(-adjusted_scores)

        x_vals = np.zeros(n, dtype=int)
        remaining_capital = available_capital
        positions_used = 0
        symbol_collateral: dict[str, float] = {}
        max_conc_value = self._max_conc_pct / 100.0 * available_capital

        for idx in order:
            if positions_used >= self._max_positions:
                break
            if upper_bounds[idx] <= 0:
                continue

            c = candidates[idx]
            coll = collateral_per[idx]

            if c.strategy == "csp" and coll > 0:
                sym_used = symbol_collateral.get(c.symbol, 0.0)
                max_by_conc = int((max_conc_value - sym_used) / coll) if coll > 0 else 0
                max_by_cap = int(remaining_capital / coll) if coll > 0 else 0
                contracts = min(int(upper_bounds[idx]), max_by_conc, max_by_cap)
            else:
                contracts = int(upper_bounds[idx])

            if contracts <= 0:
                continue

            x_vals[idx] = contracts
            if c.strategy == "csp":
                used = contracts * coll
                remaining_capital -= used
                symbol_collateral[c.symbol] = symbol_collateral.get(c.symbol, 0.0) + used
            positions_used += 1

        return self._build_result(
            candidates, x_vals, collateral_per, available_capital,
            "greedy_fallback", conviction_signals,
        )

    def _build_result(
        self,
        candidates: list[ScoredCandidate],
        x_vals: np.ndarray,
        collateral_per: np.ndarray,
        available_capital: float,
        solver_status: str,
        conviction_signals: dict[str, ConvictionSignal] | None,
    ) -> AllocationResult:
        """Convert solver output into an AllocationResult."""
        trades: list[AllocatedTrade] = []
        total_premium = 0.0
        total_collateral = 0.0

        for i, c in enumerate(candidates):
            contracts = int(x_vals[i])
            if contracts <= 0:
                continue

            prem = contracts * c.premium_per_contract
            coll = contracts * collateral_per[i]
            total_premium += prem
            total_collateral += coll

            conviction = ""
            ext_pct = 0.0
            if conviction_signals and c.symbol in conviction_signals:
                sig = conviction_signals[c.symbol]
                conviction = sig.conviction_level
                ext_pct = sig.price_to_8ema_pct

            trades.append(AllocatedTrade(
                symbol=c.symbol,
                option_type=c.option_type,
                option_symbol=c.option_symbol,
                strike=c.strike,
                expiration=c.expiration,
                dte=c.dte,
                contracts=contracts,
                bid=c.bid,
                premium_per_contract=c.premium_per_contract,
                total_premium=round(prem, 2),
                collateral=round(coll, 2),
                annualized_return_pct=c.annualized_return_pct,
                score=c.score,
                conviction=conviction,
                extension_pct=round(ext_pct, 2),
                strategy=c.strategy,
            ))

        trades.sort(key=lambda t: t.total_premium, reverse=True)

        util_pct = (total_collateral / available_capital * 100) if available_capital > 0 else 0.0

        return AllocationResult(
            trades=trades,
            total_premium=round(total_premium, 2),
            total_collateral=round(total_collateral, 2),
            capital_utilization_pct=round(util_pct, 1),
            positions_used=len(trades),
            solver_status=solver_status,
            available_capital=available_capital,
        )
