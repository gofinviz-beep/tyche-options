"""Live scan: find optimal CSP + CC trades using conviction engine + MILP optimizer.

Uses local OHLCV + ticker metadata for conviction filtering, then
hits Tradier for real-time quotes and options chains.  Covered call
candidates are sourced from actual broker equity positions.

The MILP portfolio allocator replaces the old greedy allocation loop,
producing provably optimal capital deployment across all candidates.
"""

from __future__ import annotations

import asyncio
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.chdir(os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv()

from tyche.broker.tradier.client import TradierClient
from tyche.conviction.engine import ConvictionEngine, ConvictionSignal
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.strategy.allocator import PortfolioAllocator
from tyche.strategy.strategies.base import ScoredCandidate

MIN_MARKET_CAP = 5_000_000_000  # $5B
MIN_PRICE = 15.0
AVAILABLE_CAPITAL = 100_000.0
DTE_MIN = 3
DTE_MAX = 14
OTM_PCT = 0.05
MAX_POSITIONS = 8
MAX_CONTRACTS = 40
MAX_CONCENTRATION_PCT = 25.0
VALID_EXCHANGES = {"XNYS", "XNAS", "XASE", "XNGS", "XNCM", "XNMS", "ARCX", "BATS"}


def _build_scored_candidate(
    symbol: str,
    contract: object,
    quote: object,
    exp_str: str,
    dte: int,
    strategy: str,
    available_cash: float,
    shares_held: int = 0,
) -> ScoredCandidate | None:
    """Build a ScoredCandidate from a Tradier option contract."""
    if contract.bid <= 0:
        return None

    premium_per = contract.bid * 100
    if strategy == "csp":
        collateral = contract.strike * 100
        max_ctrs = int(available_cash // collateral) if collateral > 0 else 0
    else:
        collateral = quote.last * 100
        max_ctrs = shares_held // 100

    if max_ctrs <= 0:
        return None

    if collateral > 0 and dte > 0:
        ann_return = (contract.bid / contract.strike) * (365 / dte) * 100
    else:
        ann_return = 0.0

    liq_factor = min(1.0, contract.open_interest / (1000 if strategy == "csp" else 500))
    score = ann_return * liq_factor

    return ScoredCandidate(
        symbol=symbol,
        option_symbol=contract.option_symbol,
        option_type=contract.option_type,
        strike=contract.strike,
        expiration=datetime.strptime(exp_str, "%Y-%m-%d").date(),
        dte=dte,
        bid=contract.bid,
        ask=contract.ask,
        mid=contract.mid,
        volume=contract.volume,
        open_interest=contract.open_interest,
        implied_volatility=contract.implied_volatility,
        underlying_price=quote.last,
        strategy=strategy,
        delta=contract.delta,
        theta=contract.theta,
        gamma=contract.gamma,
        vega=contract.vega,
        premium_per_contract=round(premium_per, 2),
        total_premium=round(premium_per * max_ctrs, 2),
        collateral_required=round(collateral * max_ctrs, 2),
        annualized_return_pct=round(ann_return, 2),
        score=round(score, 4),
    )


async def main() -> None:
    store = OHLCVStore(data_dir="data")
    meta_store = TickerMetaStore(data_dir="data")

    if not store.exists:
        print("ERROR: OHLCV data store not found. Run bootstrap first.")
        return
    if not meta_store.exists:
        print("ERROR: Ticker metadata store not found. Run bootstrap first.")
        return

    engine = ConvictionEngine(
        ema_fast=8, ema_slow=21,
        max_extension_pct=3.0, min_days_above_emas=5, max_days_above_emas=10,
    )

    all_caps = meta_store.get_market_caps()
    all_exchanges = meta_store.get_exchanges()
    qualified = {
        t for t, cap in all_caps.items()
        if cap >= MIN_MARKET_CAP and all_exchanges.get(t, "") in VALID_EXCHANGES
    }
    print(f"Tickers passing market cap (>=${MIN_MARKET_CAP/1e9:.0f}B) + exchange: {len(qualified)}")

    all_tickers = store.get_all_tickers()
    tickers_to_analyze = [t for t in all_tickers if t in qualified]
    print(f"Tickers with OHLCV data: {len(tickers_to_analyze)}")

    ticker_data = store.read_tickers(tickers_to_analyze)
    signals = engine.analyze_batch(ticker_data)
    eligible = [s for s in signals if s.csp_eligible]
    conviction_map: dict[str, ConvictionSignal] = {s.ticker: s for s in signals}
    print(f"\nConviction-eligible (CSP): {len(eligible)}")

    if not eligible:
        print("No stocks pass all conviction filters today.")
        return

    eligible.sort(key=lambda s: s.price_to_8ema_pct)

    print(f"\n{'Symbol':<8} {'Price':>8} {'8-EMA':>8} {'21-EMA':>8} {'Ext%':>6} {'Days':>5} {'MktCap':>10}")
    print("-" * 65)
    for s in eligible:
        cap = all_caps.get(s.ticker, 0)
        print(
            f"{s.ticker:<8} ${s.last_close:>7.2f} ${s.ema_8:>7.2f} ${s.ema_21:>7.2f} "
            f"{s.price_to_8ema_pct:>+5.1f}% {s.days_above_both_emas:>5} ${cap/1e9:>8.1f}B"
        )

    tradier = TradierClient(
        api_token=os.environ["TYCHE_TRADIER_API_TOKEN"],
        account_id=os.environ["TYCHE_TRADIER_ACCOUNT_ID"],
        base_url="https://api.tradier.com/v1",
    )

    try:
        # ---- Live quotes ----
        csp_symbols = [s.ticker for s in eligible]
        quotes = await tradier.get_quotes(csp_symbols)
        live_prices = {q.symbol: q for q in quotes}

        print(f"\n{'='*80}")
        print("LIVE QUOTES (Tradier)")
        print(f"{'='*80}")
        print(f"{'Symbol':<8} {'Last':>8} {'Bid':>8} {'Ask':>8} {'Volume':>10}")
        print("-" * 50)
        for s in eligible:
            q = live_prices.get(s.ticker)
            if q:
                print(f"{s.ticker:<8} ${q.last:>7.2f} ${q.bid:>7.2f} ${q.ask:>7.2f} {q.volume:>10,}")

        today = date.today()
        target_min = today + timedelta(days=DTE_MIN)
        target_max = today + timedelta(days=DTE_MAX)

        # ---- Scan CSP candidates ----
        print(f"\n{'='*80}")
        print(f"SCANNING OPTIONS (DTE {DTE_MIN}-{DTE_MAX}, OTM {OTM_PCT*100:.0f}%)")
        print(f"{'='*80}")

        csp_candidates: list[ScoredCandidate] = []
        for sig in eligible:
            q = live_prices.get(sig.ticker)
            if not q or q.last < MIN_PRICE:
                continue

            try:
                expirations = await tradier.get_options_expirations(sig.ticker)
            except Exception:
                continue

            valid_exps = []
            for exp_str in expirations:
                try:
                    exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                    if target_min <= exp_date <= target_max:
                        valid_exps.append(exp_str)
                except ValueError:
                    continue

            if not valid_exps:
                continue

            for exp_str in valid_exps[:1]:
                try:
                    chain = await tradier.get_options_chain(sig.ticker, exp_str)
                except Exception:
                    continue

                exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                dte = (exp_date - today).days
                target_strike = q.last * (1 - OTM_PCT)

                best_put = None
                for c in chain.contracts:
                    if c.option_type != "put" or c.strike > target_strike or c.bid <= 0:
                        continue
                    if best_put is None or c.strike > best_put.strike:
                        best_put = c

                if best_put:
                    sc = _build_scored_candidate(
                        sig.ticker, best_put, q, exp_str, dte, "csp", AVAILABLE_CAPITAL,
                    )
                    if sc:
                        csp_candidates.append(sc)

        print(f"CSP candidates found: {len(csp_candidates)}")

        # ---- Scan CC candidates from broker positions ----
        cc_candidates: list[ScoredCandidate] = []
        held_shares: dict[str, int] = {}

        try:
            positions = await tradier.get_positions()
            equity_positions = [
                p for p in positions
                if p.option_symbol is None and p.quantity >= 100
            ]

            if equity_positions:
                print(f"\nEquity positions for CC scan: {len(equity_positions)}")
                eq_symbols = [p.symbol for p in equity_positions]
                eq_quotes = await tradier.get_quotes(eq_symbols)
                eq_prices = {q.symbol: q for q in eq_quotes}

                for pos in equity_positions:
                    shares = int(pos.quantity)
                    held_shares[pos.symbol] = shares
                    q = eq_prices.get(pos.symbol)
                    if not q or q.last < MIN_PRICE:
                        continue

                    cost_basis_per = pos.cost_basis / pos.quantity if pos.quantity > 0 else 0.0

                    try:
                        expirations = await tradier.get_options_expirations(pos.symbol)
                    except Exception:
                        continue

                    valid_exps = []
                    for exp_str in expirations:
                        try:
                            exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                            if target_min <= exp_date <= target_max:
                                valid_exps.append(exp_str)
                        except ValueError:
                            continue

                    if not valid_exps:
                        continue

                    for exp_str in valid_exps[:1]:
                        try:
                            chain = await tradier.get_options_chain(pos.symbol, exp_str)
                        except Exception:
                            continue

                        exp_date = datetime.strptime(exp_str, "%Y-%m-%d").date()
                        dte = (exp_date - today).days

                        best_call = None
                        for c in chain.contracts:
                            if c.option_type != "call" or c.bid <= 0:
                                continue
                            if c.strike <= q.last:
                                continue
                            if cost_basis_per > 0 and c.strike < cost_basis_per:
                                continue
                            if best_call is None or c.bid > best_call.bid:
                                best_call = c

                        if best_call:
                            sc = _build_scored_candidate(
                                pos.symbol, best_call, q, exp_str, dte,
                                "covered_call", AVAILABLE_CAPITAL, shares,
                            )
                            if sc:
                                cc_candidates.append(sc)

                print(f"CC candidates found: {len(cc_candidates)}")
            else:
                print("\nNo equity positions for CC scan.")
        except Exception as exc:
            print(f"\nCC scan skipped (could not fetch positions): {exc}")

        # ---- Run MILP optimizer ----
        if not csp_candidates and not cc_candidates:
            print("\nNo tradeable candidates found in the DTE window.")
            return

        allocator = PortfolioAllocator(
            max_positions=MAX_POSITIONS,
            max_contracts_per_position=MAX_CONTRACTS,
            max_concentration_pct=MAX_CONCENTRATION_PCT,
        )

        result = allocator.optimize(
            csp_candidates=csp_candidates,
            cc_candidates=cc_candidates,
            available_capital=AVAILABLE_CAPITAL,
            conviction_signals=conviction_map,
            held_shares=held_shares,
        )

        print(f"\n{'='*80}")
        print(f"OPTIMAL PORTFOLIO (${AVAILABLE_CAPITAL:,.0f} capital, {MAX_POSITIONS} max positions)")
        print(f"Solver: {result.solver_status}")
        print(f"{'='*80}")

        if not result.trades:
            print("No trades in optimal solution.")
            return

        print(
            f"\n{'#':>2} {'Type':<5} {'Symbol':<7} {'Strike':>8} {'Exp':>11} "
            f"{'DTE':>4} {'Ctrs':>5} {'Bid':>7} {'Premium':>9} "
            f"{'Collateral':>11} {'Ann%':>7} {'Ext%':>5} {'Conv':>6}"
        )
        print("-" * 110)
        for i, t in enumerate(result.trades, 1):
            type_str = "PUT" if t.option_type == "put" else "CALL"
            print(
                f"{i:>2} {type_str:<5} {t.symbol:<7} ${t.strike:>7.2f} {t.expiration.isoformat():>11} "
                f"{t.dte:>4} {t.contracts:>5} ${t.bid:>6.2f} ${t.total_premium:>8,.0f} "
                f"${t.collateral:>10,.0f} {t.annualized_return_pct:>6.1f}% "
                f"{t.extension_pct:>+4.1f}% {t.conviction:>6}"
            )
        print("-" * 110)
        print(
            f"   {'TOTAL':<5} {'':<7} {'':<8} {'':>11} {'':<4} "
            f"{result.positions_used:>5} {'':<7} ${result.total_premium:>8,.0f} "
            f"${result.total_collateral:>10,.0f}"
        )
        print(f"\nCapital utilization: {result.capital_utilization_pct}%")
        print(f"Capital remaining: ${AVAILABLE_CAPITAL - result.total_collateral:,.0f}")

        # Show all raw candidates for context
        if csp_candidates:
            print(f"\n--- All CSP Candidates (pre-optimizer) ---")
            for sc in sorted(csp_candidates, key=lambda x: x.annualized_return_pct, reverse=True):
                sig = conviction_map.get(sc.symbol)
                ext = f"{sig.price_to_8ema_pct:+.1f}%" if sig else "n/a"
                cap_b = all_caps.get(sc.symbol, 0) / 1e9
                print(
                    f"  {sc.symbol:<7} ${sc.strike:>7.2f} put {sc.expiration.isoformat()} "
                    f"DTE={sc.dte} bid=${sc.bid:.2f} ann={sc.annualized_return_pct:.1f}% "
                    f"OI={sc.open_interest} ext={ext} cap=${cap_b:.0f}B"
                )

    finally:
        await tradier.close()


if __name__ == "__main__":
    asyncio.run(main())
