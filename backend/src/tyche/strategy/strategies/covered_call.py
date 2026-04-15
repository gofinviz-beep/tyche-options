"""Covered Call (CC) strategies — wheel leg and deep-dip recovery overlay.

Two strategy classes:

  CoveredCallStrategy — standard wheel CC on assigned shares (short DTE,
      strike above cost basis).

  RecoveryCoveredCallStrategy — deep-dip CC overlay on shares bought at
      oversold EMAs. Uses 21-EMA as the strike ceiling (don't sell upside
      past recovery target), longer DTE (14–45d), and a proximity bonus
      for strikes near the 21-EMA recovery target.
"""

from __future__ import annotations

from datetime import date

from tyche.broker.base import OptionsChain, Quote
from tyche.strategy.strategies.base import (
    FilteredCandidate,
    RawCandidate,
    ScoredCandidate,
)


class CoveredCallStrategy:
    """Identifies, filters, and scores covered call candidates.

    Only applicable when holding shares of the underlying. Scans call
    options at or above cost basis to generate income while holding.
    """

    name: str = "covered_call"

    def __init__(
        self,
        dte_min: int = 3,
        dte_max: int = 14,
    ) -> None:
        self._dte_min = dte_min
        self._dte_max = dte_max

    def identify_candidates(
        self,
        chain: OptionsChain,
        quote: Quote,
        shares_held: int = 100,
        cost_basis_per_share: float = 0.0,
    ) -> list[RawCandidate]:
        """Extract call contracts for covered call writing.

        Args:
            chain: Options chain for the underlying.
            quote: Current quote.
            shares_held: Number of shares held.
            cost_basis_per_share: Average cost per share (to avoid selling below basis).
        """
        today = date.today()
        max_contracts = shares_held // 100
        if max_contracts <= 0:
            return []

        candidates: list[RawCandidate] = []
        for contract in chain.calls:
            dte = (contract.expiration - today).days
            if not (self._dte_min <= dte <= self._dte_max):
                continue

            # CC targets OTM calls (strike above current price)
            if contract.strike <= quote.last:
                continue

            # Prefer strikes above cost basis so being called away is profitable
            if cost_basis_per_share > 0 and contract.strike < cost_basis_per_share:
                continue

            if contract.bid <= 0:
                continue

            candidates.append(
                RawCandidate(
                    symbol=chain.symbol,
                    option_symbol=contract.option_symbol,
                    option_type="call",
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
                    strategy="covered_call",
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
        min_oi: int = 50,
        min_volume: int = 5,
        max_spread_pct: float = 20.0,
    ) -> list[FilteredCandidate]:
        filtered: list[FilteredCandidate] = []
        for c in candidates:
            filters: dict[str, bool] = {}
            filters["min_open_interest"] = c.open_interest >= min_oi
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
        self,
        candidates: list[FilteredCandidate],
        shares_held: int = 100,
        cost_basis_per_share: float = 0.0,
    ) -> list[ScoredCandidate]:
        """Score CC candidates by premium yield and called-away profit."""
        max_contracts = shares_held // 100
        scored: list[ScoredCandidate] = []

        for c in candidates:
            contracts = min(max_contracts, 1)
            premium_per_contract = c.bid * 100
            total_premium = premium_per_contract * contracts
            shares_value = c.underlying_price * 100 * contracts

            if shares_value > 0 and c.dte > 0:
                return_pct = (total_premium / shares_value) * 100
                annualized = return_pct * (365 / c.dte)
            else:
                annualized = 0.0

            called_away_profit = 0.0
            if cost_basis_per_share > 0:
                called_away_profit = (
                    (c.strike - cost_basis_per_share) * 100 * contracts
                    + total_premium
                )

            liquidity_factor = min(1.0, c.open_interest / 500)
            score = annualized * liquidity_factor

            sc = ScoredCandidate(
                **{k: getattr(c, k) for k in FilteredCandidate.__dataclass_fields__},
                premium_per_contract=round(premium_per_contract, 2),
                total_premium=round(total_premium, 2),
                collateral_required=round(shares_value, 2),
                annualized_return_pct=round(annualized, 2),
                score=round(score, 4),
            )
            scored.append(sc)

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored


