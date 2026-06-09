"""Local Parquet data store for cached OHLCV daily bars, intraday bars, ticker metadata, and options chains.

Storage layout:
  data/ohlcv_daily/{TICKER}.parquet     — one file per ticker, daily bars
  data/intraday_5min/{TICKER}.parquet   — one file per ticker, 5-min bars
  data/ticker_meta.parquet              — single file, small (~5K rows)
  data/options_chains/{TICKER}.parquet  — one file per ticker, options chain snapshots

Per-ticker partitioning gives:
  - Zero contention for parallel writes (different tickers = different files)
  - Blast radius limited to a single ticker on corruption
  - O(1) reads for a single ticker (no scanning 5M rows)
  - Safe incremental appends without read-entire-store overhead

Ticker metadata remains a single file because it is small and always read in bulk.
Options chain snapshots are accumulated over time for backtest validation.
"""

from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

from tyche.exceptions import DataStoreError, InsufficientDataError
from tyche.storage import (
    exists as storage_exists,
    parquet_num_rows,
    read_json,
    read_parquet as storage_read_parquet,
    write_json,
)
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend, context_for_data_access

if TYPE_CHECKING:
    from tyche.market_data.polygon import DailyBar, IntradayBar, PolygonClient, TickerInfo

logger = structlog.get_logger()

OHLCV_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("vwap", pa.float64()),
    ]
)

TICKER_META_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("name", pa.string()),
        ("market_cap", pa.float64()),
        ("exchange", pa.string()),
        ("type", pa.string()),
        ("last_updated", pa.date32()),
        ("institutional_pct", pa.float64()),
        ("sic_code", pa.string()),
        ("sic_description", pa.string()),
        ("sector", pa.string()),
        # Shares outstanding (Polygon weighted_shares_outstanding). Used to
        # derive a live market cap = shares x latest daily close, so market cap
        # tracks price daily instead of Polygon's months-stale market_cap field.
        ("shares_outstanding", pa.float64()),
    ]
)

_META_MIGRATE_COLS = {
    "institutional_pct": pd.NA,
    "sic_code": pd.NA,
    "sic_description": pd.NA,
    "sector": pd.NA,
    "shares_outstanding": 0.0,
}


