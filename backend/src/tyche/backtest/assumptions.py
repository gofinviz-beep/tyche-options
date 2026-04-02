"""Assumptions audit: centralised summary of all backtest parameters.

Emits a structured assumptions block at the start of every backtest run
so that reviewers can verify what constants drove the results.  Also
supports a standalone ``--print-assumptions`` mode.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from tyche.backtest.execution import ExecutionModel
from tyche.backtest.premium import PremiumModel


@dataclass
class BacktestAssumptions:
    """Snapshot of every assumption that affects backtest outcomes."""

    # Identity
    script_name: str

    # Universe filters
    min_market_cap: float
    min_price: float
    min_volume: int
    valid_exchanges: list[str]

    # Trade parameters
    dte: int
    otm_pct: float | None = None
    strike_offsets: list[float] | None = None

    # Premium model
    premium_model: dict[str, Any] = field(default_factory=dict)

    # Execution model
    execution_model: dict[str, Any] = field(default_factory=dict)

    # Conviction / trend filters
    ema_fast: int = 8
    ema_slow: int = 21
    max_extension_pct: float = 3.0
    min_days_above_emas: int = 5
    max_days_above_emas: int = 10
    min_prior_streak: int | None = None

    # Capital simulation
    starting_capital: float | None = None
    max_positions: int | None = None
    max_concentration_pct: float | None = None

    # Walk-forward
    walk_forward_enabled: bool = False
    train_days: int | None = None
    test_days: int | None = None

    # Extras (script-specific overrides)
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serialisable dictionary of all assumptions."""
        d: dict[str, Any] = {}
        d["script"] = self.script_name

        d["universe_filters"] = {
            "min_market_cap": self.min_market_cap,
            "min_market_cap_label": _format_cap(self.min_market_cap),
            "min_price": self.min_price,
            "min_volume": self.min_volume,
            "valid_exchanges": self.valid_exchanges,
        }

        trade: dict[str, Any] = {"dte": self.dte}
        if self.otm_pct is not None:
            trade["otm_pct"] = self.otm_pct
        if self.strike_offsets is not None:
            trade["strike_offsets"] = self.strike_offsets
        d["trade_parameters"] = trade

        d["premium_model"] = self.premium_model
        d["execution_model"] = self.execution_model

        d["conviction_filters"] = {
            "ema_fast": self.ema_fast,
            "ema_slow": self.ema_slow,
            "max_extension_pct": self.max_extension_pct,
            "min_days_above_emas": self.min_days_above_emas,
            "max_days_above_emas": self.max_days_above_emas,
        }
        if self.min_prior_streak is not None:
            d["conviction_filters"]["min_prior_streak"] = self.min_prior_streak

        if self.starting_capital is not None:
            d["capital_simulation"] = {
                "starting_capital": self.starting_capital,
                "max_positions": self.max_positions,
                "max_concentration_pct": self.max_concentration_pct,
            }

        if self.walk_forward_enabled:
            d["walk_forward"] = {
                "enabled": True,
                "train_days": self.train_days,
                "test_days": self.test_days,
            }

        if self.extra:
            d["extra"] = self.extra

        return d

    def print_summary(self) -> None:
        """Print a human-readable assumptions block to stdout."""
        d = self.to_dict()
        border = "=" * 70
        print(f"\n{border}")
        print("BACKTEST ASSUMPTIONS")
        print(border)
        _print_section("Script", d["script"])
        _print_section("Universe Filters", d["universe_filters"])
        _print_section("Trade Parameters", d["trade_parameters"])
        _print_section("Premium Model", d["premium_model"])
        _print_section("Execution Model", d["execution_model"])
        _print_section("Conviction Filters", d["conviction_filters"])
        if "capital_simulation" in d:
            _print_section("Capital Simulation", d["capital_simulation"])
        if "walk_forward" in d:
            _print_section("Walk-Forward", d["walk_forward"])
        if "extra" in d:
            _print_section("Extra", d["extra"])
        print(border + "\n")

    def to_json(self, indent: int = 2) -> str:
        """Serialise to a JSON string."""
        return json.dumps(self.to_dict(), indent=indent, default=str)


def build_assumptions(
    script_name: str,
    *,
    min_market_cap: float,
    min_price: float,
    min_volume: int,
    valid_exchanges: list[str],
    dte: int,
    premium_model: PremiumModel,
    execution_model: ExecutionModel,
    otm_pct: float | None = None,
    strike_offsets: list[float] | None = None,
    ema_fast: int = 8,
    ema_slow: int = 21,
    max_extension_pct: float = 3.0,
    min_days_above_emas: int = 5,
    max_days_above_emas: int = 10,
    min_prior_streak: int | None = None,
    starting_capital: float | None = None,
    max_positions: int | None = None,
    max_concentration_pct: float | None = None,
    walk_forward_enabled: bool = False,
    train_days: int | None = None,
    test_days: int | None = None,
    **extra: Any,
) -> BacktestAssumptions:
    """Convenience builder that accepts raw values + model objects."""
    return BacktestAssumptions(
        script_name=script_name,
        min_market_cap=min_market_cap,
        min_price=min_price,
        min_volume=min_volume,
        valid_exchanges=valid_exchanges,
        dte=dte,
        otm_pct=otm_pct,
        strike_offsets=strike_offsets,
        premium_model=premium_model.describe(),
        execution_model=execution_model.describe(),
        ema_fast=ema_fast,
        ema_slow=ema_slow,
        max_extension_pct=max_extension_pct,
        min_days_above_emas=min_days_above_emas,
        max_days_above_emas=max_days_above_emas,
        min_prior_streak=min_prior_streak,
        starting_capital=starting_capital,
        max_positions=max_positions,
        max_concentration_pct=max_concentration_pct,
        walk_forward_enabled=walk_forward_enabled,
        train_days=train_days,
        test_days=test_days,
        extra=extra if extra else {},
    )


def _format_cap(value: float) -> str:
    """Human-readable market cap label."""
    if value >= 1e9:
        return f"${value / 1e9:.0f}B"
    if value >= 1e6:
        return f"${value / 1e6:.0f}M"
    return f"${value:,.0f}"


def _print_section(label: str, data: Any) -> None:
    """Print one section of the assumptions block."""
    if isinstance(data, dict):
        print(f"\n  {label}:")
        for k, v in data.items():
            if isinstance(v, list):
                print(f"    {k}: {', '.join(str(x) for x in v)}")
            elif isinstance(v, float):
                print(f"    {k}: {v:g}")
            else:
                print(f"    {k}: {v}")
    else:
        print(f"\n  {label}: {data}")