class RecoveryCoveredCallStrategy:
    """Covered call strategy optimized for deep-dip recovery plays.

    When shares are bought at an oversold dip below the 21-EMA or 50-EMA,
    this strategy sells calls with the 21-EMA as the strike ceiling —
    capturing premium during the recovery while preserving upside to the
    mean-reversion target.

    Key differences from standard CoveredCallStrategy:
      - Longer DTE (14-45d) to match recovery timelines.
      - Strike ceiling at 21-EMA (or a configurable % above entry).
      - Proximity bonus: strikes near the 21-EMA recovery target score higher.
      - Does not require strikes above cost basis — the deep dip entry is
        the cost basis, and we accept being called away at the EMA.
    """

    name: str = "recovery_covered_call"

    def __init__(
        self,
        dte_min: int = 14,
        dte_max: int = 45,
        min_strike_above_entry_pct: float = 2.0,
        max_strike_above_entry_pct: float = 10.0,
    ) -> None:
        self._dte_min = dte_min
        self._dte_max = dte_max
        self._min_strike_pct = min_strike_above_entry_pct
        self._max_strike_pct = max_strike_above_entry_pct

    def identify_candidates(
        self,
        chain: OptionsChain,
        quote: Quote,
        shares_held: int = 100,
        cost_basis_per_share: float = 0.0,
        ema_21: float = 0.0,
    ) -> list[RawCandidate]:
        """Extract call contracts suited for recovery CC overlay.

        Args:
            chain: Options chain for the underlying.
            quote: Current quote.
            shares_held: Number of shares held.
            cost_basis_per_share: Average cost per share (deep dip entry price).
            ema_21: Current 21-EMA value — used as the strike ceiling
                (recovery target). If 0, falls back to % above entry.
        """
        today = date.today()
        max_contracts = shares_held // 100
        if max_contracts <= 0:
            return []

        ref_price = cost_basis_per_share if cost_basis_per_share > 0 else quote.last
        strike_floor = ref_price * (1 + self._min_strike_pct / 100)

        if ema_21 > 0:
            strike_ceiling = ema_21
        else:
            strike_ceiling = ref_price * (1 + self._max_strike_pct / 100)

        if strike_ceiling < strike_floor:
            strike_ceiling = strike_floor * 1.01

        candidates: list[RawCandidate] = []
        for contract in chain.calls:
            dte = (contract.expiration - today).days
            if not (self._dte_min <= dte <= self._dte_max):
                continue

            if contract.strike <= quote.last:
                continue

            if contract.strike < strike_floor or contract.strike > strike_ceiling:
                continue

            if contract.bid <= 0:
                continue

            candidates.append(
                RawCandidate(
                    symbol=chain.symbol,
                    option_symbol=contract.option_symbol,
                    option_type="call",
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
                    strategy="recovery_covered_call",
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
        min_oi: int = 50,
        min_volume: int = 5,
        max_spread_pct: float = 20.0,
    ) -> list[FilteredCandidate]:
        filtered: list[FilteredCandidate] = []
        for c in candidates:
            filters: dict[str, bool] = {}
            filters["min_open_interest"] = c.open_interest >= min_oi
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
        self,
        candidates: list[FilteredCandidate],
        shares_held: int = 100,
        cost_basis_per_share: float = 0.0,
        ema_21: float = 0.0,
    ) -> list[ScoredCandidate]:
        """Score recovery CC candidates with proximity-to-EMA bonus.

        Candidates closer to the 21-EMA recovery target get a bonus because
        being called away near the EMA means full recovery achieved. The
        dual return (stock appreciation + premium) is reflected in the score.
        """
        max_contracts = shares_held // 100
        scored: list[ScoredCandidate] = []

        for c in candidates:
            contracts = min(max_contracts, 1)
            premium_per_contract = c.bid * 100
            total_premium = premium_per_contract * contracts
            shares_value = c.underlying_price * 100 * contracts

            if shares_value > 0 and c.dte > 0:
                return_pct = (total_premium / shares_value) * 100
                annualized = return_pct * (365 / c.dte)
            else:
                annualized = 0.0

            called_away_profit = 0.0
            if cost_basis_per_share > 0:
                called_away_profit = (
                    (c.strike - cost_basis_per_share) * 100 * contracts
                    + total_premium
                )

            liquidity_factor = min(1.0, c.open_interest / 500)

            proximity_factor = 1.0
            if ema_21 > 0 and c.strike > 0:
                dist_to_ema_pct = abs(c.strike - ema_21) / ema_21 * 100
                proximity_factor = max(0.5, 1.0 - dist_to_ema_pct / 20)

            dte_factor = min(1.0, c.dte / 14)

            score = annualized * liquidity_factor * proximity_factor * dte_factor

            sc = ScoredCandidate(
                **{k: getattr(c, k) for k in FilteredCandidate.__dataclass_fields__},
                premium_per_contract=round(premium_per_contract, 2),
                total_premium=round(total_premium, 2),
                collateral_required=round(shares_value, 2),
                annualized_return_pct=round(annualized, 2),
                score=round(score, 4),
            )
            scored.append(sc)

        scored.sort(key=lambda x: x.score, reverse=True)
        return scored
