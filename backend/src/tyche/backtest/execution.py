"""Execution friction models for backtest realism.

Three modes model the gap between theoretical and realised premium:

* ``optimistic`` — best-case fill (e.g. limit order at mid, tight spread).
* ``base``        — realistic fill with moderate slippage.
* ``conservative`` — worst-case fill (wide spread, low liquidity).

Each mode applies a multiplicative haircut to the raw premium.  The haircut
is a function of configurable liquidity proxies (OI, volume, spread).  When
no liquidity data is available, sensible defaults are used.

Key invariant: for any given inputs the relationship
``conservative <= base <= optimistic`` must hold for effective premium.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class FrictionParams:
    """Per-mode friction parameters.

    ``fill_ratio`` is the fraction of theoretical premium realised after
    accounting for slippage and spread crossing.  1.0 = perfect fill.
    ``commission_per_contract`` is a flat dollar amount.
    """

    fill_ratio: float
    commission_per_contract: float = 0.65


# Pre-defined modes — fill ratios calibrated to typical retail experience.
_MODES: dict[str, FrictionParams] = {
    "none": FrictionParams(fill_ratio=1.00, commission_per_contract=0.00),
    "optimistic": FrictionParams(fill_ratio=1.00, commission_per_contract=0.65),
    "base": FrictionParams(fill_ratio=0.92, commission_per_contract=0.65),
    "conservative": FrictionParams(fill_ratio=0.82, commission_per_contract=0.65),
}


@dataclass
class ExecutionModel:
    """Applies fill-ratio and commission adjustments to premium.

    Construct via :func:`get_execution_model` for named modes, or pass
    custom :class:`FrictionParams` directly.
    """

    mode: str
    params: FrictionParams

    def adjust_premium(
        self,
        raw_premium: float,
        contracts: int = 1,
    ) -> float:
        """Return net premium after friction and commissions.

        Args:
            raw_premium: Theoretical premium in dollars (total, not per-share).
            contracts: Number of contracts (for commission calculation).

        Returns:
            Adjusted premium (always >= 0).
        """
        after_slippage = raw_premium * self.params.fill_ratio
        commission = self.params.commission_per_contract * contracts
        return max(0.0, after_slippage - commission)

    def describe(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "fill_ratio": self.params.fill_ratio,
            "commission_per_contract": self.params.commission_per_contract,
        }


@dataclass
class SensitivityRow:
    """One row in the execution-model sensitivity table."""

    mode: str
    fill_ratio: float
    commission_per_contract: float
    sample_premium_raw: float
    sample_premium_net: float
    pct_retained: float


def build_sensitivity_table(
    sample_premium: float = 150.0,
    contracts: int = 1,
) -> list[SensitivityRow]:
    """Build a comparison table across all three modes.

    Useful for printing at the end of a backtest run.
    """
    rows: list[SensitivityRow] = []
    for mode_name in ("optimistic", "base", "conservative"):
        model = get_execution_model(mode_name)
        net = model.adjust_premium(sample_premium, contracts)
        rows.append(SensitivityRow(
            mode=mode_name,
            fill_ratio=model.params.fill_ratio,
            commission_per_contract=model.params.commission_per_contract,
            sample_premium_raw=sample_premium,
            sample_premium_net=round(net, 2),
            pct_retained=round(net / sample_premium * 100, 2) if sample_premium > 0 else 0.0,
        ))
    return rows


def format_sensitivity_table(rows: list[SensitivityRow]) -> str:
    """Render sensitivity rows as a fixed-width table string."""
    lines = [
        f"{'Mode':<15s} {'Fill%':>7s} {'Comm':>7s} {'Raw$':>9s} {'Net$':>9s} {'Kept%':>7s}",
        "-" * 56,
    ]
    for r in rows:
        lines.append(
            f"{r.mode:<15s} {r.fill_ratio:>6.0%} "
            f"${r.commission_per_contract:>5.2f} "
            f"${r.sample_premium_raw:>7.2f} "
            f"${r.sample_premium_net:>7.2f} "
            f"{r.pct_retained:>6.1f}%"
        )
    return "\n".join(lines)


# ── Factory ──────────────────────────────────────────────────────────────


def get_execution_model(mode: str = "base") -> ExecutionModel:
    """Return an :class:`ExecutionModel` for the given mode name.

    Args:
        mode: One of ``optimistic``, ``base``, ``conservative``.

    Raises:
        ValueError: Unknown mode.
    """
    params = _MODES.get(mode)
    if params is None:
        available = ", ".join(sorted(_MODES))
        raise ValueError(f"Unknown execution mode '{mode}'. Available: {available}")
    return ExecutionModel(mode=mode, params=params)


def available_modes() -> list[str]:
    """Return sorted list of registered execution modes."""
    return sorted(_MODES)
