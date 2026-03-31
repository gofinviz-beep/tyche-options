"""Ingest historical OHLCV data from the infiniti store into Tyche's format.

Reads per-ticker Parquet files from the infiniti data_extract directory,
applies pre-filters (exchange, market cap, stock type), transforms columns
to match Tyche's OHLCV_SCHEMA, and writes per-ticker Parquet files.

Usage:
    cd backend && python scripts/ingest_infiniti.py [--min-market-cap 500000000]
"""

import argparse
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, "src")

from tyche.config import TycheSettings
from tyche.market_data.data_store import OHLCV_SCHEMA, OHLCVStore, TickerMetaStore

INFINITI_BASE = Path("/Users/m0m0zk1/Development/python/infiniti/data_extract/ohlcv")

VALID_EXCHANGES = {"XNYS", "XNAS", "XNMS", "XASE", "ARCX", "BATS"}

SKIP_SUFFIXES = {".WS", ".U", ".R", ".RT", ".W"}

MIN_BARS = 60


def should_skip_ticker(ticker: str) -> bool:
    """Skip warrants, units, rights, and other non-common-stock suffixes."""
    for suffix in SKIP_SUFFIXES:
        if ticker.upper().endswith(suffix):
            return True
    if "." in ticker and ticker.split(".")[-1] in {"WS", "U", "R", "RT", "W"}:
        return True
    return False


def transform_infiniti_df(df: pd.DataFrame) -> pd.DataFrame:
    """Transform infiniti columns to Tyche's OHLCV schema."""
    out = df.rename(columns={"trade_date": "date"}).copy()
    out["date"] = pd.to_datetime(out["date"]).dt.date

    for col in ["ticker", "adj_close", "source", "ingested_at"]:
        if col in out.columns:
            out = out.drop(columns=[col])

    out["volume"] = pd.to_numeric(out["volume"], errors="coerce").fillna(0).astype(np.int64)
    out["vwap"] = pd.to_numeric(out["vwap"], errors="coerce").fillna(0.0)

    out = out.drop_duplicates(subset=["date"], keep="last")
    out = out.sort_values("date").reset_index(drop=True)

    required = {"date", "open", "high", "low", "close", "volume", "vwap"}
    out = out[[c for c in out.columns if c in required]]

    return out


