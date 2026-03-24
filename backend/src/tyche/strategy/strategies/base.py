"""Strategy protocol and shared types."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Protocol

from tyche.broker.base import OptionsChain, Quote


@dataclass
class RawCandidate:
    """A potential trade identified by a strategy before filtering."""

    symbol: str
    option_symbol: str
    option_type: str  # put, call
    strike: float
    expiration: date
    dte: int
    bid: float
    ask: float
    mid: float
    volume: int
    open_interest: int
    implied_volatility: float
    underlying_price: float
    strategy: str  # csp, covered_call, long_call, long_put

    # Greeks
    delta: float = 0.0
    theta: float = 0.0
    gamma: float = 0.0
    vega: float = 0.0


@dataclass
class FilteredCandidate(RawCandidate):
    """A candidate that passed all deterministic filters."""

    bid_ask_spread_pct: float = 0.0
    passed_filters: dict[str, bool] = field(default_factory=dict)


@dataclass
class ScoredCandidate(FilteredCandidate):
    """A candidate with a deterministic score for ranking."""

    premium_per_contract: float = 0.0
    total_premium: float = 0.0
    collateral_required: float = 0.0
    annualized_return_pct: float = 0.0
    score: float = 0.0

    # Earnings context (populated later)
    earnings_within_dte: bool = False
    earnings_date: date | None = None


class StrategyProtocol(Protocol):
    """Protocol that all strategy implementations must follow."""

    name: str

    def identify_candidates(
        self, chain: OptionsChain, quote: Quote
    ) -> list[RawCandidate]: ...

    def apply_filters(
        self, candidates: list[RawCandidate], min_oi: int, min_volume: int, max_spread_pct: float
    ) -> list[FilteredCandidate]: ...

    def score(
        self, candidates: list[FilteredCandidate], available_cash: float
    ) -> list[ScoredCandidate]: ...
