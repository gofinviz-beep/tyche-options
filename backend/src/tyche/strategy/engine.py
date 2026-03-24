"""Strategy engine — orchestrates scanning across watchlist and strategies."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

import structlog

from tyche.broker.base import (
    AccountBalance,
    BrokerClient,
    BrokerPosition,
    OptionsChain,
    Quote,
)
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.strategy.strategies.cash_secured_put import CashSecuredPutStrategy
from tyche.strategy.strategies.covered_call import CoveredCallStrategy

logger = structlog.get_logger()


class StrategyEngine:
    """Orchestrates the scan-filter-score pipeline across watchlist symbols."""

    def __init__(
        self,
        csp_strategy: CashSecuredPutStrategy | None = None,
        cc_strategy: CoveredCallStrategy | None = None,
    ) -> None:
        self.csp = csp_strategy or CashSecuredPutStrategy()
        self.cc = cc_strategy or CoveredCallStrategy()

    async def scan_csp_candidates(
        self,
        broker: BrokerClient,
        watchlist: list[str],
        available_cash: float,
        earnings_dates: dict[str, date | None] | None = None,
        min_oi: int = 100,
        min_volume: int = 10,
        max_spread_pct: float = 15.0,
        top_n: int = 10,
    ) -> list[ScoredCandidate]:
        """Scan the watchlist for CSP opportunities.

        Args:
            broker: Broker client for market data.
            watchlist: List of ticker symbols to scan.
            available_cash: Cash available for collateral.
            earnings_dates: Map of symbol -> next earnings date (or None).
            min_oi: Minimum open interest filter.
            min_volume: Minimum volume filter.
            max_spread_pct: Maximum bid-ask spread percentage.
            top_n: Return top N candidates.

        Returns:
            Scored and ranked CSP candidates.
        """
        earnings_dates = earnings_dates or {}
        all_scored: list[ScoredCandidate] = []
        scan_id = str(uuid.uuid4())

        for symbol in watchlist:
            try:
                quote = await broker.get_quote(symbol)
                expirations = await broker.get_options_expirations(symbol)

                for exp_str in expirations[:4]:
                    try:
                        chain = await broker.get_options_chain(symbol, exp_str)
                    except Exception:
                        logger.warning("chain_fetch_failed", symbol=symbol, expiration=exp_str)
                        continue

                    raw = self.csp.identify_candidates(chain, quote)
                    filtered = self.csp.apply_filters(raw, min_oi, min_volume, max_spread_pct)
                    scored = self.csp.score(filtered, available_cash)

                    # Tag with earnings context
                    earnings_date = earnings_dates.get(symbol)
                    for sc in scored:
                        if earnings_date and sc.dte > 0:
                            sc.earnings_date = earnings_date
                            sc.earnings_within_dte = (
                                earnings_date <= sc.expiration
                            )

                    all_scored.extend(scored)

            except Exception:
                logger.warning("symbol_scan_failed", symbol=symbol, exc_info=True)
                continue

        all_scored.sort(key=lambda x: x.score, reverse=True)
        logger.info(
            "csp_scan_complete",
            symbols_scanned=len(watchlist),
            candidates_found=len(all_scored),
            top_n=top_n,
        )
        return all_scored[:top_n]

    async def scan_cc_candidates(
        self,
        broker: BrokerClient,
        positions: list[BrokerPosition],
        min_oi: int = 50,
        min_volume: int = 5,
        max_spread_pct: float = 20.0,
        top_n: int = 5,
    ) -> list[ScoredCandidate]:
        """Scan held share positions for covered call opportunities.

        Only considers equity positions (not option positions).
        """
        all_scored: list[ScoredCandidate] = []

        equity_positions = [
            p for p in positions
            if p.option_symbol is None and p.quantity >= 100
        ]

        for pos in equity_positions:
            try:
                quote = await broker.get_quote(pos.symbol)
                expirations = await broker.get_options_expirations(pos.symbol)

                cost_basis_per_share = (
                    pos.cost_basis / pos.quantity if pos.quantity > 0 else 0.0
                )

                for exp_str in expirations[:4]:
                    try:
                        chain = await broker.get_options_chain(pos.symbol, exp_str)
                    except Exception:
                        continue

                    raw = self.cc.identify_candidates(
                        chain, quote,
                        shares_held=int(pos.quantity),
                        cost_basis_per_share=cost_basis_per_share,
                    )
                    filtered = self.cc.apply_filters(raw, min_oi, min_volume, max_spread_pct)
                    scored = self.cc.score(
                        filtered,
                        shares_held=int(pos.quantity),
                        cost_basis_per_share=cost_basis_per_share,
                    )
                    all_scored.extend(scored)

            except Exception:
                logger.warning("cc_scan_failed", symbol=pos.symbol, exc_info=True)
                continue

        all_scored.sort(key=lambda x: x.score, reverse=True)
        logger.info(
            "cc_scan_complete",
            positions_scanned=len(equity_positions),
            candidates_found=len(all_scored),
        )
        return all_scored[:top_n]
