"""Strategy engine — orchestrates scanning across watchlist and strategies."""

from __future__ import annotations

import asyncio
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any

import structlog

from tyche.broker.base import (
    BrokerClient,
    BrokerPosition,
    OptionsChain,
    Quote,
)
from tyche.strategy.ranking import RankingWeights, rank_candidates
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.market_data.economic_calendar import EconomicCalendar
from tyche.strategy.strategies.cash_secured_put import CashSecuredPutStrategy
from tyche.strategy.strategies.covered_call import CoveredCallStrategy
from tyche.telemetry import csp_scan_candidates_found, csp_scan_drops

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
    max_expirations: int = 2,
    min_dte: int = 5,
    target_dte: int = 14,
) -> list[str]:
    """Pick the most useful expiration date(s) from Tradier's list.

    Enforces a minimum DTE floor (default 5 — never target < 5 DTE)
    and prefers expirations closest to ``target_dte`` (default 14).
    When ``max_expirations > 1``, returns the N expirations sorted by
    proximity to the sweet spot rather than purely nearest-first.

    Returns at most ``max_expirations`` dates.
    """
    today = today or date.today()

    valid: list[tuple[str, int]] = []
    for exp_str in available_expirations:
        try:
            exp_date = date.fromisoformat(exp_str)
            dte = (exp_date - today).days
            if dte >= min_dte:
                valid.append((exp_str, dte))
        except ValueError:
            continue

    valid.sort(key=lambda t: abs(t[1] - target_dte))

    result = [exp_str for exp_str, _ in valid[:max_expirations]]

    logger.info(
        "expiration_targeting",
        today=today.isoformat(),
        day=today.strftime("%A"),
        min_dte=min_dte,
        target_dte=target_dte,
        selected=result,
        available_in_range=len(valid),
    )
    return result


