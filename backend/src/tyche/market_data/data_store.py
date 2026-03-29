"""Local Parquet data store for cached OHLCV daily bars, intraday bars, and ticker metadata.

Storage layout:
  data/ohlcv_daily/{TICKER}.parquet   — one file per ticker, daily bars
  data/intraday_5min/{TICKER}.parquet — one file per ticker, 5-min bars
  data/ticker_meta.parquet            — single file, small (~5K rows)

Per-ticker partitioning gives:
  - Zero contention for parallel writes (different tickers = different files)
  - Blast radius limited to a single ticker on corruption
  - O(1) reads for a single ticker (no scanning 5M rows)
  - Safe incremental appends without read-entire-store overhead

Ticker metadata remains a single file because it is small and always read in bulk.
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


def _ticker_path(base_dir: Path, ticker: str) -> Path:
    """Return the per-ticker Parquet file path, sanitizing the symbol."""
    safe = ticker.replace("/", "_").replace("\\", "_")
    return base_dir / f"{safe}.parquet"


class _MetadataCache:
    """Lightweight JSON cache for aggregate store stats.

    Avoids scanning thousands of Parquet files just to answer
    "how many tickers / what's the date range / how many rows?"
    Updated on every write; fast to read for API status endpoints.
    """

    def __init__(self, cache_path: Path) -> None:
        self._path = cache_path

    def read(self) -> dict:
        if not self._path.exists():
            return {}
        try:
            return json.loads(self._path.read_text())
        except Exception:
            return {}

    def write(self, data: dict) -> None:
        try:
            self._path.write_text(json.dumps(data, default=str))
        except Exception as exc:
            logger.warning("metadata_cache_write_error", error=str(exc))

    def rebuild(self, store_dir: Path, dedup_col: str = "date") -> dict:
        """Full scan of all Parquet files to rebuild cache. Slow but accurate."""
        earliest: date | None = None
        latest: date | None = None
        total_rows = 0
        ticker_count = 0

        for path in store_dir.glob("*.parquet"):
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

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._store_dir = self._data_dir / "ohlcv_daily"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_path = self._data_dir / "ohlcv_daily.parquet"
        self._cache = _MetadataCache(self._store_dir / "_meta.json")

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

        logger.info("ohlcv_migrate_start", legacy_path=str(self._legacy_path))
        df = pd.read_parquet(self._legacy_path)
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
            path = _ticker_path(self._store_dir, ticker_str)
            table = pa.Table.from_pandas(ticker_df, schema=OHLCV_SCHEMA)
            pq.write_table(table, path, compression="snappy")
            count += 1

        self._legacy_path.rename(self._legacy_path.with_suffix(".parquet.bak"))
        logger.info("ohlcv_migrate_complete", tickers=count)
        self.rebuild_cache()
        return count

    def _ticker_path(self, ticker: str) -> Path:
        return _ticker_path(self._store_dir, ticker)

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

            path = self._ticker_path(ticker)
            if path.exists():
                existing_df = pd.read_parquet(path)
                existing_df["date"] = pd.to_datetime(existing_df["date"]).dt.date
                combined = pd.concat([existing_df, new_df], ignore_index=True)
                combined = combined.drop_duplicates(subset=["date"], keep="last")
                rows_added = len(combined) - len(existing_df)
            else:
                combined = new_df.drop_duplicates(subset=["date"], keep="last")
                rows_added = len(combined)

            combined = combined.sort_values("date").reset_index(drop=True)
            table = pa.Table.from_pandas(combined, schema=OHLCV_SCHEMA)
            pq.write_table(table, path, compression="snappy")
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
        cached["ticker_count"] = len(list(self._store_dir.glob("*.parquet")))
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
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(
                columns=["date", "open", "high", "low", "close", "volume", "vwap"]
            )

        df = pd.read_parquet(path)
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
        return sorted(
            p.stem for p in self._store_dir.glob("*.parquet")
        )

    def get_row_count(self) -> int:
        """Return total number of rows across all ticker files (cached)."""
        cached = self._ensure_cache()
        return cached.get("total_rows", 0)

    def read_all(self) -> pd.DataFrame:
        """Read all tickers into a single DataFrame with a 'ticker' column.

        Used by backtest and screen_universe which need cross-ticker views.
        """
        frames: list[pd.DataFrame] = []
        for path in self._store_dir.glob("*.parquet"):
            try:
                df = pd.read_parquet(path)
                df["ticker"] = path.stem
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


class TickerMetaStore:
    """Manages persisted ticker metadata (market cap, exchange, type).

    Stored as a separate Parquet file alongside OHLCV data.
    Refreshed on each bootstrap to keep market caps current.
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._parquet_path = self._data_dir / "ticker_meta.parquet"

    @property
    def parquet_path(self) -> Path:
        return self._parquet_path

    @property
    def exists(self) -> bool:
        return self._parquet_path.exists()

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
                }
                for t in tickers
            ]
        )

        if self.exists:
            existing_df = pd.read_parquet(self._parquet_path)
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(subset=["ticker"], keep="last")
        else:
            combined = new_df.drop_duplicates(subset=["ticker"], keep="last")

        combined = combined.sort_values("ticker").reset_index(drop=True)
        combined["last_updated"] = pd.to_datetime(combined["last_updated"]).dt.date

        table = pa.Table.from_pandas(combined, schema=TICKER_META_SCHEMA)
        pq.write_table(table, self._parquet_path, compression="snappy")

        logger.info(
            "ticker_meta_write",
            tickers_stored=len(combined),
            path=str(self._parquet_path),
        )
        return len(combined)

    def read_meta(self) -> pd.DataFrame:
        """Read all ticker metadata."""
        if not self.exists:
            return pd.DataFrame(
                columns=["ticker", "name", "market_cap", "exchange", "type", "last_updated"]
            )
        return pd.read_parquet(self._parquet_path)

    def get_market_caps(self, tickers: list[str] | None = None) -> dict[str, float]:
        """Return a ticker -> market_cap mapping.

        Args:
            tickers: Optional filter. If None, returns all.
        """
        if not self.exists:
            return {}

        df = pd.read_parquet(self._parquet_path, columns=["ticker", "market_cap"])
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        return dict(zip(df["ticker"], df["market_cap"]))

    def get_exchanges(self, tickers: list[str] | None = None) -> dict[str, str]:
        """Return a ticker -> exchange mapping."""
        if not self.exists:
            return {}

        df = pd.read_parquet(self._parquet_path, columns=["ticker", "exchange"])
        if tickers:
            df = df[df["ticker"].isin(tickers)]
        return dict(zip(df["ticker"], df["exchange"]))

    def update_market_caps(self, caps: dict[str, float]) -> int:
        """Bulk-update market caps for existing tickers.

        Returns the number of tickers updated.
        """
        if not self.exists or not caps:
            return 0

        df = pd.read_parquet(self._parquet_path)
        updated = 0
        for ticker, cap in caps.items():
            mask = df["ticker"] == ticker
            if mask.any():
                df.loc[mask, "market_cap"] = cap
                updated += 1

        df["last_updated"] = pd.to_datetime(df["last_updated"]).dt.date
        table = pa.Table.from_pandas(df, schema=TICKER_META_SCHEMA)
        pq.write_table(table, self._parquet_path, compression="snappy")

        logger.info("ticker_meta_caps_updated", updated=updated, total_caps=len(caps))
        return updated

    def get_ticker_count(self) -> int:
        """Return count of tickers in the metadata store."""
        if not self.exists:
            return 0
        try:
            meta = pq.read_metadata(self._parquet_path)
            return meta.num_rows
        except Exception:
            return 0


