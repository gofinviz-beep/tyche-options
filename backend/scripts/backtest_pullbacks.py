"""Backtest historical EMA pullback events and build per-ticker bounce profiles.

For each qualifying ticker, scans OHLCV history to find pullback events
(pullback_to_8ema, pullback_to_21ema), measures the bounce from entry to the
first close below the 8-EMA, and persists raw events + aggregated profiles.

Usage:
    cd backend && python scripts/backtest_pullbacks.py [--workers 4] [--force]
"""

import argparse
import sqlite3
import sys
import time
import uuid
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.market_data.data_store import OHLCVStore

EMA_FAST = 8
EMA_SLOW = 21
PROXIMITY_PCT = 2.0
MIN_BARS = 60


@dataclass
class PullbackEventRow:
    ticker: str
    pullback_type: str
    entry_date: str
    entry_price: float
    peak_date: str
    peak_price: float
    peak_gain_pct: float
    exit_date: str
    exit_price: float
    exit_gain_pct: float
    days_to_peak: int
    days_to_exit: int
    max_drawdown_pct: float
    volume_declining_at_entry: int


def compute_ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def compute_slope(series: pd.Series, periods: int = 3) -> float:
    if len(series) < periods:
        return 0.0
    y = series.iloc[-periods:].values
    x = np.arange(periods, dtype=float)
    if np.std(y) == 0:
        return 0.0
    return float(np.polyfit(x, y, 1)[0])


def classify_trend(
    price: float,
    ema_8: float,
    ema_21: float,
    slope_8: float,
    slope_21: float,
    pct_to_8: float,
    pct_to_21: float,
) -> str:
    """Classify trend state — mirrors ConvictionEngine._classify_trend."""
    above_8 = price > ema_8
    above_21 = price > ema_21
    both_slopes_up = slope_8 > 0 and slope_21 > 0

    if above_8 and above_21:
        if both_slopes_up and pct_to_8 > 1.0:
            return "strong_uptrend"
        return "uptrend"

    if above_21 and not above_8:
        if abs(pct_to_8) <= PROXIMITY_PCT:
            return "pullback_to_8ema"
        if abs(pct_to_21) <= PROXIMITY_PCT:
            return "pullback_to_21ema"
        return "consolidation"

    if not above_21 and abs(pct_to_21) <= PROXIMITY_PCT and slope_21 > 0:
        return "pullback_to_21ema"

    if not above_8 and not above_21:
        return "downtrend"

    return "consolidation"


def is_volume_declining(volumes: pd.Series, idx: int, lookback: int = 5) -> bool:
    """Check if recent volume is below the lookback average."""
    if idx < lookback:
        return False
    recent_avg = volumes.iloc[idx - lookback : idx].mean()
    return bool(volumes.iloc[idx] < recent_avg)