def _apply_21ema_strike_bonus(
    candidates: list[ScoredCandidate],
    conviction_signals: dict[str, Any],
) -> list[ScoredCandidate]:
    """Boost scores for strikes near the 21-EMA — the ideal assignment level.

    Strikes within 3% of the 21-EMA get up to a 20% score bonus. This aligns
    CSP assignment prices with the institutional defense zone, so if assigned,
    the user buys at the price where they'd want to own the stock anyway.
    """
    for sc in candidates:
        sig = conviction_signals.get(sc.symbol)
        if sig is None:
            continue

        ema_21 = getattr(sig, "ema_21", 0) if hasattr(sig, "ema_21") else 0
        if ema_21 <= 0:
            continue

        distance_pct = abs(sc.strike - ema_21) / ema_21 * 100
        if distance_pct <= 3.0:
            bonus = 1.0 + (0.20 * (1.0 - distance_pct / 3.0))
            sc.score = round(sc.score * bonus, 4)

    return candidates


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
        min_oi: int = 50,
        min_volume: int = 10,
        max_spread_pct: float = 15.0,
        min_bid: float = 0.50,
        min_premium_pct: float = 0.5,
        top_n: int = 10,
        max_expirations: int = 2,
        strike_range_pct: float = 15.0,
        expiration_mode: str = "friday_target",
        csp_strike_preference: str = "legacy",
        pullback_strike_offset_pct: float = 5.0,
        pullback_strike_ceiling_pct: float = 1.0,
        earliest_expiration_only: bool = False,
        min_scan_dte: int = 5,
        target_dte_sweet_spot: int = 14,
        ranking_mode: str = "legacy",
        ranking_weights: RankingWeights | None = None,
        pre_allocator_pool_size: int = 0,
        economic_calendar: EconomicCalendar | None = None,
    ) -> tuple[list[ScoredCandidate], dict[str, int]]:
        """Scan the watchlist for CSP opportunities.

        Strike ranges are path-specific:

        **Path B (pullback)**:
            Floor  = support_ema × (1 − pullback_strike_offset_pct/100)
            Ceiling = support_ema × (1 − pullback_strike_ceiling_pct/100)
            e.g. 5 % below → 1 % below the support EMA.

        **Path A (uptrend)**:
            Floor  = quote.last × (1 − strike_range_pct/100)   (15 % below)
            Ceiling = ema_8   (at the support level — ideal assignment price)

        After collecting all candidates, if ``earliest_expiration_only``
        is True the set is narrowed to only the single earliest expiration
        date across all tickers, maximizing capital recycling speed.

        Returns:
            Tuple of (scored candidates, diagnostic drop counts).
        """
        earnings_dates = earnings_dates or {}
        conviction_signals = conviction_signals or {}
        all_scored: list[ScoredCandidate] = []

        drops: dict[str, int] = {
            "api_error": 0,
            "no_expirations": 0,
            "no_target_exps": 0,
            "chain_fetch_failed": 0,
            "empty_chain": 0,
            "no_puts_in_range": 0,
            "reject_dte": 0,
            "reject_itm": 0,
            "reject_strike_floor": 0,
            "reject_bid_zero": 0,
            "ceiling_filtered_all": 0,
            "quality_filtered_all": 0,
            "insufficient_capital": 0,
            "symbols_with_candidates": 0,
        }

        vrp_map: dict[str, float] = {}
        iv_rank_map: dict[str, float] = {}
        trend_confirm_map: dict[str, bool] = {}
        rsi_map: dict[str, float] = {}
        csp_safety_map: dict[str, float] = {}
        if conviction_signals:
            for sym, sig in conviction_signals.items():
                v = getattr(sig, "vrp", None)
                if v is not None:
                    vrp_map[sym] = v
                ir = getattr(sig, "iv_rank", None)
                if ir is not None:
                    iv_rank_map[sym] = ir
                ema50 = getattr(sig, "ema_50", 0.0)
                lc = getattr(sig, "last_close", 0.0)
                if ema50 > 0 and lc > 0:
                    trend_confirm_map[sym] = lc >= ema50
                rsi = getattr(sig, "rsi_14", None)
                if rsi is not None:
                    rsi_map[sym] = rsi
                csp_prob = getattr(sig, "csp_safety_prob", None)
                if csp_prob is not None:
                    csp_safety_map[sym] = csp_prob

        semaphore = asyncio.Semaphore(10)

        async def _scan_symbol(
            symbol: str,
        ) -> tuple[list[ScoredCandidate], dict[str, int]]:
            """Scan a single symbol — runs under semaphore for parallelism."""
            local_drops: dict[str, int] = {}
            scored_out: list[ScoredCandidate] = []

            async with semaphore:
                quote = await broker.get_quote(symbol)

                if quote.last <= 0:
                    quote_last_fallback = quote.close if quote.close > 0 else quote.bid
                    if quote_last_fallback > 0:
                        logger.warning(
                            "quote_last_zero_fallback",
                            symbol=symbol,
                            fallback=quote_last_fallback,
                        )
                        quote = Quote(
                            symbol=quote.symbol,
                            last=quote_last_fallback,
                            bid=quote.bid,
                            ask=quote.ask,
                            high=quote.high,
                            low=quote.low,
                            open=quote.open,
                            close=quote.close,
                            volume=quote.volume,
                            change=quote.change,
                            change_pct=quote.change_pct,
                        )

                expirations = await broker.get_options_expirations(symbol)
                if not expirations:
                    local_drops["no_expirations"] = 1
                    return scored_out, local_drops

                if expiration_mode == "friday_target":
                    target_exps = target_expiration_dates(
                        expirations,
                        max_expirations=max_expirations,
                        min_dte=min_scan_dte,
                        target_dte=target_dte_sweet_spot,
                    )
                else:
                    target_exps = expirations[:max_expirations]

                if not target_exps:
                    local_drops["no_target_exps"] = 1
                    return scored_out, local_drops

                sig = conviction_signals.get(symbol)
                is_pullback = sig and hasattr(sig, "trend_state") and sig.trend_state in (
                    "pullback_to_8ema", "pullback_to_21ema",
                )

                if is_pullback and sig:
                    support_ema = (
                        sig.ema_21
                        if sig.trend_state == "pullback_to_21ema"
                        else sig.ema_8
                    )
                    strike_floor = support_ema * (1 - pullback_strike_offset_pct / 100)
                    strike_ceiling = support_ema * (1 - pullback_strike_ceiling_pct / 100)
                    logger.info(
                        "pullback_strike_targeting",
                        symbol=symbol,
                        trend=sig.trend_state,
                        support_ema=round(support_ema, 2),
                        strike_floor=round(strike_floor, 2),
                        strike_ceiling=round(strike_ceiling, 2),
                        floor_pct=pullback_strike_offset_pct,
                        ceiling_pct=pullback_strike_ceiling_pct,
                    )
                else:
                    ema_8 = getattr(sig, "ema_8", 0) if sig else 0
                    if ema_8 > 0:
                        strike_floor = quote.last * (1 - strike_range_pct / 100)
                        strike_ceiling = ema_8
                    else:
                        strike_floor = quote.last * (1 - strike_range_pct / 100)
                        strike_ceiling = None
                    logger.info(
                        "uptrend_strike_targeting",
                        symbol=symbol,
                        ema_8=round(ema_8, 2) if ema_8 else None,
                        quote_last=round(quote.last, 2),
                        strike_floor=round(strike_floor, 2),
                        strike_ceiling=round(strike_ceiling, 2) if strike_ceiling else None,
                    )

                symbol_found_any = False
                for exp_str in target_exps:
                    try:
                        chain = await broker.get_options_chain(symbol, exp_str)
                    except Exception:
                        local_drops["chain_fetch_failed"] = local_drops.get("chain_fetch_failed", 0) + 1
                        logger.warning("chain_fetch_failed", symbol=symbol, expiration=exp_str)
                        continue

                    if chain.underlying_price == 0:
                        chain.underlying_price = quote.last

                    if not chain.puts:
                        local_drops["empty_chain"] = local_drops.get("empty_chain", 0) + 1
                        continue

                    raw = self.csp.identify_candidates(
                        chain, quote, strike_floor=strike_floor
                    )

                    if not raw:
                        local_drops["no_puts_in_range"] = local_drops.get("no_puts_in_range", 0) + 1
                        _dte_min = self.csp._dte_min
                        _dte_max = self.csp._dte_max
                        today = date.today()
                        dte_fail = itm_fail = floor_fail = bid_fail = 0
                        for c in chain.puts:
                            dte = (c.expiration - today).days
                            if not (_dte_min <= dte <= _dte_max):
                                dte_fail += 1
                            elif c.strike >= quote.last:
                                itm_fail += 1
                            elif strike_floor > 0 and c.strike < strike_floor:
                                floor_fail += 1
                            elif c.bid <= 0:
                                bid_fail += 1
                        local_drops["reject_dte"] = local_drops.get("reject_dte", 0) + dte_fail
                        local_drops["reject_itm"] = local_drops.get("reject_itm", 0) + itm_fail
                        local_drops["reject_strike_floor"] = local_drops.get("reject_strike_floor", 0) + floor_fail
                        local_drops["reject_bid_zero"] = local_drops.get("reject_bid_zero", 0) + bid_fail
                        logger.info(
                            "csp_no_puts_in_range",
                            symbol=symbol,
                            expiration=exp_str,
                            quote_last=quote.last,
                            strike_floor=round(strike_floor, 2),
                            strike_ceiling=round(strike_ceiling, 2) if strike_ceiling else None,
                            total_puts=len(chain.puts),
                            dte_fail=dte_fail,
                            itm_fail=itm_fail,
                            floor_fail=floor_fail,
                            bid_fail=bid_fail,
                        )
                        continue

                    if strike_ceiling is not None:
                        raw = [c for c in raw if c.strike <= strike_ceiling]
                        if not raw:
                            local_drops["ceiling_filtered_all"] = local_drops.get("ceiling_filtered_all", 0) + 1
                            continue

                    filtered = self.csp.apply_filters(
                        raw, min_oi, min_volume, max_spread_pct,
                        min_bid=min_bid, min_premium_pct=min_premium_pct,
                    )
                    if not filtered:
                        local_drops["quality_filtered_all"] = local_drops.get("quality_filtered_all", 0) + 1
                        logger.debug(
                            "csp_quality_filter_killed_all",
                            symbol=symbol,
                            expiration=exp_str,
                            raw_count=len(raw),
                            min_oi=min_oi,
                            min_volume=min_volume,
                            max_spread_pct=max_spread_pct,
                            min_bid=min_bid,
                            min_premium_pct=min_premium_pct,
                        )
                        continue

                    earnings_date = earnings_dates.get(symbol)
                    earnings_set: set[str] = set()
                    if earnings_date:
                        for fc in filtered:
                            if fc.dte > 0 and earnings_date <= fc.expiration:
                                earnings_set.add(fc.symbol)
                                break

                    scored = self.csp.score(
                        filtered, available_cash,
                        vrp_map=vrp_map,
                        iv_rank_map=iv_rank_map,
                        trend_confirm_map=trend_confirm_map,
                        rsi_map=rsi_map,
                        earnings_within_dte_set=earnings_set,
                        csp_safety_map=csp_safety_map,
                    )
                    if not scored:
                        local_drops["insufficient_capital"] = local_drops.get("insufficient_capital", 0) + 1
                        continue

                    for sc in scored:
                        if earnings_date and sc.dte > 0:
                            sc.earnings_date = earnings_date
                            sc.earnings_within_dte = (
                                earnings_date <= sc.expiration
                            )
                        if economic_calendar and sc.dte > 0:
                            today = date.today()
                            sc.macro_event_in_dte = economic_calendar.has_high_impact_event(
                                today, sc.expiration,
                            )

                    scored_out.extend(scored)
                    symbol_found_any = True

                if symbol_found_any:
                    local_drops["symbols_with_candidates"] = 1

            return scored_out, local_drops

        tasks = [_scan_symbol(s) for s in watchlist]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for r in results:
            if isinstance(r, Exception):
                drops["api_error"] += 1
                logger.warning("symbol_scan_failed", exc_info=r)
                continue
            scored_list, local_drops = r
            all_scored.extend(scored_list)
            for key, val in local_drops.items():
                drops[key] = drops.get(key, 0) + val

        if earliest_expiration_only and all_scored:
            earliest = min(sc.expiration for sc in all_scored)
            before = len(all_scored)
            all_scored = [sc for sc in all_scored if sc.expiration == earliest]
            drops["earliest_exp_filtered"] = before - len(all_scored)
            logger.info(
                "earliest_expiration_filter",
                earliest=earliest.isoformat(),
                before=before,
                after=len(all_scored),
                filtered_out=before - len(all_scored),
            )

        if csp_strike_preference == "near_21ema" and conviction_signals:
            all_scored = _apply_21ema_strike_bonus(
                all_scored, conviction_signals
            )

        all_scored = rank_candidates(
            all_scored,
            conviction_signals or {},
            mode=ranking_mode,
            weights=ranking_weights,
        )
        logger.info(
            "csp_scan_complete",
            symbols_scanned=len(watchlist),
            candidates_found=len(all_scored),
            top_n=top_n,
            expiration_mode=expiration_mode,
            strike_range_pct=strike_range_pct,
            strike_preference=csp_strike_preference,
            available_cash=available_cash,
            earliest_expiration_only=earliest_expiration_only,
            ranking_mode=ranking_mode,
            diagnostics=drops,
        )

        csp_scan_candidates_found.record(
            len(all_scored),
            {"expiration_mode": expiration_mode, "symbols_scanned": len(watchlist)},
        )
        for reason, count in drops.items():
            if count > 0 and reason != "symbols_with_candidates":
                csp_scan_drops.add(count, {"reason": reason})

        pool_size = max(top_n, pre_allocator_pool_size)
        drops["pre_allocator_pool_size"] = min(pool_size, len(all_scored))
        return all_scored[:pool_size], drops

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
