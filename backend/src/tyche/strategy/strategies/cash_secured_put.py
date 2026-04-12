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
        min_oi: int = 50,
        min_volume: int = 10,
        max_spread_pct: float = 15.0,
        min_bid: float = 0.50,
        min_premium_pct: float = 0.5,
    ) -> list[FilteredCandidate]:
        """Apply deterministic quality filters.

        Args:
            min_oi: Minimum open interest (liquidity signal across sessions).
            min_volume: Minimum daily volume. 0 disables the check.
            max_spread_pct: Maximum bid-ask spread as pct of midpoint.
            min_bid: Minimum bid price per share (e.g. 0.50 = $50/contract).
            min_premium_pct: Minimum bid/strike as pct (premium-to-collateral floor).
        """
        filtered: list[FilteredCandidate] = []

        for c in candidates:
            filters: dict[str, bool] = {}

            filters["min_open_interest"] = c.open_interest >= min_oi
            if min_volume > 0:
                filters["min_volume"] = c.volume >= min_volume

            spread_pct = ((c.ask - c.bid) / c.mid * 100) if c.mid > 0 else 100.0
            filters["max_bid_ask_spread"] = spread_pct <= max_spread_pct

            filters["min_bid"] = c.bid >= min_bid

            if c.strike > 0:
                premium_pct = (c.bid / c.strike) * 100
            else:
                premium_pct = 0.0
            filters["min_premium_pct"] = premium_pct >= min_premium_pct

            if all(filters.values()):
                fc = FilteredCandidate(
                    **{k: getattr(c, k) for k in RawCandidate.__dataclass_fields__},
                    bid_ask_spread_pct=round(spread_pct, 2),
                    passed_filters=filters,
                )
                filtered.append(fc)
        return filtered

    # Backtest-derived day-of-week multipliers for CSP entry quality.
    # Tuesday/Wednesday are optimal; Thursday/Friday are weakest.
    _DOW_FACTORS: dict[int, float] = {
        0: 0.85,  # Monday
        1: 1.00,  # Tuesday  (optimal)
        2: 1.00,  # Wednesday (optimal)
        3: 0.70,  # Thursday
        4: 0.70,  # Friday
    }

    def score(
        self,
        candidates: list[FilteredCandidate],
        available_cash: float,
        vrp_map: dict[str, float] | None = None,
        iv_rank_map: dict[str, float] | None = None,
        trend_confirm_map: dict[str, bool] | None = None,
        rsi_map: dict[str, float] | None = None,
        earnings_within_dte_set: set[str] | None = None,
        scan_date: date | None = None,
    ) -> list[ScoredCandidate]:
        """Score and rank CSP candidates by risk-adjusted premium quality.

        Factors beyond annualized return:
        - **liquidity_factor**: scales to 1.0 at OI >= 1000
        - **dte_factor**: penalizes < 7 DTE (3 DTE -> 0.43x, 7+ -> 1.0x)
        - **vrp_factor**: up to 30% bonus for positive VRP tickers
        - **iv_rank_factor**: 0.7 at rank 0, 1.0 at rank 60-85, drops to 0.8 at 100
        - **trend_confirm_factor**: 0.85 penalty when price < 50-EMA
        - **rsi_factor**: 0.7 penalty when RSI > 70 (overbought mean-reversion risk)
        - **earnings_factor**: 0.5 penalty when earnings fall within DTE
        - **dow_factor**: day-of-week penalty (Mon 0.85x, Thu/Fri 0.70x, Tue/Wed 1.0x)
        - **iv_catalyst_factor**: 0.5 penalty when IV Rank > 80 AND earnings within DTE
        """
        scored: list[ScoredCandidate] = []
        vrp_map = vrp_map or {}
        iv_rank_map = iv_rank_map or {}
        trend_confirm_map = trend_confirm_map or {}
        rsi_map = rsi_map or {}
        earnings_within_dte_set = earnings_within_dte_set or set()

        effective_date = scan_date or date.today()
        dow_factor = self._DOW_FACTORS.get(effective_date.weekday(), 1.0)

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

            liquidity_factor = min(1.0, c.open_interest / 1000)
            dte_factor = min(1.0, c.dte / 7)
            vrp = vrp_map.get(c.symbol, 0.0)
            vrp_factor = 1.0 + min(0.3, max(0.0, vrp) * 1.0)

            iv_rank = iv_rank_map.get(c.symbol)
            if iv_rank is not None:
                if iv_rank <= 60:
                    iv_rank_factor = 0.7 + 0.3 * (iv_rank / 60)
                elif iv_rank <= 85:
                    iv_rank_factor = 1.0
                else:
                    # IV Rank > 85: event-driven spike risk — linearly penalize
                    # 85 → 1.0, 100 → 0.8
                    iv_rank_factor = 1.0 - (iv_rank - 85) * (0.2 / 15)
            else:
                iv_rank_factor = 1.0

            above_50ema = trend_confirm_map.get(c.symbol)
            trend_confirm_factor = 1.0 if above_50ema is None else (1.0 if above_50ema else 0.85)

            rsi = rsi_map.get(c.symbol)
            rsi_factor = 0.7 if (rsi is not None and rsi > 70) else 1.0

            has_earnings = c.symbol in earnings_within_dte_set
            earnings_factor = 0.5 if has_earnings else 1.0

            # Compound penalty: high IV + earnings = likely IV trap
            iv_catalyst_factor = 1.0
            if has_earnings and iv_rank is not None and iv_rank > 80:
                iv_catalyst_factor = 0.5

            score = annualized * liquidity_factor * dte_factor * vrp_factor * iv_rank_factor * trend_confirm_factor * rsi_factor * earnings_factor * dow_factor * iv_catalyst_factor

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