def _auto_migrate_meta_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Add missing columns to a ticker-meta DataFrame (backward compat)."""
    for col, default in _META_MIGRATE_COLS.items():
        if col not in df.columns:
            df[col] = default
    return df


CONVICTION_SIGNAL_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("as_of_date", pa.date32()),
        ("last_close", pa.float64()),
        ("ema_8", pa.float64()),
        ("ema_21", pa.float64()),
        ("ema_8_slope", pa.float64()),
        ("ema_21_slope", pa.float64()),
        ("price_to_8ema_pct", pa.float64()),
        ("price_to_21ema_pct", pa.float64()),
        ("volume_declining_on_pullback", pa.bool_()),
        ("avg_volume_20d", pa.int64()),
        ("latest_volume", pa.int64()),
        ("days_above_both_emas", pa.int64()),
        ("prior_streak", pa.int64()),
        ("ema_50", pa.float64()),
        ("ema_50_slope", pa.float64()),
        ("rsi_14", pa.float64()),
        ("iv_rank", pa.float64()),
        ("iv_percentile", pa.float64()),
        ("atm_iv", pa.float64()),
        ("vrp", pa.float64()),
        ("csp_safety_prob", pa.float64()),
    ]
)

INTRADAY_SCHEMA = pa.schema(
    [
        ("timestamp", pa.timestamp("us")),
        ("date", pa.date32()),
        ("open", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("close", pa.float64()),
        ("volume", pa.int64()),
        ("vwap", pa.float64()),
        ("num_transactions", pa.int64()),
    ]
)

OPTIONS_CHAIN_SCHEMA = pa.schema(
    [
        ("snapshot_date", pa.date32()),
        ("expiration", pa.date32()),
        ("strike", pa.float64()),
        ("option_type", pa.string()),
        ("bid", pa.float64()),
        ("ask", pa.float64()),
        ("mid", pa.float64()),
        ("last", pa.float64()),
        ("volume", pa.int64()),
        ("open_interest", pa.int64()),
        ("implied_volatility", pa.float64()),
        ("delta", pa.float64()),
        ("gamma", pa.float64()),
        ("theta", pa.float64()),
        ("vega", pa.float64()),
        ("rho", pa.float64()),
        ("underlying_price", pa.float64()),
    ]
)


def _ticker_path(base_dir: Path, ticker: str) -> Path:
    """Return the per-ticker Parquet file path, sanitizing the symbol."""
    safe = ticker.replace("/", "_").replace("\\", "_")
    return base_dir / f"{safe}.parquet"


class _MetadataCache:
    """Lightweight JSON cache for aggregate store stats."""

    def __init__(self, rel_path: str, ctx: StorageContext) -> None:
        self._rel = rel_path
        self._ctx = ctx

    def read(self) -> dict:
        if not storage_exists(self._rel, ctx=self._ctx):
            return {}
        try:
            data = read_json(self._rel, ctx=self._ctx)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def write(self, data: dict) -> None:
        try:
            write_json(data, self._rel, atomic=False, ctx=self._ctx)
        except Exception as exc:
            logger.warning("metadata_cache_write_error", error=str(exc))

    def rebuild(
        self,
        store: StoreBackend | Path,
        dedup_col: str = "date",
    ) -> dict:
        """Full scan of all Parquet files to rebuild cache. Slow but accurate."""
        earliest: date | None = None
        latest: date | None = None
        total_rows = 0
        ticker_count = 0

        if isinstance(store, StoreBackend):
            for rel in store.iter_parquet_rels():
                try:
                    total_rows += parquet_num_rows(rel, ctx=store.ctx)
                    ticker_count += 1
                    df = storage_read_parquet(rel, columns=[dedup_col], ctx=store.ctx)
                    if df.empty:
                        continue
                    dates = pd.to_datetime(df[dedup_col]).dt.date.tolist()
                    fmin, fmax = min(dates), max(dates)
                    if earliest is None or fmin < earliest:
                        earliest = fmin
                    if latest is None or fmax > latest:
                        latest = fmax
                except Exception:
                    continue
        else:
            for path in store.glob("*.parquet"):
                try:
                    meta = pq.read_metadata(path)
                    total_rows += meta.num_rows
                    ticker_count += 1
                    table = pq.read_table(path, columns=[dedup_col])
                    if table.num_rows > 0:
                        dates = table.column(dedup_col).to_pylist()
                        fmin, fmax = min(dates), max(dates)
                        if earliest is None or fmin < earliest:
                            earliest = fmin
                        if latest is None or fmax > latest:
                            latest = fmax
                except Exception:
                    continue

        data = {
            "ticker_count": ticker_count,
            "total_rows": total_rows,
            "earliest_date": earliest.isoformat() if earliest else None,
            "latest_date": latest.isoformat() if latest else None,
        }
        self.write(data)
        return data


class OHLCVStore:
    """Manages per-ticker Parquet files of daily OHLCV data.

    Layout: data/ohlcv_daily/{TICKER}.parquet
    Each file contains only that ticker's daily bars, deduplicated on date.
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create(
            "ohlcv_daily",
            data_dir,
            ctx,
            ticker_normalize="as_is",
            upper_stems=False,
        )
        self._legacy_rel = "ohlcv_daily.parquet"
        self._cache = _MetadataCache(self._io.rel("_meta.json"), self._io.ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    @property
    def exists(self) -> bool:
        return self._io.has_any_parquet()

    @property
    def has_legacy_file(self) -> bool:
        return storage_exists(self._legacy_rel, ctx=self._io.ctx)

    def rebuild_cache(self) -> dict:
        """Force a full scan and rebuild the metadata cache."""
        return self._cache.rebuild(self._io, dedup_col="date")

    def _ensure_cache(self) -> dict:
        """Return cached metadata, rebuilding lazily if missing."""
        cached = self._cache.read()
        if cached:
            return cached
        if self.exists:
            return self.rebuild_cache()
        return {}

    def migrate_from_legacy(self) -> int:
        """Split the old single-file store into per-ticker files.

        Returns the number of ticker files created.
        """
        if not self.has_legacy_file:
            return 0

        logger.info("ohlcv_migrate_start", legacy_path=self._legacy_rel)
        df = storage_read_parquet(self._legacy_rel, ctx=self._io.ctx)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        count = 0

        for ticker, group in df.groupby("ticker"):
            ticker_str = str(ticker)
            ticker_df = (
                group.drop(columns=["ticker"])
                .drop_duplicates(subset=["date"], keep="last")
                .sort_values("date")
                .reset_index(drop=True)
            )
            self._io.write_df(
                self._io.ticker_rel(ticker_str),
                ticker_df,
                schema=OHLCV_SCHEMA,
            )
            count += 1

        legacy_local = self._io.store_dir.parent / "ohlcv_daily.parquet"
        if legacy_local.exists():
            legacy_local.rename(legacy_local.with_suffix(".parquet.bak"))
        logger.info("ohlcv_migrate_complete", tickers=count)
        self.rebuild_cache()
        return count

    def get_latest_date(self) -> date | None:
        """Return the most recent date across all ticker files (cached)."""
        cached = self._ensure_cache()
        val = cached.get("latest_date")
        if val:
            return date.fromisoformat(val) if isinstance(val, str) else val
        return None

    def get_date_range(self) -> tuple[date | None, date | None]:
        """Return (earliest, latest) dates across all ticker files (cached)."""
        if not self.exists:
            return None, None
        cached = self._ensure_cache()
        e_str, l_str = cached.get("earliest_date"), cached.get("latest_date")
        earliest = date.fromisoformat(e_str) if e_str else None
        latest = date.fromisoformat(l_str) if l_str else None
        return earliest, latest

    def get_ticker_count(self) -> int:
        """Return count of ticker files in the store (cached)."""
        cached = self._ensure_cache()
        return cached.get("ticker_count", 0)

    def write_bars(self, bars: list[DailyBar]) -> int:
        """Write daily bars, grouped by ticker into per-ticker files.

        Each ticker's file is independently read-dedup-written, so only
        that ticker's data is touched. Returns total new rows added.
        """
        if not bars:
            return 0

        by_ticker: dict[str, list[DailyBar]] = {}
        for b in bars:
            by_ticker.setdefault(b.ticker, []).append(b)

        total_added = 0
        for ticker, ticker_bars in by_ticker.items():
            new_df = pd.DataFrame(
                [
                    {
                        "date": b.date,
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                        "vwap": b.vwap,
                    }
                    for b in ticker_bars
                ]
            )
            new_df["date"] = pd.to_datetime(new_df["date"]).dt.date

            rel = self._io.ticker_rel(ticker)
            existing_df = self._io.read_df(rel)
            if existing_df is not None and not existing_df.empty:
                existing_df["date"] = pd.to_datetime(existing_df["date"]).dt.date
                combined = pd.concat([existing_df, new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["date"], keep="last")
                rows_added = len(combined) - len(existing_df)
            else:
                combined = new_df.drop_duplicates(subset=["date"], keep="last")
                rows_added = len(combined)

            combined = combined.sort_values("date").reset_index(drop=True)
            self._io.write_df(rel, combined, schema=OHLCV_SCHEMA)
            total_added += rows_added

        self._update_cache_after_write(bars, total_added, len(by_ticker))
        logger.info(
            "data_store_write",
            rows_added=total_added,
            tickers_touched=len(by_ticker),
        )
        return total_added

    def _update_cache_after_write(
        self, bars: list[DailyBar], rows_added: int, tickers_touched: int
    ) -> None:
        """Incrementally update the metadata cache after a write."""
        cached = self._cache.read()
        if not cached:
            self.rebuild_cache()
            return

        new_dates = [b.date for b in bars]
        new_min, new_max = min(new_dates), max(new_dates)

        e_str = cached.get("earliest_date")
        l_str = cached.get("latest_date")
        old_earliest = date.fromisoformat(e_str) if e_str else None
        old_latest = date.fromisoformat(l_str) if l_str else None

        earliest = min(old_earliest, new_min) if old_earliest else new_min
        latest = max(old_latest, new_max) if old_latest else new_max

        cached["earliest_date"] = earliest.isoformat()
        cached["latest_date"] = latest.isoformat()
        cached["total_rows"] = cached.get("total_rows", 0) + rows_added
        cached["ticker_count"] = len(self._io.list_ticker_stems())
        self._cache.write(cached)

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read OHLCV data for a single ticker, sorted by date ascending.

        Returns DataFrame with columns: date, open, high, low, close, volume, vwap
        """
        empty = pd.DataFrame(
            columns=["date", "open", "high", "low", "close", "volume", "vwap"]
        )
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]

        return df.sort_values("date").reset_index(drop=True)

    def read_tickers(
        self,
        tickers: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Read OHLCV data for multiple tickers at once."""
        result: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            df = self.read_ticker(ticker, start_date, end_date)
            if not df.empty:
                result[ticker] = df
        return result

    def get_all_tickers(self) -> list[str]:
        """Return sorted list of all tickers in the store."""
        return self._io.list_ticker_stems()

    def get_row_count(self) -> int:
        """Return total number of rows across all ticker files (cached)."""
        cached = self._ensure_cache()
        return cached.get("total_rows", 0)

    def read_all(self) -> pd.DataFrame:
        """Read all tickers into a single DataFrame with a 'ticker' column.

        Used by backtest and screen_universe which need cross-ticker views.
        """
        frames: list[pd.DataFrame] = []
        for rel in self._io.iter_parquet_rels():
            try:
                df = storage_read_parquet(rel, ctx=self._io.ctx)
                df["ticker"] = Path(rel).stem
                frames.append(df)
            except Exception:
                continue
        if not frames:
            return pd.DataFrame(
                columns=["ticker", "date", "open", "high", "low", "close", "volume", "vwap"]
            )
        combined = pd.concat(frames, ignore_index=True)
        combined["date"] = pd.to_datetime(combined["date"]).dt.date
        return combined

    def screen_universe(
        self,
        min_avg_volume: int = 500_000,
        min_price: float = 5.0,
        min_dollar_volume: float = 5_000_000.0,
        lookback_days: int = 20,
    ) -> list[str]:
        """Screen the stored universe locally using price and volume filters."""
        df = self.read_all()
        if df.empty:
            return []

        latest_date = df["date"].max()
        cutoff = latest_date - timedelta(days=int(lookback_days * 1.5))
        recent = df[df["date"] >= cutoff]

        stats = recent.groupby("ticker").agg(
            avg_volume=("volume", "mean"),
            last_close=("close", "last"),
            bar_count=("date", "count"),
        )

        stats["avg_dollar_vol"] = recent.groupby("ticker").apply(
            lambda g: (g["close"] * g["volume"]).mean(),
            include_groups=False,
        )

        qualified = stats[
            (stats["avg_volume"] >= min_avg_volume)
            & (stats["last_close"] >= min_price)
            & (stats["avg_dollar_vol"] >= min_dollar_volume)
            & (stats["bar_count"] >= lookback_days * 0.5)
        ]

        tickers = sorted(qualified.index.tolist())

        logger.info(
            "universe_screen_local",
            total_tickers=len(stats),
            qualified=len(tickers),
            min_avg_vol=min_avg_volume,
            min_price=min_price,
            min_dollar_vol=min_dollar_volume,
        )
        return tickers


# Polygon classifies some large-cap NASDAQ names as ADRC; treat as CS so they
# pass filter_equity_only() and participate in scanner/alpha/conviction pipelines.
EQUITY_TYPE_OVERRIDES: dict[str, str] = {
    "ARM": "CS",
}


def _apply_equity_type_overrides(df: pd.DataFrame) -> pd.DataFrame:
    """Force security type for tickers in :data:`EQUITY_TYPE_OVERRIDES`."""
    for ticker, sec_type in EQUITY_TYPE_OVERRIDES.items():
        mask = df["ticker"] == ticker
        if mask.any():
            df.loc[mask, "type"] = sec_type
    return df


class TickerMetaStore:
    """Manages persisted ticker metadata (market cap, exchange, type).

    Stored as a separate Parquet file alongside OHLCV data.
    Refreshed on each bootstrap to keep market caps current.
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("", data_dir, ctx)
        self._meta_rel = "ticker_meta.parquet"

    @property
    def parquet_path(self) -> Path:
        return self._io.store_dir / "ticker_meta.parquet"

    @property
    def exists(self) -> bool:
        return self._io.exists(self._meta_rel)

    def write_meta(self, tickers: list[TickerInfo]) -> int:
        """Write ticker metadata, upserting on ticker symbol.

        Returns the number of tickers stored.
        """
        if not tickers:
            return 0

        today = date.today()
        new_df = pd.DataFrame(
            [
                {
                    "ticker": t.ticker,
                    "name": t.name,
                    "market_cap": t.market_cap,
                    "exchange": t.primary_exchange,
                    "type": t.type,
                    "last_updated": today,
                    "institutional_pct": None,
                    "sic_code": None,
                    "sic_description": None,
                    "sector": None,
                    "shares_outstanding": 0.0,
                }
                for t in tickers
            ]
        )

        if self.exists:
            existing_df = self._io.read_df(self._meta_rel)
            assert existing_df is not None
            existing_df = _auto_migrate_meta_columns(existing_df)

            # Build preservation maps before overwrite: keep existing positive
            # market_cap and non-null institutional_pct when incoming row has
            # zero / NaN (the Polygon list API omits market_cap).
            cap_map = dict(
                existing_df[existing_df["market_cap"] > 0][
                    ["ticker", "market_cap"]
                ].values
            )
            inst_map = dict(
                existing_df.dropna(subset=["institutional_pct"])[
                    ["ticker", "institutional_pct"]
                ].values
            )
            sic_map = dict(
                existing_df.dropna(subset=["sic_code"])[
                    ["ticker", "sic_code"]
                ].values
            )
            sic_desc_map = dict(
                existing_df.dropna(subset=["sic_description"])[
                    ["ticker", "sic_description"]
                ].values
            )
            sector_map = dict(
                existing_df.dropna(subset=["sector"])[
                    ["ticker", "sector"]
                ].values
            )
            shares_map = (
                dict(
                    existing_df[existing_df["shares_outstanding"] > 0][
                        ["ticker", "shares_outstanding"]
                    ].values
                )
                if "shares_outstanding" in existing_df.columns
                else {}
            )

            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["ticker"], keep="last")

            for ticker, cap in cap_map.items():
                mask = (combined["ticker"] == ticker) & (combined["market_cap"] <= 0)
                combined.loc[mask, "market_cap"] = cap
            for ticker, pct in inst_map.items():
                mask = (combined["ticker"] == ticker) & combined["institutional_pct"].isna()
                combined.loc[mask, "institutional_pct"] = pct
            for ticker, sic in sic_map.items():
                mask = (combined["ticker"] == ticker) & combined["sic_code"].isna()
                combined.loc[mask, "sic_code"] = sic
            for ticker, desc in sic_desc_map.items():
                mask = (combined["ticker"] == ticker) & combined["sic_description"].isna()
                combined.loc[mask, "sic_description"] = desc
            for ticker, sec in sector_map.items():
                mask = (combined["ticker"] == ticker) & combined["sector"].isna()
                combined.loc[mask, "sector"] = sec
            for ticker, sh in shares_map.items():
                mask = (combined["ticker"] == ticker) & (
                    combined["shares_outstanding"] <= 0
                )
                combined.loc[mask, "shares_outstanding"] = sh
        else:
            combined = new_df.drop_duplicates(subset=["ticker"], keep="last")

        combined = combined.sort_values("ticker").reset_index(drop=True)
        combined["last_updated"] = pd.to_datetime(combined["last_updated"]).dt.date
        if "institutional_pct" in combined.columns:
            combined["institutional_pct"] = pd.to_numeric(
                combined["institutional_pct"], errors="coerce"
            )
        combined["shares_outstanding"] = pd.to_numeric(
            combined.get("shares_outstanding"), errors="coerce"
        ).fillna(0.0)
        combined = _apply_equity_type_overrides(combined)

        self._io.write_df(self._meta_rel, combined, schema=TICKER_META_SCHEMA)

        logger.info(
            "ticker_meta_write",
            tickers_stored=len(combined),
            path=str(self.parquet_path),
        )
        return len(combined)

    def read_meta(self) -> pd.DataFrame:
        """Read all ticker metadata."""
        if not self.exists:
            return pd.DataFrame(
                columns=[
                    "ticker", "name", "market_cap", "exchange", "type",
                    "last_updated", "institutional_pct",
                    "sic_code", "sic_description", "sector", "shares_outstanding",
                ]
            )
        df = self._io.read_df(self._meta_rel)
        assert df is not None
        return _auto_migrate_meta_columns(df)

    def get_market_caps(self, tickers: list[str] | None = None) -> dict[str, float]:
        """Return a ticker -> market_cap mapping.

        Args:
            tickers: Optional filter. If None, returns all.
        """
        if not self.exists:
            return {}

        df = self._io.read_df(self._meta_rel, columns=["ticker", "market_cap"])
        assert df is not None
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        return dict(zip(df["ticker"], df["market_cap"]))

    def get_shares_outstanding(
        self, tickers: list[str] | None = None
    ) -> dict[str, float]:
        """Return a ticker -> shares_outstanding mapping (only positive values)."""
        if not self.exists:
            return {}
        try:
            df = self._io.read_df(
                self._meta_rel, columns=["ticker", "shares_outstanding"]
            )
            if df is None:
                return {}
        except (ValueError, KeyError):
            return {}
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        df = df[df["shares_outstanding"] > 0]
        return dict(zip(df["ticker"], df["shares_outstanding"]))

    def update_shares_outstanding(self, shares: dict[str, float]) -> int:
        """Bulk-update shares outstanding for existing tickers.

        Returns the number of tickers updated.
        """
        if not self.exists or not shares:
            return 0

        df = self._io.read_df(self._meta_rel)
        assert df is not None
        df = _auto_migrate_meta_columns(df)
        updated = 0
        for ticker, sh in shares.items():
            if sh is None or sh <= 0:
                continue
            mask = df["ticker"] == ticker
            if mask.any():
                df.loc[mask, "shares_outstanding"] = float(sh)
                updated += 1

        df["last_updated"] = pd.to_datetime(df["last_updated"]).dt.date
        self._io.write_df(self._meta_rel, df, schema=TICKER_META_SCHEMA)

        logger.info("ticker_meta_shares_updated", updated=updated, total=len(shares))
        return updated

    def get_exchanges(self, tickers: list[str] | None = None) -> dict[str, str]:
        """Return a ticker -> exchange mapping."""
        if not self.exists:
            return {}

        df = self._io.read_df(self._meta_rel, columns=["ticker", "exchange"])
        assert df is not None
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        return dict(zip(df["ticker"], df["exchange"]))

    def update_market_caps(self, caps: dict[str, float]) -> int:
        """Bulk-update market caps for existing tickers.

        Returns the number of tickers updated.
        """
        if not self.exists or not caps:
            return 0

        df = self._io.read_df(self._meta_rel)
        assert df is not None
        df = _auto_migrate_meta_columns(df)
        updated = 0
        for ticker, cap in caps.items():
            mask = df["ticker"] == ticker
            if mask.any():
                df.loc[mask, "market_cap"] = cap
                updated += 1

        df["last_updated"] = pd.to_datetime(df["last_updated"]).dt.date
        self._io.write_df(self._meta_rel, df, schema=TICKER_META_SCHEMA)

        logger.info("ticker_meta_caps_updated", updated=updated, total_caps=len(caps))
        return updated

    def get_ticker_types(self, tickers: list[str] | None = None) -> dict[str, str]:
        """Return a ticker -> type mapping (e.g. 'CS' for common stock, 'ETF')."""
        if not self.exists:
            return {}

        df = self._io.read_df(self._meta_rel, columns=["ticker", "type"])
        assert df is not None
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        return dict(zip(df["ticker"], df["type"]))

    def filter_equity_only(self, tickers: list[str]) -> list[str]:
        """Return only tickers present in the meta store with type 'CS' (common stock).

        Tickers not in the meta store or with non-CS type are excluded.
        """
        if not self.exists:
            return tickers

        types = self.get_ticker_types(tickers)
        return [t for t in tickers if types.get(t) == "CS"]

    def get_institutional_pcts(self, tickers: list[str] | None = None) -> dict[str, float]:
        """Return a ticker -> institutional_pct mapping from persisted data."""
        if not self.exists:
            return {}

        cols = ["ticker", "institutional_pct"]
        try:
            df = self._io.read_df(self._meta_rel, columns=cols)
            if df is None:
                return {}
        except Exception:
            return {}
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        df = df.dropna(subset=["institutional_pct"])
        return dict(zip(df["ticker"], df["institutional_pct"]))

    def update_institutional_pcts(self, pcts: dict[str, float]) -> int:
        """Bulk-update institutional ownership percentages for existing tickers.

        Returns the number of tickers updated.
        """
        if not self.exists or not pcts:
            return 0

        df = self._io.read_df(self._meta_rel)
        assert df is not None
        df = _auto_migrate_meta_columns(df)

        updated = 0
        for ticker, pct in pcts.items():
            mask = df["ticker"] == ticker
            if mask.any():
                df.loc[mask, "institutional_pct"] = pct
                updated += 1

        df["last_updated"] = pd.to_datetime(df["last_updated"]).dt.date
        if "institutional_pct" in df.columns:
            df["institutional_pct"] = pd.to_numeric(df["institutional_pct"], errors="coerce")
        self._io.write_df(self._meta_rel, df, schema=TICKER_META_SCHEMA)

        logger.info("ticker_meta_institutional_updated", updated=updated, total=len(pcts))
        return updated

    def get_sectors(self, tickers: list[str] | None = None) -> dict[str, str]:
        """Return a ticker -> sector mapping from persisted data."""
        if not self.exists:
            return {}

        try:
            df = self._io.read_df(self._meta_rel, columns=["ticker", "sector"])
            if df is None:
                return {}
        except Exception:
            return {}
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        df = df.dropna(subset=["sector"])
        return dict(zip(df["ticker"], df["sector"]))

    def get_names(self, tickers: list[str] | None = None) -> dict[str, str]:
        """Return a ticker -> name mapping from persisted data."""
        if not self.exists:
            return {}

        try:
            df = self._io.read_df(self._meta_rel, columns=["ticker", "name"])
            if df is None:
                return {}
        except Exception:
            return {}
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        df = df.dropna(subset=["name"])
        return dict(zip(df["ticker"], df["name"]))

    def get_meta_batch(self, tickers: list[str]) -> dict[str, dict]:
        """Return a ticker -> {market_cap, exchange, name, sector} mapping.

        Vectorized — reads only the columns needed, filters to the
        requested tickers, and converts NaN to None for Pydantic compat.
        """
        if not self.exists or not tickers:
            return {}

        cols = ["ticker", "market_cap", "exchange", "name", "sector"]
        try:
            df = self._io.read_df(self._meta_rel, columns=cols)
            if df is None:
                return {}
        except Exception:
            return {}

        df = df[df["ticker"].isin(tickers)]

        result: dict[str, dict] = {}
        for row in df.itertuples(index=False):
            result[row.ticker] = {
                "market_cap": None if pd.isna(row.market_cap) else row.market_cap,
                "exchange": row.exchange if pd.notna(row.exchange) else "",
                "name": row.name if pd.notna(row.name) else "",
                "sector": None if pd.isna(row.sector) else row.sector,
            }
        return result

    def update_sic_data(
        self, sic_data: dict[str, tuple[str, str, str | None]]
    ) -> int:
        """Bulk-update SIC code, description, and derived sector.

        Args:
            sic_data: Mapping of ticker -> (sic_code, sic_description, sector).

        Returns the number of tickers updated.
        """
        if not self.exists or not sic_data:
            return 0

        df = self._io.read_df(self._meta_rel)
        assert df is not None
        df = _auto_migrate_meta_columns(df)

        updated = 0
        for ticker, (sic_code, sic_desc, sector) in sic_data.items():
            mask = df["ticker"] == ticker
            if mask.any():
                df.loc[mask, "sic_code"] = sic_code
                df.loc[mask, "sic_description"] = sic_desc
                if sector:
                    df.loc[mask, "sector"] = sector
                updated += 1

        df["last_updated"] = pd.to_datetime(df["last_updated"]).dt.date
        self._io.write_df(self._meta_rel, df, schema=TICKER_META_SCHEMA)

        logger.info("ticker_meta_sic_updated", updated=updated, total=len(sic_data))
        return updated

    def get_ticker_count(self) -> int:
        """Return count of tickers in the metadata store."""
        if not self.exists:
            return 0
        return parquet_num_rows(self._meta_rel, ctx=self._io.ctx)


class ConvictionSignalStore:
    """Disk cache for conviction engine EMA data.

    Persists only the data-derived fields (EMAs, slopes, volumes, streaks)
    as a single Parquet file.  Config-dependent fields (trend_state,
    csp_eligible, conviction_level, gate_results) are recomputed on load
    using the current engine settings, so config changes take effect
    without needing a cache flush.

    Eviction policy:
      - New OHLCV date: date mismatch on read → returns None, caller recomputes
      - Explicit invalidation: ``clear()`` deletes the file
      - Config changes: no eviction needed (gates recomputed on load)
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._parquet_path = self._data_dir / "conviction_signals.parquet"

    @property
    def parquet_path(self) -> Path:
        return self._parquet_path

    @property
    def exists(self) -> bool:
        return self._parquet_path.exists()

    def get_cached_date(self) -> date | None:
        """Return the as_of_date stored in the cache, or None if empty."""
        if not self.exists:
            return None
        try:
            table = pq.read_table(self._parquet_path, columns=["as_of_date"])
            if table.num_rows == 0:
                return None
            dates = table.column("as_of_date").to_pylist()
            return max(dates)
        except Exception:
            return None

    def write_signals(self, signals: list) -> int:
        """Persist conviction EMA data to disk.  Overwrites the entire file.

        Args:
            signals: List of ``ConvictionSignal`` dataclass instances.

        Returns:
            Number of signals written.
        """
        if not signals:
            return 0

        rows = []
        for sig in signals:
            if sig.as_of_date is None:
                continue
            rows.append({
                "ticker": sig.ticker,
                "as_of_date": sig.as_of_date,
                "last_close": sig.last_close,
                "ema_8": sig.ema_8,
                "ema_21": sig.ema_21,
                "ema_8_slope": sig.ema_8_slope,
                "ema_21_slope": sig.ema_21_slope,
                "price_to_8ema_pct": sig.price_to_8ema_pct,
                "price_to_21ema_pct": sig.price_to_21ema_pct,
                "volume_declining_on_pullback": sig.volume_declining_on_pullback,
                "avg_volume_20d": sig.avg_volume_20d,
                "latest_volume": sig.latest_volume,
                "days_above_both_emas": sig.days_above_both_emas,
                "prior_streak": sig.prior_streak,
                "ema_50": getattr(sig, "ema_50", 0.0),
                "ema_50_slope": getattr(sig, "ema_50_slope", 0.0),
                "rsi_14": getattr(sig, "rsi_14", 0.0),
                "iv_rank": getattr(sig, "iv_rank", None),
                "iv_percentile": getattr(sig, "iv_percentile", None),
                "atm_iv": getattr(sig, "atm_iv", None),
                "vrp": getattr(sig, "vrp", None),
                "csp_safety_prob": getattr(sig, "csp_safety_prob", None),
            })

        if not rows:
            return 0

        df = pd.DataFrame(rows)
        df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
        df = df.sort_values("ticker").reset_index(drop=True)
        table = pa.Table.from_pandas(df, schema=CONVICTION_SIGNAL_SCHEMA)
        pq.write_table(table, self._parquet_path, compression="snappy")

        logger.info(
            "conviction_signal_store_write",
            signals=len(rows),
            path=str(self._parquet_path),
        )
        return len(rows)

    def read_signals(self, as_of_date: date) -> list[dict] | None:
        """Read cached EMA data for a given date.

        Returns a list of row dicts if the file contains data for
        ``as_of_date``, or ``None`` on cache miss (file absent, empty,
        or date mismatch).
        """
        if not self.exists:
            return None

        try:
            df = pd.read_parquet(self._parquet_path)
            df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date
            df = df[df["as_of_date"] == as_of_date]
            if df.empty:
                return None
            return df.to_dict("records")
        except Exception:
            logger.warning("conviction_signal_store_read_error", exc_info=True)
            return None

    def clear(self) -> None:
        """Delete the cache file (called on OHLCV update or explicit invalidation)."""
        if self.exists:
            self._parquet_path.unlink()
            logger.info("conviction_signal_store_cleared")


class IntradayStore:
    """Manages per-ticker Parquet files of 5-minute intraday OHLCV bars.

    Layout: data/intraday_5min/{TICKER}.parquet
    Each file contains only that ticker's intraday bars, deduplicated on timestamp.
    Per-ticker partitioning enables parallel writes and limits blast radius.
    """

    def __init__(
        self,
        data_dir: str = "data",
        multiplier: int = 5,
        ctx: StorageContext | None = None,
    ) -> None:
        self._ctx = context_for_data_access(data_dir, ctx)
        self._multiplier = multiplier
        local_root = self._ctx.local_root or Path(data_dir)
        self._data_dir = local_root
        self._store_dir = local_root / f"intraday_{multiplier}min"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_path = local_root / f"intraday_{multiplier}min.parquet"
        self._cache = _MetadataCache(
            f"intraday_{multiplier}min/_meta.json",
            self._ctx,
        )

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    @property
    def exists(self) -> bool:
        return any(self._store_dir.glob("*.parquet"))

    @property
    def has_legacy_file(self) -> bool:
        return self._legacy_path.exists()

    def rebuild_cache(self) -> dict:
        """Force a full scan and rebuild the metadata cache."""
        return self._cache.rebuild(self._store_dir, dedup_col="date")

    def _ensure_cache(self) -> dict:
        """Return cached metadata, rebuilding lazily if missing."""
        cached = self._cache.read()
        if cached:
            return cached
        if self.exists:
            return self.rebuild_cache()
        return {}

    def migrate_from_legacy(self) -> int:
        """Split the old single-file store into per-ticker files.

        Returns the number of ticker files created.
        """
        if not self._legacy_path.exists():
            return 0

        logger.info("intraday_migrate_start", legacy_path=str(self._legacy_path))
        df = pd.read_parquet(self._legacy_path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["date"] = pd.to_datetime(df["date"]).dt.date
        count = 0

        for ticker, group in df.groupby("ticker"):
            ticker_str = str(ticker)
            ticker_df = (
                group.drop(columns=["ticker"])
                .drop_duplicates(subset=["timestamp"], keep="last")
                .sort_values("timestamp")
                .reset_index(drop=True)
            )
            path = _ticker_path(self._store_dir, ticker_str)
            table = pa.Table.from_pandas(ticker_df, schema=INTRADAY_SCHEMA)
            pq.write_table(table, path, compression="snappy")
            count += 1

        self._legacy_path.rename(self._legacy_path.with_suffix(".parquet.bak"))
        logger.info("intraday_migrate_complete", tickers=count)
        self.rebuild_cache()
        return count

    def _ticker_path(self, ticker: str) -> Path:
        return _ticker_path(self._store_dir, ticker)

    def get_date_range(self) -> tuple[date | None, date | None]:
        """Return (earliest, latest) dates across all ticker files (cached)."""
        if not self.exists:
            return None, None
        cached = self._ensure_cache()
        e_str, l_str = cached.get("earliest_date"), cached.get("latest_date")
        earliest = date.fromisoformat(e_str) if e_str else None
        latest = date.fromisoformat(l_str) if l_str else None
        return earliest, latest

    def get_latest_date(self) -> date | None:
        """Return the most recent date in the store (cached)."""
        cached = self._ensure_cache()
        val = cached.get("latest_date")
        if val:
            return date.fromisoformat(val) if isinstance(val, str) else val
        return None

    def get_ticker_count(self) -> int:
        """Return count of ticker files in the store (cached)."""
        cached = self._ensure_cache()
        return cached.get("ticker_count", 0)

    def get_row_count(self) -> int:
        """Return total number of rows across all ticker files (cached)."""
        cached = self._ensure_cache()
        return cached.get("total_rows", 0)

    def get_tickers(self) -> list[str]:
        """Return sorted list of tickers with intraday data."""
        return sorted(
            p.stem for p in self._store_dir.glob("*.parquet")
        )

    def write_bars(self, bars: list[IntradayBar]) -> int:
        """Write intraday bars, grouped by ticker into per-ticker files.

        Each ticker's file is independently read-dedup-written.
        Returns total new rows added.
        """
        if not bars:
            return 0

        by_ticker: dict[str, list[IntradayBar]] = {}
        for b in bars:
            by_ticker.setdefault(b.ticker, []).append(b)

        total_added = 0
        for ticker, ticker_bars in by_ticker.items():
            new_df = pd.DataFrame(
                [
                    {
                        "timestamp": b.timestamp,
                        "date": b.timestamp.date(),
                        "open": b.open,
                        "high": b.high,
                        "low": b.low,
                        "close": b.close,
                        "volume": b.volume,
                        "vwap": b.vwap,
                        "num_transactions": b.num_transactions,
                    }
                    for b in ticker_bars
                ]
            )
            new_df["timestamp"] = pd.to_datetime(new_df["timestamp"])
            new_df["date"] = pd.to_datetime(new_df["date"]).dt.date

            path = self._ticker_path(ticker)
            if path.exists():
                existing_df = pd.read_parquet(path)
                existing_df["timestamp"] = pd.to_datetime(existing_df["timestamp"])
                existing_df["date"] = pd.to_datetime(existing_df["date"]).dt.date
                combined = pd.concat([existing_df, new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["timestamp"], keep="last")
                rows_added = len(combined) - len(existing_df)
            else:
                combined = new_df.drop_duplicates(subset=["timestamp"], keep="last")
                rows_added = len(combined)

            combined = combined.sort_values("timestamp").reset_index(drop=True)
            table = pa.Table.from_pandas(combined, schema=INTRADAY_SCHEMA)
            pq.write_table(table, path, compression="snappy")
            total_added += rows_added

        self._update_cache_after_write(bars, total_added, len(by_ticker))
        logger.info(
            "intraday_store_write",
            rows_added=total_added,
            tickers_touched=len(by_ticker),
        )
        return total_added

    def _update_cache_after_write(
        self, bars: list[IntradayBar], rows_added: int, tickers_touched: int
    ) -> None:
        """Incrementally update the metadata cache after a write."""
        cached = self._cache.read()
        if not cached:
            self.rebuild_cache()
            return

        new_dates = [b.timestamp.date() for b in bars]
        new_min, new_max = min(new_dates), max(new_dates)

        e_str = cached.get("earliest_date")
        l_str = cached.get("latest_date")
        old_earliest = date.fromisoformat(e_str) if e_str else None
        old_latest = date.fromisoformat(l_str) if l_str else None

        earliest = min(old_earliest, new_min) if old_earliest else new_min
        latest = max(old_latest, new_max) if old_latest else new_max

        cached["earliest_date"] = earliest.isoformat()
        cached["latest_date"] = latest.isoformat()
        cached["total_rows"] = cached.get("total_rows", 0) + rows_added
        cached["ticker_count"] = len(list(self._store_dir.glob("*.parquet")))
        self._cache.write(cached)

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read intraday bars for a single ticker, sorted by timestamp.

        Returns DataFrame with columns:
            timestamp, date, open, high, low, close, volume, vwap, num_transactions
        """
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(
                columns=[
                    "timestamp", "date", "open", "high", "low",
                    "close", "volume", "vwap", "num_transactions",
                ]
            )

        df = pd.read_parquet(path)
        df["timestamp"] = pd.to_datetime(df["timestamp"])
        df["date"] = pd.to_datetime(df["date"]).dt.date
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]

        return df.sort_values("timestamp").reset_index(drop=True)

    def read_tickers(
        self,
        tickers: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Read intraday bars for multiple tickers at once."""
        result: dict[str, pd.DataFrame] = {}
        for ticker in tickers:
            df = self.read_ticker(ticker, start_date, end_date)
            if not df.empty:
                result[ticker] = df
        return result

    def get_dates_for_ticker(self, ticker: str) -> list[date]:
        """Return sorted list of dates with data for a given ticker."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return []
        try:
            table = pq.read_table(path, columns=["date"])
            dates = pd.to_datetime(
                pd.Series(table.column("date").to_pylist())
            ).dt.date.unique()
            return sorted(dates)
        except Exception:
            return []


class OptionsChainStore:
    """Manages per-ticker Parquet files of options chain snapshots.

    Layout: data/options_chains/{TICKER}.parquet
    Each file contains timestamped snapshots of that ticker's options chains,
    deduplicated on (snapshot_date, expiration, strike, option_type).

    Designed for quarterly ingestion to build a historical dataset of real
    market premiums for backtest validation.  Cloud-ready: the directory
    can be synced to GCS/S3 as-is.
    """

    DEDUP_COLS = ["snapshot_date", "expiration", "strike", "option_type"]

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._ctx = context_for_data_access(data_dir, ctx)
        local_root = self._ctx.local_root or Path(data_dir)
        self._data_dir = local_root
        self._store_dir = local_root / "options_chains"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._cache = _MetadataCache("options_chains/_meta.json", self._ctx)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    @property
    def exists(self) -> bool:
        return any(self._store_dir.glob("*.parquet"))

    def rebuild_cache(self) -> dict:
        """Full scan of all Parquet files to rebuild the metadata cache."""
        return self._cache.rebuild(self._store_dir, dedup_col="snapshot_date")

    def _ticker_path(self, ticker: str) -> Path:
        return _ticker_path(self._store_dir, ticker)

    def write_chains(
        self,
        ticker: str,
        snapshot_date: date,
        contracts: list[dict],
        underlying_price: float,
    ) -> int:
        """Append an options chain snapshot for a ticker.

        Args:
            ticker: Underlying symbol.
            snapshot_date: Date the chain was captured.
            contracts: List of contract dicts with keys matching OptionContract fields.
            underlying_price: Underlying price at time of snapshot.

        Returns:
            Number of new rows added.
        """
        if not contracts:
            return 0

        rows = []
        for c in contracts:
            exp = c.get("expiration")
            if isinstance(exp, str):
                exp = datetime.strptime(exp, "%Y-%m-%d").date()
            rows.append({
                "snapshot_date": snapshot_date,
                "expiration": exp,
                "strike": float(c.get("strike", 0)),
                "option_type": c.get("option_type", ""),
                "bid": float(c.get("bid", 0)),
                "ask": float(c.get("ask", 0)),
                "mid": float(c.get("mid", 0)),
                "last": float(c.get("last", 0)),
                "volume": int(c.get("volume", 0)),
                "open_interest": int(c.get("open_interest", 0)),
                "implied_volatility": float(c.get("implied_volatility", 0)),
                "delta": float(c.get("delta", 0)),
                "gamma": float(c.get("gamma", 0)),
                "theta": float(c.get("theta", 0)),
                "vega": float(c.get("vega", 0)),
                "rho": float(c.get("rho", 0)),
                "underlying_price": underlying_price,
            })

        new_df = pd.DataFrame(rows)
        new_df["snapshot_date"] = pd.to_datetime(new_df["snapshot_date"]).dt.date
        new_df["expiration"] = pd.to_datetime(new_df["expiration"]).dt.date

        path = self._ticker_path(ticker)
        if path.exists():
            existing_df = pd.read_parquet(path)
            existing_df["snapshot_date"] = pd.to_datetime(existing_df["snapshot_date"]).dt.date
            existing_df["expiration"] = pd.to_datetime(existing_df["expiration"]).dt.date
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=self.DEDUP_COLS, keep="last")
            rows_added = len(combined) - len(existing_df)
        else:
            combined = new_df.drop_duplicates(subset=self.DEDUP_COLS, keep="last")
            rows_added = len(combined)

        combined = combined.sort_values(
            ["snapshot_date", "expiration", "strike", "option_type"]
        ).reset_index(drop=True)
        table = pa.Table.from_pandas(combined, schema=OPTIONS_CHAIN_SCHEMA)
        pq.write_table(table, path, compression="snappy")

        logger.debug(
            "options_chain_write",
            ticker=ticker,
            snapshot_date=str(snapshot_date),
            contracts=len(rows),
            rows_added=rows_added,
        )
        return rows_added

    def read_ticker(
        self,
        ticker: str,
        snapshot_date: date | None = None,
        option_type: str | None = None,
    ) -> pd.DataFrame:
        """Read options chain data for a ticker.

        Args:
            ticker: Underlying symbol.
            snapshot_date: Filter to specific snapshot date.
            option_type: Filter to 'put' or 'call'.

        Returns:
            DataFrame with OPTIONS_CHAIN_SCHEMA columns.
        """
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in OPTIONS_CHAIN_SCHEMA])

        df = pd.read_parquet(path)
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
        df["expiration"] = pd.to_datetime(df["expiration"]).dt.date

        if snapshot_date is not None:
            df = df[df["snapshot_date"] == snapshot_date]
        if option_type is not None:
            df = df[df["option_type"] == option_type]

        return df.sort_values(
            ["snapshot_date", "expiration", "strike"]
        ).reset_index(drop=True)

    def get_nearest_snapshot_date(
        self, ticker: str, target_date: date
    ) -> date | None:
        """Find the closest snapshot date to target_date for a ticker."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return None

        try:
            table = pq.read_table(path, columns=["snapshot_date"])
            dates = pd.to_datetime(
                pd.Series(table.column("snapshot_date").to_pylist())
            ).dt.date.unique()
        except Exception:
            return None

        if len(dates) == 0:
            return None

        sorted_dates = sorted(dates)
        best = min(sorted_dates, key=lambda d: abs((d - target_date).days))
        return best

    def get_put_premium(
        self,
        ticker: str,
        snapshot_date: date,
        target_strike: float,
        target_expiration: date | None = None,
        tolerance_pct: float = 2.0,
    ) -> dict | None:
        """Look up the closest OTM put premium for backtest use.

        Args:
            ticker: Underlying symbol.
            snapshot_date: Which snapshot to query.
            target_strike: Desired strike price.
            target_expiration: Desired expiration (nearest if None).
            tolerance_pct: Max % deviation from target_strike to accept a match.

        Returns:
            Dict with bid, ask, mid, strike, expiration, iv, delta or None.
        """
        df = self.read_ticker(ticker, snapshot_date=snapshot_date, option_type="put")
        if df.empty:
            return None

        if target_expiration is not None:
            exp_df = df[df["expiration"] == target_expiration]
            if exp_df.empty:
                exps = sorted(df["expiration"].unique())
                nearest_exp = min(exps, key=lambda e: abs((e - target_expiration).days))
                df = df[df["expiration"] == nearest_exp]
            else:
                df = exp_df
        else:
            nearest_exp = df["expiration"].min()
            df = df[df["expiration"] == nearest_exp]

        if df.empty:
            return None

        df = df.copy()
        df["strike_diff_pct"] = ((df["strike"] - target_strike) / target_strike * 100).abs()
        within_tol = df[df["strike_diff_pct"] <= tolerance_pct]

        if within_tol.empty:
            return None

        best = within_tol.loc[within_tol["strike_diff_pct"].idxmin()]
        return {
            "bid": float(best["bid"]),
            "ask": float(best["ask"]),
            "mid": float(best["mid"]),
            "strike": float(best["strike"]),
            "expiration": best["expiration"],
            "implied_volatility": float(best["implied_volatility"]),
            "delta": float(best["delta"]),
            "underlying_price": float(best["underlying_price"]),
        }

    def list_tickers(self) -> list[str]:
        """Return sorted list of tickers with options chain data."""
        return sorted(p.stem for p in self._store_dir.glob("*.parquet"))

    def list_snapshot_dates(self, ticker: str | None = None) -> list[date]:
        """Return sorted list of unique snapshot dates.

        If ticker is specified, only dates for that ticker.
        Otherwise, union of all dates across all tickers.
        """
        if ticker:
            path = self._ticker_path(ticker)
            if not path.exists():
                return []
            try:
                table = pq.read_table(path, columns=["snapshot_date"])
                dates = pd.to_datetime(
                    pd.Series(table.column("snapshot_date").to_pylist())
                ).dt.date.unique()
                return sorted(dates)
            except Exception:
                return []

        all_dates: set[date] = set()
        for path in self._store_dir.glob("*.parquet"):
            try:
                table = pq.read_table(path, columns=["snapshot_date"])
                dates = pd.to_datetime(
                    pd.Series(table.column("snapshot_date").to_pylist())
                ).dt.date.unique()
                all_dates.update(dates)
            except Exception:
                continue
        return sorted(all_dates)

    def get_ticker_count(self) -> int:
        """Return count of tickers with options chain data."""
        return len(list(self._store_dir.glob("*.parquet")))

    def get_stats(self) -> dict:
        """Return aggregate stats about the options chain store."""
        cached = self._cache.read()
        if cached:
            return cached

        total_rows = 0
        ticker_count = 0
        all_dates: set[date] = set()

        for path in self._store_dir.glob("*.parquet"):
            try:
                meta = pq.read_metadata(path)
                total_rows += meta.num_rows
                ticker_count += 1
                table = pq.read_table(path, columns=["snapshot_date"])
                dates = pd.to_datetime(
                    pd.Series(table.column("snapshot_date").to_pylist())
                ).dt.date.unique()
                all_dates.update(dates)
            except Exception:
                continue

        stats = {
            "ticker_count": ticker_count,
            "total_rows": total_rows,
            "snapshot_dates": len(all_dates),
            "earliest_date": min(all_dates).isoformat() if all_dates else None,
            "latest_date": max(all_dates).isoformat() if all_dates else None,
        }
        self._cache.write(stats)
        return stats


async def _backfill_market_caps(
    polygon: PolygonClient,
    meta_store: TickerMetaStore,
    concurrency: int = 20,
    rate_limit_rpm: int = 500,
) -> int:
    """Backfill market caps for tickers that have market_cap == 0.

    Called automatically after write_meta() to populate caps from the
    per-ticker detail endpoint, since the list endpoint omits market_cap.

    Returns the number of tickers updated.
    """
    caps = meta_store.get_market_caps()
    missing = [t for t, c in caps.items() if c <= 0]

    if not missing:
        logger.info("market_cap_backfill_skipped", reason="all_caps_present")
        return 0

    logger.info(
        "market_cap_backfill_start",
        total_tickers=len(caps),
        missing=len(missing),
        concurrency=concurrency,
        rate_limit_rpm=rate_limit_rpm,
    )

    fetched = await polygon.get_batch_market_caps_concurrent(
        missing,
        concurrency=concurrency,
        rate_limit_rpm=rate_limit_rpm,
    )

    updated = 0
    if fetched:
        updated = meta_store.update_market_caps(fetched)

    logger.info(
        "market_cap_backfill_complete",
        fetched=len(fetched),
        updated=updated,
        still_missing=len(missing) - len(fetched),
    )
    return updated


async def _backfill_sic_data(
    polygon: PolygonClient,
    meta_store: TickerMetaStore,
    concurrency: int = 20,
    rate_limit_rpm: int = 500,
) -> int:
    """Backfill SIC code, description, and sector for tickers missing sector data.

    Uses the same `/v3/reference/tickers/{ticker}` endpoint as market cap backfill
    but extracts `sic_code` and `sic_description`. Also updates market caps if missing.

    Returns the number of tickers updated.
    """
    from tyche.market_data.sic_sectors import sic_to_sector

    sectors = meta_store.get_sectors()
    caps = meta_store.get_market_caps()
    all_tickers = list(caps.keys()) if caps else []
    missing = [t for t in all_tickers if t not in sectors]

    if not missing:
        logger.info("sic_backfill_skipped", reason="all_sectors_present")
        return 0

    logger.info(
        "sic_backfill_start",
        total_tickers=len(all_tickers),
        missing=len(missing),
        concurrency=concurrency,
        rate_limit_rpm=rate_limit_rpm,
    )

    details = await polygon.get_batch_ticker_details_concurrent(
        missing,
        concurrency=concurrency,
        rate_limit_rpm=rate_limit_rpm,
    )

    sic_data: dict[str, tuple[str, str, str | None]] = {}
    cap_updates: dict[str, float] = {}
    for ticker, info in details.items():
        sic_code = info.get("sic_code")
        sic_desc = info.get("sic_description")
        if sic_code:
            sector = sic_to_sector(sic_code)
            sic_data[ticker] = (sic_code, sic_desc or "", sector)
        cap = info.get("market_cap", 0)
        if cap and cap > 0 and caps.get(ticker, 0) <= 0:
            cap_updates[ticker] = cap

    updated = 0
    if sic_data:
        updated = meta_store.update_sic_data(sic_data)
    if cap_updates:
        meta_store.update_market_caps(cap_updates)

    logger.info(
        "sic_backfill_complete",
        fetched=len(details),
        sic_updated=updated,
        caps_updated=len(cap_updates),
        still_missing=len(missing) - len(sic_data),
    )
    return updated


def recompute_market_caps_from_shares(
    meta_store: TickerMetaStore,
    ohlcv_store: "OHLCVStore",
    tickers: list[str] | None = None,
    *,
    progress_job: str | None = None,
) -> int:
    """Derive a live market cap = shares_outstanding x latest daily close.

    Writes the result back into ticker_meta's ``market_cap`` so every consumer
    (conviction, scanner, alpha) reads a price-current value without code
    changes. Tickers with no stored shares are left untouched (they keep their
    prior Polygon market cap as a fallback). No network calls — uses local
    shares (refreshed weekly) and the already-daily OHLCV close.

    Returns the number of tickers updated.
    """
    shares = meta_store.get_shares_outstanding(tickers)
    if not shares:
        logger.info("market_cap_recompute_skipped", reason="no_shares_data")
        return 0

    cap_updates: dict[str, float] = {}
    share_items = list(shares.items())
    total = len(share_items)
    import time

    prog_start = time.monotonic()
    if progress_job:
        from tyche.ops.job_progress import log_job_phase, log_job_progress

        log_job_phase(progress_job, "recompute_market_caps", tickers_with_shares=total)
    for i, (ticker, sh) in enumerate(share_items, start=1):
        if sh is None or sh <= 0:
            continue
        try:
            df = ohlcv_store.read_ticker(ticker)
        except Exception:
            continue
        if df is None or df.empty or "close" not in df.columns:
            continue
        try:
            close = float(df.sort_values("date")["close"].iloc[-1])
        except (IndexError, ValueError, TypeError):
            continue
        if close > 0:
            cap_updates[ticker] = sh * close
        if progress_job and (i == 1 or i % 500 == 0 or i == total):
            log_job_progress(
                progress_job,
                "recompute_market_caps",
                done=i,
                total=total,
                start_time=prog_start,
                caps_computed=len(cap_updates),
            )

    updated = meta_store.update_market_caps(cap_updates) if cap_updates else 0
    if progress_job:
        from tyche.ops.job_progress import log_job_phase

        log_job_phase(
            progress_job,
            "recompute_market_caps",
            status="complete",
            updated=updated,
        )
    logger.info(
        "market_cap_recompute_complete",
        updated=updated,
        tickers_with_shares=len(shares),
    )
    return updated


async def bootstrap_ohlcv(
    polygon: PolygonClient,
    store: OHLCVStore,
    days: int = 120,
    include_today: bool = False,
    # Deprecated — ignored. Ticker metadata is a separate operation
    # (ingest_data.py --meta). Kept for caller compatibility.
    meta_store: TickerMetaStore | None = None,
    backfill_market_caps: bool = True,
    market_cap_concurrency: int = 20,
    market_cap_rpm: int = 500,
    *,
    progress_job: str | None = None,
) -> dict[str, int]:
    """Bootstrap the OHLCV store by fetching grouped daily bars from Polygon.

    This function ONLY fetches price bars. Ticker reference metadata (market
    cap, exchange, type) is managed separately via ``ingest_data.py --meta``
    or the ``refresh_ticker_meta()`` helper. This avoids overwriting stable
    metadata on every incremental OHLCV refresh.

    Args:
        include_today: When True, fetch up to today (use after market close).
            When False (default), stop at yesterday for safety during market
            hours.

    Returns:
        Stats dict with dates_fetched, bars_stored, tickers_found.
    """
    end = date.today() if include_today else date.today() - timedelta(days=1)
    start = end - timedelta(days=int(days * 1.5))

    latest_stored = store.get_latest_date()
    if latest_stored:
        start = latest_stored + timedelta(days=1)
        logger.info(
            "bootstrap_incremental",
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )

    dates_to_fetch: list[date] = []
    current = start
    while current <= end:
        if current.weekday() < 5:
            dates_to_fetch.append(current)
        current += timedelta(days=1)

    if not dates_to_fetch:
        logger.info("bootstrap_up_to_date")
        return {"dates_fetched": 0, "bars_stored": 0, "tickers_found": 0, "tickers_meta": 0}

    total_bars = 0
    dates_fetched = 0
    total_dates = len(dates_to_fetch)
    import time as _time

    prog_start = _time.monotonic()
    if progress_job:
        from tyche.ops.job_progress import log_job_phase, log_job_progress

        log_job_phase(
            progress_job,
            "bootstrap_ohlcv",
            dates_to_fetch=total_dates,
            from_date=dates_to_fetch[0].isoformat() if dates_to_fetch else None,
            to_date=dates_to_fetch[-1].isoformat() if dates_to_fetch else None,
        )

    for i, fetch_date in enumerate(dates_to_fetch, start=1):
        try:
            bars = await polygon.get_grouped_daily(fetch_date)
            if bars:
                stored = store.write_bars(bars)
                total_bars += stored
                dates_fetched += 1
                logger.info(
                    "bootstrap_date_complete",
                    date=fetch_date.isoformat(),
                    bars=len(bars),
                    stored=stored,
                )
        except Exception:
            logger.warning(
                "bootstrap_date_failed",
                date=fetch_date.isoformat(),
                exc_info=True,
            )
        if progress_job and (i == 1 or i == total_dates):
            log_job_progress(
                progress_job,
                "bootstrap_ohlcv",
                done=i,
                total=total_dates,
                start_time=prog_start,
                dates_fetched=dates_fetched,
                bars_stored=total_bars,
                last_date=fetch_date.isoformat(),
            )

    ticker_count = store.get_ticker_count()
    logger.info(
        "bootstrap_complete",
        dates_fetched=dates_fetched,
        total_bars=total_bars,
        tickers=ticker_count,
    )
    return {
        "dates_fetched": dates_fetched,
        "bars_stored": total_bars,
        "tickers_found": ticker_count,
        "tickers_meta": 0,
    }


async def _fetch_equity_type_override_infos(
    polygon: PolygonClient,
) -> list[TickerInfo]:
    """Fetch reference rows for ADR names that should be stored as common stock."""
    from tyche.market_data.polygon import TickerInfo

    infos: list[TickerInfo] = []
    for ticker in EQUITY_TYPE_OVERRIDES:
        try:
            details = await polygon.get_ticker_details(ticker)
        except Exception:
            logger.warning(
                "equity_type_override_fetch_failed",
                ticker=ticker,
                exc_info=True,
            )
            continue
        if not details:
            continue
        infos.append(
            TickerInfo(
                ticker=str(details.get("ticker", ticker)).upper(),
                name=str(details.get("name", ticker)),
                market=str(details.get("market", "stocks")),
                locale=str(details.get("locale", "us")),
                type=str(details.get("type", "ADRC")),
                active=bool(details.get("active", True)),
                primary_exchange=str(details.get("primary_exchange", "")),
                market_cap=float(details.get("market_cap", 0) or 0),
            )
        )
    return infos


async def refresh_ticker_meta(
    polygon: PolygonClient,
    meta_store: TickerMetaStore,
    backfill_market_caps: bool = True,
    market_cap_concurrency: int = 20,
    market_cap_rpm: int = 500,
) -> dict[str, int]:
    """Refresh ticker reference metadata (type, exchange, market cap).

    This is an infrequent operation — market cap and type data change slowly.
    Run explicitly via ``ingest_data.py --meta`` or on a weekly schedule,
    NOT on every OHLCV refresh.
    """
    tickers_meta = 0
    market_caps_updated = 0

    try:
        logger.info("refresh_fetching_ticker_meta")
        ticker_infos = await polygon.get_tickers(
            market="stocks", active=True, ticker_type="CS"
        )
        if ticker_infos:
            tickers_meta = meta_store.write_meta(ticker_infos)
            logger.info("refresh_ticker_meta_complete", tickers=tickers_meta)
        override_infos = await _fetch_equity_type_override_infos(polygon)
        if override_infos:
            tickers_meta = meta_store.write_meta(override_infos)
            logger.info(
                "refresh_ticker_meta_overrides",
                tickers=tickers_meta,
                symbols=sorted(EQUITY_TYPE_OVERRIDES),
            )
    except Exception:
        logger.warning("refresh_ticker_meta_failed", exc_info=True)

    if backfill_market_caps and meta_store.exists:
        try:
            market_caps_updated = await _backfill_market_caps(
                polygon,
                meta_store,
                concurrency=market_cap_concurrency,
                rate_limit_rpm=market_cap_rpm,
            )
        except Exception:
            logger.warning("refresh_market_cap_backfill_failed", exc_info=True)

    logger.info(
        "refresh_ticker_meta_done",
        tickers_meta=tickers_meta,
        market_caps_updated=market_caps_updated,
    )
    return {
        "tickers_meta": tickers_meta,
        "market_caps_updated": market_caps_updated,
    }