def scan_ticker(ticker: str, store_dir: str) -> list[PullbackEventRow]:
    """Scan a single ticker's OHLCV for pullback events.

    Runs in a worker process — reads Parquet directly (no OHLCVStore instance).
    """
    path = Path(store_dir) / f"{ticker}.parquet"
    if not path.exists():
        return []

    try:
        df = pd.read_parquet(path)
    except Exception:
        return []

    df["date"] = pd.to_datetime(df["date"]).dt.date
    df = df.sort_values("date").reset_index(drop=True)

    if len(df) < MIN_BARS:
        return []

    close = df["close"].astype(float)
    volume = df["volume"].astype(float)

    ema_8 = compute_ema(close, EMA_FAST)
    ema_21 = compute_ema(close, EMA_SLOW)

    events: list[PullbackEventRow] = []
    n = len(df)
    i = MIN_BARS

    while i < n:
        price = close.iloc[i]
        e8 = ema_8.iloc[i]
        e21 = ema_21.iloc[i]

        slope_8 = compute_slope(ema_8.iloc[max(0, i - 2) : i + 1])
        slope_21 = compute_slope(ema_21.iloc[max(0, i - 2) : i + 1])

        pct_to_8 = ((price - e8) / e8 * 100) if e8 else 0
        pct_to_21 = ((price - e21) / e21 * 100) if e21 else 0

        trend = classify_trend(price, e8, e21, slope_8, slope_21, pct_to_8, pct_to_21)

        if trend not in ("pullback_to_8ema", "pullback_to_21ema"):
            i += 1
            continue

        pullback_type = "8ema" if trend == "pullback_to_8ema" else "21ema"
        entry_date = df["date"].iloc[i]
        entry_price = price
        vol_declining = is_volume_declining(volume, i)

        peak_price = entry_price
        peak_idx = i
        max_drawdown = 0.0
        exit_idx = None

        j = i + 1
        while j < n:
            day_close = close.iloc[j]
            day_low = float(df["low"].iloc[j])
            day_ema8 = ema_8.iloc[j]

            if day_close > peak_price:
                peak_price = day_close
                peak_idx = j

            dd = ((day_low - entry_price) / entry_price) * 100
            if dd < max_drawdown:
                max_drawdown = dd

            if day_close < day_ema8:
                exit_idx = j
                break

            j += 1

        if exit_idx is None:
            i = j if j < n else n
            continue

        exit_price = close.iloc[exit_idx]
        peak_gain = ((peak_price - entry_price) / entry_price) * 100
        exit_gain = ((exit_price - entry_price) / entry_price) * 100

        events.append(PullbackEventRow(
            ticker=ticker,
            pullback_type=pullback_type,
            entry_date=str(entry_date),
            entry_price=round(entry_price, 4),
            peak_date=str(df["date"].iloc[peak_idx]),
            peak_price=round(peak_price, 4),
            peak_gain_pct=round(peak_gain, 4),
            exit_date=str(df["date"].iloc[exit_idx]),
            exit_price=round(exit_price, 4),
            exit_gain_pct=round(exit_gain, 4),
            days_to_peak=peak_idx - i,
            days_to_exit=exit_idx - i,
            max_drawdown_pct=round(max_drawdown, 4),
            volume_declining_at_entry=int(vol_declining),
        ))

        i = exit_idx + 1

    return events


def aggregate_profiles(
    events: list[PullbackEventRow],
) -> list[dict]:
    """Aggregate raw events into per-ticker, per-type profiles."""
    from collections import defaultdict

    buckets: dict[tuple[str, str], list[PullbackEventRow]] = defaultdict(list)
    for e in events:
        buckets[(e.ticker, e.pullback_type)].append(e)

    profiles = []
    now = datetime.now(timezone.utc).isoformat()

    for (ticker, ptype), evts in buckets.items():
        peaks = [e.peak_gain_pct for e in evts]
        exits = [e.exit_gain_pct for e in evts]
        d2p = [e.days_to_peak for e in evts]
        d2e = [e.days_to_exit for e in evts]
        dds = [e.max_drawdown_pct for e in evts]

        count = len(evts)
        profiles.append({
            "id": str(uuid.uuid4()),
            "ticker": ticker,
            "pullback_type": ptype,
            "event_count": count,
            "median_peak_gain_pct": round(float(np.median(peaks)), 4),
            "mean_peak_gain_pct": round(float(np.mean(peaks)), 4),
            "p25_peak_gain_pct": round(float(np.percentile(peaks, 25)), 4),
            "p75_peak_gain_pct": round(float(np.percentile(peaks, 75)), 4),
            "median_exit_gain_pct": round(float(np.median(exits)), 4),
            "win_rate_5pct": round(sum(1 for p in peaks if p >= 5) / count, 4),
            "win_rate_10pct": round(sum(1 for p in peaks if p >= 10) / count, 4),
            "median_days_to_peak": int(np.median(d2p)),
            "median_days_to_exit": int(np.median(d2e)),
            "avg_max_drawdown_pct": round(float(np.mean(dds)), 4),
            "last_computed": now,
        })

    return profiles