class IntradayStore:
    """Manages per-ticker Parquet files of 5-minute intraday OHLCV bars.

    Layout: data/intraday_5min/{TICKER}.parquet
    Each file contains only that ticker's intraday bars, deduplicated on timestamp.
    Per-ticker partitioning enables parallel writes and limits blast radius.
    """

    def __init__(self, data_dir: str = "data", multiplier: int = 5) -> None:
        self._data_dir = Path(data_dir)
        self._multiplier = multiplier
        self._store_dir = self._data_dir / f"intraday_{multiplier}min"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._legacy_path = self._data_dir / f"intraday_{multiplier}min.parquet"
        self._cache = _MetadataCache(self._store_dir / "_meta.json")

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


async def bootstrap_ohlcv(
    polygon: PolygonClient,
    store: OHLCVStore,
    days: int = 120,
    meta_store: TickerMetaStore | None = None,
) -> dict[str, int]:
    """Bootstrap the data store by fetching N trading days of grouped daily bars.

    Uses one API call per calendar date (skips weekends).
    Also fetches ticker reference data (market cap, exchange, type) and
    persists it in the TickerMetaStore for backtesting and screening.

    Returns stats dict with dates_fetched, bars_stored, tickers_found, tickers_meta.
    """
    end = date.today() - timedelta(days=1)
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

    for fetch_date in dates_to_fetch:
        try:
            bars = await polygon.get_grouped_daily(fetch_date)
            if bars:
                stored = store.write_bars(bars)
                total_bars += stored
                dates_fetched += 1
                logger.debug(
                    "bootstrap_date_complete",
                    date=fetch_date.isoformat(),
                    bars=len(bars),
                )
        except Exception:
            logger.warning(
                "bootstrap_date_failed",
                date=fetch_date.isoformat(),
                exc_info=True,
            )

    # Fetch and persist ticker reference metadata (market cap, exchange, type)
    tickers_meta = 0
    if meta_store is None:
        meta_store = TickerMetaStore(data_dir=str(store._data_dir))

    try:
        logger.info("bootstrap_fetching_ticker_meta")
        ticker_infos = await polygon.get_tickers(
            market="stocks", active=True, ticker_type="CS"
        )
        if ticker_infos:
            tickers_meta = meta_store.write_meta(ticker_infos)
            logger.info("bootstrap_ticker_meta_complete", tickers=tickers_meta)
    except Exception:
        logger.warning("bootstrap_ticker_meta_failed", exc_info=True)

    ticker_count = store.get_ticker_count()
    logger.info(
        "bootstrap_complete",
        dates_fetched=dates_fetched,
        total_bars=total_bars,
        tickers=ticker_count,
        tickers_meta=tickers_meta,
    )
    return {
        "dates_fetched": dates_fetched,
        "bars_stored": total_bars,
        "tickers_found": ticker_count,
        "tickers_meta": tickers_meta,
    }
