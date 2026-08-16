"""Ad-hoc scan: AI / chip names that have dropped -> deep-dip recovery ranking.

Uses the platform's own ConvictionFeatureEngine over GCS OHLCV plus live Tradier
quotes (pre-market / intraday) to find the biggest drops with the best recovery setups.
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta

import pandas as pd

from tyche.config import get_settings
from tyche.market_data.data_store import OHLCVStore, TickerMetaStore
from tyche.conviction.features import ConvictionFeatureEngine
from tyche.broker.tradier.client import TradierClient

# Top AI / semiconductor complex (chipmakers, memory, AI-hardware, foundry, equip)
UNIVERSE = [
    "NVDA", "AMD", "MU", "AVGO", "QCOM", "TXN", "INTC", "MRVL", "ARM", "TSM",
    "ASML", "AMAT", "LRCX", "KLAC", "ADI", "NXPI", "MCHP", "ON", "MPWR",
    "SMCI", "ANET", "VRT", "WDC", "STX", "TER", "MSFT", "GOOGL", "META", "AMZN", "ORCL",
]


def _rsi_zone(rsi: float) -> str:
    if rsi <= 30:
        return "deep-oversold"
    if rsi <= 50:
        return "stabilizing"
    if rsi <= 70:
        return "neutral"
    return "overbought"


async def main() -> None:
    settings = get_settings()
    ohlcv = OHLCVStore(data_dir=settings.data_dir)
    meta = TickerMetaStore(data_dir=settings.data_dir)
    engine = ConvictionFeatureEngine(
        oversold_dip_pct_21ema=settings.oversold_dip_pct_21ema,
        oversold_dip_pct_50ema=settings.oversold_dip_pct_50ema,
        oversold_min_prior_uptrend=settings.oversold_min_prior_uptrend,
    )

    caps = meta.get_market_caps(UNIVERSE)

    # Live quotes (pre-market / intraday) from Tradier
    base_url = settings.tradier_base_url or (
        "https://sandbox.tradier.com/v1"
        if settings.tradier_sandbox
        else "https://api.tradier.com/v1"
    )
    client = TradierClient(
        api_token=settings.tradier_api_token,
        account_id=settings.tradier_account_id,
        base_url=base_url,
    )
    quotes: dict[str, object] = {}
    try:
        qlist = await client.get_quotes(UNIVERSE)
        quotes = {q.symbol: q for q in qlist}
    except Exception as exc:  # noqa: BLE001
        print(f"[warn] Tradier quotes failed: {exc}")

    start = date.today() - timedelta(days=400)
    rows = []
    for t in UNIVERSE:
        df = ohlcv.read_ticker(t, start_date=start)
        if df.empty or len(df) < 60:
            print(f"[skip] {t}: insufficient OHLCV ({len(df)} rows)")
            continue
        last_close = float(df["close"].iloc[-1])
        last_date = df["date"].iloc[-1]

        sig = engine.analyze(t, df)

        # Recent-high drawdown (20 / 60 trading days).
        # Guard against corrupt single-bar prints (e.g. bad split adjustment):
        # ignore any bar whose close is > 2x the window median.
        c = df["close"]

        def _clean_high(window: int) -> float:
            w = c.tail(window)
            med = float(w.median())
            clean = w[w <= med * 2.0] if med > 0 else w
            return float(clean.max()) if len(clean) else float(w.max())

        high20 = _clean_high(20)
        high60 = _clean_high(60)
        dd20 = (last_close / high20 - 1.0) * 100.0
        dd60 = (last_close / high60 - 1.0) * 100.0
        ret5 = (last_close / float(c.iloc[-6]) - 1.0) * 100.0 if len(c) > 6 else 0.0

        q = quotes.get(t)
        live = float(getattr(q, "last", 0) or 0) if q else 0.0
        prev = float(getattr(q, "close", 0) or 0) if q else 0.0
        chg_today = float(getattr(q, "change_pct", 0) or 0) if q else 0.0
        # live drawdown vs the OHLCV EMAs (EMAs from Friday close; live=today)
        px = live if live > 0 else last_close
        ema8 = sig.ema_8 or 0.0
        ema21 = sig.ema_21 or 0.0
        ema50 = getattr(sig, "ema_50", 0.0) or 0.0
        live_to_8 = (px / ema8 - 1.0) * 100.0 if ema8 else 0.0
        live_to_21 = (px / ema21 - 1.0) * 100.0 if ema21 else 0.0
        live_to_50 = (px / ema50 - 1.0) * 100.0 if ema50 else 0.0

        cap_b = caps.get(t, 0.0) / 1e9

        rows.append(
            {
                "ticker": t,
                "cap_$B": round(cap_b, 1),
                "live": round(px, 2),
                "prev_close": round(prev, 2),
                "chg_today%": round(chg_today, 2),
                "ret_5d%": round(ret5, 1),
                "dd_20d%": round(dd20, 1),
                "dd_60d%": round(dd60, 1),
                "vs_8ema%": round(live_to_8, 1),
                "vs_21ema%": round(live_to_21, 1),
                "vs_50ema%": round(live_to_50, 1),
                "rsi14": round(sig.rsi_14, 1),
                "rsi_zone": _rsi_zone(sig.rsi_14),
                "ema21_slope": round(sig.ema_21_slope, 3),
                "ema50_slope": round(getattr(sig, "ema_50_slope", 0.0), 3),
                "trend": sig.trend_state.value,
                "prior_streak": sig.prior_streak,
                "conv_score": round(sig.conviction_score, 3),
                "csp_safe": (round(sig.csp_safety_prob, 3) if sig.csp_safety_prob is not None else None),
                "ohlcv_date": str(last_date),
            }
        )

    df = pd.DataFrame(rows)
    if df.empty:
        print("No data.")
        return

    # Rank the drops: most negative vs 21-EMA first (biggest dip vs trend)
    df = df.sort_values("vs_21ema%").reset_index(drop=True)

    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 240)
    print("\n==== AI/CHIP DIP SCAN (sorted by drop vs 21-EMA) ====")
    print(df.to_string(index=False))

    # Deep-dip recovery candidates: oversold + trend not broken + large cap
    cand = df[
        (df["vs_21ema%"] <= -4)
        & (df["rsi14"] >= 28)
        & (df["rsi14"] <= 55)
        & (df["ema50_slope"] > -0.5)
        & (df["cap_$B"] >= 20)
    ].copy()
    print("\n==== RECOVERY-SETUP CANDIDATES (backtest thresholds) ====")
    if cand.empty:
        print("(none met all thresholds — see full table)")
    else:
        print(cand.to_string(index=False))

    df.to_csv("/tmp/ai_chip_dip_scan.csv", index=False)
    print("\nSaved: /tmp/ai_chip_dip_scan.csv")


if __name__ == "__main__":
    asyncio.run(main())