def init_db(db_path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS pullback_events (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            pullback_type TEXT NOT NULL,
            entry_date TEXT NOT NULL,
            entry_price REAL NOT NULL,
            peak_date TEXT NOT NULL,
            peak_price REAL NOT NULL,
            peak_gain_pct REAL NOT NULL,
            exit_date TEXT NOT NULL,
            exit_price REAL NOT NULL,
            exit_gain_pct REAL NOT NULL,
            days_to_peak INTEGER NOT NULL,
            days_to_exit INTEGER NOT NULL,
            max_drawdown_pct REAL DEFAULT 0.0,
            volume_declining_at_entry INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ticker_pullback_profiles (
            id TEXT PRIMARY KEY,
            ticker TEXT NOT NULL,
            pullback_type TEXT NOT NULL,
            event_count INTEGER NOT NULL,
            median_peak_gain_pct REAL DEFAULT 0.0,
            mean_peak_gain_pct REAL DEFAULT 0.0,
            p25_peak_gain_pct REAL DEFAULT 0.0,
            p75_peak_gain_pct REAL DEFAULT 0.0,
            median_exit_gain_pct REAL DEFAULT 0.0,
            win_rate_5pct REAL DEFAULT 0.0,
            win_rate_10pct REAL DEFAULT 0.0,
            median_days_to_peak INTEGER DEFAULT 0,
            median_days_to_exit INTEGER DEFAULT 0,
            avg_max_drawdown_pct REAL DEFAULT 0.0,
            last_computed TEXT
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS ix_event_ticker ON pullback_events(ticker)")
    conn.execute("CREATE INDEX IF NOT EXISTS ix_event_type_ticker ON pullback_events(pullback_type, ticker)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS ix_profile_ticker_type ON ticker_pullback_profiles(ticker, pullback_type)")
    conn.commit()
    return conn


def run_backtest(workers: int, force: bool):
    settings = TycheSettings()
    store = OHLCVStore(data_dir=settings.data_dir)

    if not store.exists:
        print("ERROR: OHLCV store is empty.")
        sys.exit(1)

    db_dir = Path(settings.db_dir)
    db_dir.mkdir(parents=True, exist_ok=True)
    db_path = str(db_dir / "backtest.db")

    conn = init_db(db_path)

    all_tickers = store.get_all_tickers()
    print(f"Store has {len(all_tickers)} tickers")

    if not force:
        cursor = conn.execute(
            "SELECT DISTINCT ticker FROM pullback_events"
        )
        already_done = {row[0] for row in cursor.fetchall()}
        tickers = [t for t in all_tickers if t not in already_done]
        print(f"  Already computed: {len(already_done)}, remaining: {len(tickers)}")
    else:
        conn.execute("DELETE FROM pullback_events")
        conn.execute("DELETE FROM ticker_pullback_profiles")
        conn.commit()
        tickers = all_tickers
        print("  --force: recomputing all tickers")

    if not tickers:
        print("Nothing to compute.")
        conn.close()
        return

    store_dir = str(store.store_dir)
    t0 = time.time()
    all_events: list[PullbackEventRow] = []
    completed = 0
    errors = 0

    print(f"\nScanning {len(tickers)} tickers with {workers} workers...")

    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(scan_ticker, ticker, store_dir): ticker
            for ticker in tickers
        }

        for future in as_completed(futures):
            ticker = futures[future]
            completed += 1

            if completed % 200 == 0:
                elapsed = time.time() - t0
                rate = completed / elapsed if elapsed > 0 else 0
                eta = (len(tickers) - completed) / rate if rate > 0 else 0
                print(
                    f"  [{completed}/{len(tickers)}] "
                    f"events={len(all_events)} "
                    f"({rate:.0f} tickers/s, ETA {eta:.0f}s)"
                )

            try:
                events = future.result()
                all_events.extend(events)
            except Exception as exc:
                errors += 1
                if errors <= 10:
                    print(f"  ERROR {ticker}: {exc}")

    elapsed = time.time() - t0
    print(f"\nScan complete in {elapsed:.1f}s")
    print(f"  Tickers scanned: {completed}")
    print(f"  Total events found: {len(all_events)}")
    print(f"  Errors: {errors}")

    if not all_events:
        print("No pullback events found.")
        conn.close()
        return

    print("\nWriting events to DB...")
    batch_size = 500
    for start in range(0, len(all_events), batch_size):
        batch = all_events[start : start + batch_size]
        conn.executemany(
            """INSERT OR REPLACE INTO pullback_events
               (id, ticker, pullback_type, entry_date, entry_price,
                peak_date, peak_price, peak_gain_pct,
                exit_date, exit_price, exit_gain_pct,
                days_to_peak, days_to_exit, max_drawdown_pct,
                volume_declining_at_entry)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            [
                (
                    str(uuid.uuid4()),
                    e.ticker, e.pullback_type,
                    e.entry_date, e.entry_price,
                    e.peak_date, e.peak_price, e.peak_gain_pct,
                    e.exit_date, e.exit_price, e.exit_gain_pct,
                    e.days_to_peak, e.days_to_exit, e.max_drawdown_pct,
                    e.volume_declining_at_entry,
                )
                for e in batch
            ],
        )
    conn.commit()
    print(f"  Wrote {len(all_events)} events")

    print("Computing aggregate profiles...")
    profiles = aggregate_profiles(all_events)
    conn.executemany(
        """INSERT OR REPLACE INTO ticker_pullback_profiles
           (id, ticker, pullback_type, event_count,
            median_peak_gain_pct, mean_peak_gain_pct,
            p25_peak_gain_pct, p75_peak_gain_pct,
            median_exit_gain_pct,
            win_rate_5pct, win_rate_10pct,
            median_days_to_peak, median_days_to_exit,
            avg_max_drawdown_pct, last_computed)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (
                p["id"], p["ticker"], p["pullback_type"], p["event_count"],
                p["median_peak_gain_pct"], p["mean_peak_gain_pct"],
                p["p25_peak_gain_pct"], p["p75_peak_gain_pct"],
                p["median_exit_gain_pct"],
                p["win_rate_5pct"], p["win_rate_10pct"],
                p["median_days_to_peak"], p["median_days_to_exit"],
                p["avg_max_drawdown_pct"], p["last_computed"],
            )
            for p in profiles
        ],
    )
    conn.commit()
    print(f"  Wrote {len(profiles)} profiles")

    type_8 = [p for p in profiles if p["pullback_type"] == "8ema"]
    type_21 = [p for p in profiles if p["pullback_type"] == "21ema"]

    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    print(f"  8-EMA pullback profiles: {len(type_8)}")
    if type_8:
        med_bounces = [p["median_peak_gain_pct"] for p in type_8]
        print(f"    Median bounce (across tickers): {np.median(med_bounces):.2f}%")
        print(f"    Avg win rate (>=5%): {np.mean([p['win_rate_5pct'] for p in type_8]):.1%}")
    print(f"  21-EMA pullback profiles: {len(type_21)}")
    if type_21:
        med_bounces = [p["median_peak_gain_pct"] for p in type_21]
        print(f"    Median bounce (across tickers): {np.median(med_bounces):.2f}%")
        print(f"    Avg win rate (>=5%): {np.mean([p['win_rate_5pct'] for p in type_21]):.1%}")

    conn.close()
    print(f"\nResults saved to {db_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backtest EMA pullback bounces")
    parser.add_argument(
        "--workers",
        type=int,
        default=4,
        help="Number of parallel worker processes (default: 4)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Recompute all tickers (clears existing results)",
    )
    args = parser.parse_args()
    run_backtest(args.workers, args.force)
