"""Strategy engine — orchestrates scanning across watchlist and strategies."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

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


def _next_friday(from_date: date) -> date:
    """Return the next Friday on or after *from_date*."""
    days_ahead = 4 - from_date.weekday()  # Friday = 4
    if days_ahead < 0:
        days_ahead += 7
    return from_date + timedelta(days=days_ahead)


def target_expiration_dates(
    available_expirations: list[str],
    today: date | None = None,
    max_expirations: int = 1,
) -> list[str]:
    """Select target expiration dates with day-of-week awareness.

    The number of expirations fetched depends on when the scan runs:
      - Saturday → Wednesday: nearest ``max_expirations`` expiration(s)
      - Thursday or Friday: nearest ``max_expirations + 1`` expiration(s)
        (covers both the immediate and next-week expiry)

    Only future expirations are considered. The CSP DTE filter (3-14d)
    further culls anything too far out.
    """
    today = today or date.today()
    day_of_week = today.weekday()  # Mon=0 … Sun=6

    future_exps = []
    for exp_str in available_expirations:
        try:
            if date.fromisoformat(exp_str) >= today:
                future_exps.append(exp_str)
        except ValueError:
            continue

    limit = max_expirations + 1 if day_of_week in (3, 4) else max_expirations

    result = future_exps[:limit]

    logger.info(
        "expiration_targeting",
        today=today.isoformat(),
        day=today.strftime("%A"),
        max_expirations=max_expirations,
        limit=limit,
        selected=result,
        available_count=len(future_exps),
    )
    return result


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
        conviction_signals: dict[str, Any] | None = None,
        min_oi: int = 10,
        min_volume: int = 5,
        max_spread_pct: float = 15.0,
        top_n: int = 10,
        max_expirations: int = 2,
        strike_range_pct: float = 15.0,
        expiration_mode: str = "friday_target",
    ) -> list[ScoredCandidate]:
        """Scan the watchlist for CSP opportunities.

        Args:
            broker: Broker client for market data.
            watchlist: List of ticker symbols to scan.
            available_cash: Cash available for collateral.
            earnings_dates: Map of symbol -> next earnings date (or None).
            conviction_signals: EMA conviction data keyed by ticker.
            min_oi: Minimum open interest filter.
            min_volume: Minimum volume filter.
            max_spread_pct: Maximum bid-ask spread percentage.
            top_n: Return top N candidates.
            max_expirations: Max expiration dates to scan per ticker.
            strike_range_pct: Only consider strikes within this % below the 8-EMA.
            expiration_mode: "friday_target" uses smart Friday-targeting
                (Sat-Wed → this Friday, Thu-Fri → this + next Friday).
                "max_n" uses the legacy first-N expirations approach.

        Returns:
            Scored and ranked CSP candidates.
        """
        earnings_dates = earnings_dates or {}
        conviction_signals = conviction_signals or {}
        all_scored: list[ScoredCandidate] = []

        for symbol in watchlist:
            try:
                quote = await broker.get_quote(symbol)
                expirations = await broker.get_options_expirations(symbol)

                if expiration_mode == "friday_target":
                    target_exps = target_expiration_dates(
                        expirations, max_expirations=max_expirations,
                    )
                else:
                    target_exps = expirations[:max_expirations]

                sig = conviction_signals.get(symbol)
                reference_price = quote.last
                if sig and hasattr(sig, "ema_8") and sig.ema_8 > 0:
                    reference_price = sig.ema_8
                strike_floor = reference_price * (1 - strike_range_pct / 100)

                for exp_str in target_exps:
                    try:
                        chain = await broker.get_options_chain(symbol, exp_str)
                    except Exception:
                        logger.warning("chain_fetch_failed", symbol=symbol, expiration=exp_str)
                        continue

                    if chain.underlying_price == 0:
                        chain.underlying_price = quote.last

                    raw = self.csp.identify_candidates(
                        chain, quote, strike_floor=strike_floor
                    )
                    filtered = self.csp.apply_filters(raw, min_oi, min_volume, max_spread_pct)
                    scored = self.csp.score(filtered, available_cash)

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
            expiration_mode=expiration_mode,
            strike_range_pct=strike_range_pct,
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

                    if chain.underlying_price == 0:
                        chain.underlying_price = quote.last

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