def run_ingest(
    min_market_cap: float,
    min_price: float,
    dry_run: bool = False,
):
    settings = TycheSettings()
    store = OHLCVStore(data_dir=settings.data_dir)
    meta_store = TickerMetaStore(data_dir=settings.data_dir)

    if not meta_store.exists:
        print("ERROR: ticker_meta.parquet not found. Run bootstrap first.")
        print("  python -m tyche.market_data.polygon bootstrap")
        sys.exit(1)

    print("Loading ticker metadata for pre-filtering...")
    all_caps = meta_store.get_market_caps()
    all_exchanges = meta_store.get_exchanges()
    print(f"  Metadata covers {len(all_caps)} tickers")

    has_market_caps = any(cap > 0 for cap in all_caps.values())
    if not has_market_caps:
        print("  WARNING: All market caps are 0 — skipping market-cap filter")
        print("           (run bootstrap with Polygon to populate market caps)")

    qualified = set()
    for ticker, cap in all_caps.items():
        exchange = all_exchanges.get(ticker, "")
        if exchange not in VALID_EXCHANGES:
            continue
        if has_market_caps and cap < min_market_cap:
            continue
        if not should_skip_ticker(ticker):
            qualified.add(ticker.upper())
    print(f"  Tickers passing filters: {len(qualified)}")

    if not INFINITI_BASE.exists():
        print(f"ERROR: Infiniti store not found at {INFINITI_BASE}")
        sys.exit(1)

    infiniti_dirs = sorted(
        d.name for d in INFINITI_BASE.iterdir()
        if d.is_dir() and not d.name.startswith("_")
    )
    print(f"  Infiniti store has {len(infiniti_dirs)} ticker directories")

    candidates = [t for t in infiniti_dirs if t.upper() in qualified]
    print(f"  Matching qualified tickers in infiniti: {len(candidates)}")

    if dry_run:
        print("\n[DRY RUN] Would ingest the following tickers:")
        for t in candidates[:20]:
            print(f"  {t}")
        if len(candidates) > 20:
            print(f"  ... and {len(candidates) - 20} more")
        return

    existing_tickers = set(store.get_all_tickers()) if store.exists else set()
    print(f"  Tyche store already has {len(existing_tickers)} tickers")

    ingested = 0
    skipped_existing = 0
    skipped_too_few = 0
    errors = 0
    t0 = time.time()

    for i, ticker in enumerate(candidates):
        if (i + 1) % 200 == 0 or i == 0:
            elapsed = time.time() - t0
            rate = (i + 1) / elapsed if elapsed > 0 else 0
            eta = (len(candidates) - i - 1) / rate if rate > 0 else 0
            print(
                f"  [{i + 1}/{len(candidates)}] "
                f"ingested={ingested} skipped={skipped_existing} errors={errors} "
                f"({rate:.0f} tickers/s, ETA {eta:.0f}s)"
            )

        src_path = INFINITI_BASE / ticker / "ohlcv_daily.parquet"
        if not src_path.exists():
            continue

        ticker_upper = ticker.upper()

        if ticker_upper in existing_tickers:
            existing_df = store.read_ticker(ticker_upper)
            if not existing_df.empty:
                try:
                    src_df = pd.read_parquet(src_path)
                    src_dates = pd.to_datetime(src_df["trade_date"])
                    src_min = src_dates.min().date()
                    src_max = src_dates.max().date()
                    existing_min = existing_df["date"].min()
                    existing_max = existing_df["date"].max()
                    if src_min >= existing_min and src_max <= existing_max:
                        skipped_existing += 1
                        continue
                except Exception:
                    pass

        try:
            raw = pd.read_parquet(src_path)
        except Exception as exc:
            print(f"  ERROR reading {ticker}: {exc}")
            errors += 1
            continue

        if len(raw) < MIN_BARS:
            skipped_too_few += 1
            continue

        transformed = transform_infiniti_df(raw)
        if len(transformed) < MIN_BARS:
            skipped_too_few += 1
            continue

        dest_path = store._ticker_path(ticker_upper)
        try:
            if dest_path.exists():
                existing_df = pd.read_parquet(dest_path)
                existing_df["date"] = pd.to_datetime(existing_df["date"]).dt.date
                combined = pd.concat([existing_df, transformed], ignore_index=True)
                combined = combined.drop_duplicates(subset=["date"], keep="last")
                combined = combined.sort_values("date").reset_index(drop=True)
            else:
                combined = transformed

            table = pa.Table.from_pandas(combined, schema=OHLCV_SCHEMA)
            pq.write_table(table, dest_path, compression="snappy")
            ingested += 1
        except Exception as exc:
            print(f"  ERROR writing {ticker}: {exc}")
            errors += 1
            continue

    elapsed = time.time() - t0
    print(f"\nIngest complete in {elapsed:.1f}s")
    print(f"  Ingested: {ingested}")
    print(f"  Skipped (already up-to-date): {skipped_existing}")
    print(f"  Skipped (< {MIN_BARS} bars): {skipped_too_few}")
    print(f"  Errors: {errors}")

    print("Rebuilding OHLCV cache...")
    cache = store.rebuild_cache()
    print(f"  Store now has {cache.get('ticker_count', '?')} tickers, "
          f"{cache.get('total_rows', '?')} rows")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest infiniti OHLCV into Tyche")
    parser.add_argument(
        "--min-market-cap",
        type=float,
        default=500_000_000,
        help="Minimum market cap in dollars (default: $500M)",
    )
    parser.add_argument(
        "--min-price",
        type=float,
        default=5.0,
        help="Minimum last close price (default: $5)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be ingested without writing",
    )
    args = parser.parse_args()
    run_ingest(args.min_market_cap, args.min_price, args.dry_run)
