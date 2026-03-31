"""Cash-Secured Put (CSP) strategy — primary income engine for the wheel."""

from __future__ import annotations

from datetime import date

from tyche.broker.base import OptionContract, OptionsChain, Quote
from tyche.strategy.strategies.base import (
    FilteredCandidate,
    RawCandidate,
    ScoredCandidate,
)


class CashSecuredPutStrategy:
    """Identifies, filters, and scores CSP candidates.

    Scans put options on the watchlist for opportunities to sell
    cash-secured puts with short DTE (3-14 days by default).
    """

    name: str = "csp"

    def __init__(
        self,
        dte_min: int = 1,
        dte_max: int = 45,
        max_delta: float = -0.15,
        min_delta: float = -0.45,
    ) -> None:
        self._dte_min = dte_min
        self._dte_max = dte_max
        self._max_delta = max_delta  # OTM puts have delta closer to 0
        self._min_delta = min_delta  # Deeper ITM

    def identify_candidates(
        self, chain: OptionsChain, quote: Quote, strike_floor: float = 0.0
    ) -> list[RawCandidate]:
        """Extract put contracts in the target DTE range that are OTM or near-ATM.

        Args:
            chain: Options chain data from broker.
            quote: Current quote for the underlying.
            strike_floor: Minimum strike price (e.g. EMA-based floor).
                          Strikes below this are considered too deep OTM.
        """
        today = date.today()
        candidates: list[RawCandidate] = []

        for contract in chain.puts:
            dte = (contract.expiration - today).days
            if not (self._dte_min <= dte <= self._dte_max):
                continue

            if contract.strike >= quote.last:
                continue

            if strike_floor > 0 and contract.strike < strike_floor:
                continue

            if contract.bid <= 0:
                continue

            candidates.append(
                RawCandidate(
                    symbol=chain.symbol,
                    option_symbol=contract.option_symbol,
                    option_type="put",
                    strike=contract.strike,
                    expiration=contract.expiration,
                    dte=dte,
                    bid=contract.bid,
                    ask=contract.ask,
                    mid=contract.mid,
                    volume=contract.volume,
                    open_interest=contract.open_interest,
                    implied_volatility=contract.implied_volatility,
                    underlying_price=chain.underlying_price,
                    strategy="csp",
                    delta=contract.delta,
                    theta=contract.theta,
                    gamma=contract.gamma,
                    vega=contract.vega,
                )
            )
        return candidates

    def apply_filters(
        self,
        candidates: list[RawCandidate],
        min_oi: int = 10,
        min_volume: int = 0,
        max_spread_pct: float = 15.0,
    ) -> list[FilteredCandidate]:
        """Apply deterministic quality filters.

        Open interest is the primary liquidity signal (survives across sessions).
        Volume is optional — defaults to 0 so early-morning scans are not penalized.
        """
        filtered: list[FilteredCandidate] = []

        for c in candidates:
            filters: dict[str, bool] = {}

            filters["min_open_interest"] = c.open_interest >= min_oi
            if min_volume > 0:
                filters["min_volume"] = c.volume >= min_volume

            spread_pct = ((c.ask - c.bid) / c.mid * 100) if c.mid > 0 else 100.0
            filters["max_bid_ask_spread"] = spread_pct <= max_spread_pct

            filters["positive_bid"] = c.bid > 0

            if all(filters.values()):
                fc = FilteredCandidate(
                    **{k: getattr(c, k) for k in RawCandidate.__dataclass_fields__},
                    bid_ask_spread_pct=round(spread_pct, 2),
                    passed_filters=filters,
                )
                filtered.append(fc)
        return filtered

    def score(
        self, candidates: list[FilteredCandidate], available_cash: float
    ) -> list[ScoredCandidate]:
        """Score and rank CSP candidates by annualized return on collateral."""
        scored: list[ScoredCandidate] = []

        for c in candidates:
            collateral_per_contract = c.strike * 100
            max_contracts = int(available_cash // collateral_per_contract) if collateral_per_contract > 0 else 0
            if max_contracts <= 0:
                continue

            premium_per_contract = c.bid * 100
            total_collateral = collateral_per_contract * max_contracts
            total_premium = premium_per_contract * max_contracts

            if total_collateral > 0 and c.dte > 0:
                return_pct = (total_premium / total_collateral) * 100
                annualized = return_pct * (365 / c.dte)
            else:
                return_pct = 0.0
                annualized = 0.0

            # Composite score: annualized return weighted by liquidity
            liquidity_factor = min(1.0, c.open_interest / 1000)
            score = annualized * liquidity_factor

            sc = ScoredCandidate(
                **{k: getattr(c, k) for k in FilteredCandidate.__dataclass_fields__},
                premium_per_contract=round(premium_per_contract, 2),
                total_premium=round(total_premium, 2),
                collateral_required=round(total_collateral, 2),
                annualized_return_pct=round(annualized, 2),
                score=round(score, 4),
            )
            scored.append(sc)

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored
