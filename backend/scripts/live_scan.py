"""Live scan: find CSP candidates using conviction engine + Tradier live data.

Uses local OHLCV + ticker metadata for conviction filtering, then
hits Tradier for real-time quotes and options chains.
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

from tyche.conviction.engine import ConvictionEngine
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.broker.tradier.client import TradierClient

MIN_MARKET_CAP = 5_000_000_000  # $5B
MIN_PRICE = 15.0
MIN_VOLUME = 500_000
AVAILABLE_CAPITAL = 100_000.0
DTE_MIN = 3
DTE_MAX = 14
OTM_PCT = 0.05
VALID_EXCHANGES = {"XNYS", "XNAS", "XASE", "XNGS", "XNCM", "XNMS", "ARCX", "BATS"}


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
        ema_fast=8,
        ema_slow=21,
        max_extension_pct=3.0,
        min_days_above_emas=5,
        max_days_above_emas=10,
    )

    all_caps = meta_store.get_market_caps()
    all_exchanges = meta_store.get_exchanges()
    qualified = {
        t
        for t, cap in all_caps.items()
        if cap >= MIN_MARKET_CAP and all_exchanges.get(t, "") in VALID_EXCHANGES
    }
    print(f"Tickers passing market cap (>=${MIN_MARKET_CAP/1e9:.0f}B) + exchange: {len(qualified)}")

    all_tickers = store.get_all_tickers()
    tickers_to_analyze = [t for t in all_tickers if t in qualified]
    print(f"Tickers with OHLCV data: {len(tickers_to_analyze)}")

    ticker_data = store.read_tickers(tickers_to_analyze)
    signals = engine.analyze_batch(ticker_data)
    eligible = [s for s in signals if s.csp_eligible]
    print(f"\nConviction-eligible (CSP): {len(eligible)}")

    if not eligible:
        print("No stocks pass all conviction filters today.")
        return

    eligible.sort(key=lambda s: s.price_to_8ema_pct)

    print(f"\n{'Symbol':<8} {'Price':>8} {'8-EMA':>8} {'21-EMA':>8} {'Ext%':>6} {'Days':>5} {'MktCap':>10}")
    print("-" * 65)
    for s in eligible:
        cap = all_caps.get(s.ticker, 0)
        cap_str = f"${cap / 1e9:.1f}B"
        print(
            f"{s.ticker:<8} ${s.last_close:>7.2f} ${s.ema_8:>7.2f} ${s.ema_21:>7.2f} "
            f"{s.price_to_8ema_pct:>+5.1f}% {s.days_above_both_emas:>5} {cap_str:>10}"
        )

    tradier = TradierClient(
        api_token=os.environ["TYCHE_TRADIER_API_TOKEN"],
        account_id=os.environ["TYCHE_TRADIER_ACCOUNT_ID"],
        base_url="https://api.tradier.com/v1",
    )

    try:
        symbols = [s.ticker for s in eligible]
        quotes = await tradier.get_quotes(symbols)
        live_prices = {q.symbol: q for q in quotes}

        print(f"\n{'='*80}")
        print("LIVE QUOTES (Tradier)")
        print(f"{'='*80}")
        print(f"{'Symbol':<8} {'Last':>8} {'Bid':>8} {'Ask':>8} {'Volume':>10}")
        print("-" * 50)
        for s in eligible:
            q = live_prices.get(s.ticker)
            if q:
                print(
                    f"{s.ticker:<8} ${q.last:>7.2f} ${q.bid:>7.2f} ${q.ask:>7.2f} "
                    f"{q.volume:>10,}"
                )

        today = date.today()
        target_min = today + timedelta(days=DTE_MIN)
        target_max = today + timedelta(days=DTE_MAX)

        print(f"\n{'='*80}")
        print(f"CSP CANDIDATES (DTE {DTE_MIN}-{DTE_MAX}, OTM {OTM_PCT*100:.0f}%, Capital ${AVAILABLE_CAPITAL:,.0f})")
        print(f"{'='*80}")

        trades = []
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
                    if c.option_type != "put":
                        continue
                    if c.strike > target_strike:
                        continue
                    if c.bid <= 0:
                        continue
                    if best_put is None or c.strike > best_put.strike:
                        best_put = c

                if best_put:
                    otm_pct = (q.last - best_put.strike) / q.last * 100
                    collateral = best_put.strike * 100
                    max_contracts = int(AVAILABLE_CAPITAL // collateral)
                    if max_contracts < 1:
                        continue
                    premium = best_put.bid * 100
                    ann_return = (best_put.bid / best_put.strike) * (365 / dte) * 100

                    trades.append({
                        "symbol": sig.ticker,
                        "price": q.last,
                        "strike": best_put.strike,
                        "exp": exp_str,
                        "dte": dte,
                        "otm_pct": otm_pct,
                        "bid": best_put.bid,
                        "premium": premium,
                        "ann_return": ann_return,
                        "max_contracts": max_contracts,
                        "collateral_each": collateral,
                        "ext_pct": sig.price_to_8ema_pct,
                        "days_above": sig.days_above_both_emas,
                        "market_cap": all_caps.get(sig.ticker, 0),
                    })

        trades.sort(key=lambda t: t["ann_return"], reverse=True)

        if not trades:
            print("\nNo CSP trades found with weekly expirations in the DTE window.")
            print("Most stocks only have monthly expirations.")
            return

        print(
            f"\n{'#':>2} {'Symbol':<7} {'Price':>8} {'Strike':>8} {'Exp':>11} "
            f"{'DTE':>4} {'OTM%':>6} {'Bid':>7} {'Ann%':>7} "
            f"{'Ctrs':>5} {'Premium':>9} {'Collateral':>11} {'Ext%':>5} {'Days':>4} {'MktCap':>8}"
        )
        print("-" * 130)
        for i, t in enumerate(trades, 1):
            cap_str = f"${t['market_cap']/1e9:.0f}B"
            print(
                f"{i:>2} {t['symbol']:<7} ${t['price']:>7.2f} ${t['strike']:>7.2f} {t['exp']:>11} "
                f"{t['dte']:>4} {t['otm_pct']:>5.1f}% ${t['bid']:>6.2f} {t['ann_return']:>6.1f}% "
                f"{t['max_contracts']:>5} ${t['premium']*t['max_contracts']:>8,.0f} "
                f"${t['collateral_each']*t['max_contracts']:>10,.0f} "
                f"{t['ext_pct']:>+4.1f}% {t['days_above']:>4} {cap_str:>8}"
            )

        print(f"\n--- Portfolio Allocation (${AVAILABLE_CAPITAL:,.0f} capital) ---")
        remaining = AVAILABLE_CAPITAL
        picks = []
        for t in trades:
            if remaining < t["collateral_each"]:
                continue
            n = min(t["max_contracts"], int(remaining // t["collateral_each"]))
            if n < 1:
                continue
            cost = n * t["collateral_each"]
            prem = n * t["premium"]
            picks.append({**t, "contracts": n, "total_collateral": cost, "total_premium": prem})
            remaining -= cost
            if remaining < 5000:
                break

        if picks:
            print(f"\n{'Symbol':<7} {'Trade':<30} {'Ctrs':>5} {'Premium':>9} {'Collateral':>11} {'Ann%':>7}")
            print("-" * 80)
            total_prem = 0
            total_coll = 0
            for p in picks:
                trade_desc = f"${p['strike']} put {p['exp']}"
                total_prem += p["total_premium"]
                total_coll += p["total_collateral"]
                print(
                    f"{p['symbol']:<7} {trade_desc:<30} {p['contracts']:>5} "
                    f"${p['total_premium']:>8,.0f} ${p['total_collateral']:>10,.0f} {p['ann_return']:>6.1f}%"
                )
            print("-" * 80)
            print(
                f"{'TOTAL':<7} {'':<30} {'':<5} "
                f"${total_prem:>8,.0f} ${total_coll:>10,.0f}"
            )
            print(f"Capital remaining: ${remaining:,.0f}")

    finally:
        await tradier.close()


if __name__ == "__main__":
    asyncio.run(main())
