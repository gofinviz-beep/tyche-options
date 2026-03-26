"""Local Parquet data store for cached OHLCV daily bars and ticker metadata.

Bootstrap: Fetches N days of grouped daily bars from Polygon, stores as Parquet.
Daily: Appends a single day's grouped bars, deduplicates on (ticker, date).
Read: Returns a DataFrame for a single ticker or filtered set.

Ticker metadata (market cap, exchange, type) is stored separately and refreshed
on each bootstrap to enable realistic backtesting with production-identical filters.
"""

from __future__ import annotations

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
    from tyche.market_data.polygon import DailyBar, PolygonClient, TickerInfo

logger = structlog.get_logger()

OHLCV_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
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


class OHLCVStore:
    """Manages a local Parquet file of daily OHLCV data for all tickers.

    Single-file design: one Parquet file holds all tickers' daily bars.
    Partitioned reads use predicate pushdown on (ticker, date).
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._data_dir = Path(data_dir)
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._parquet_path = self._data_dir / "ohlcv_daily.parquet"

    @property
    def parquet_path(self) -> Path:
        return self._parquet_path

    @property
    def exists(self) -> bool:
        return self._parquet_path.exists()

    def get_latest_date(self) -> date | None:
        """Return the most recent date in the store, or None if empty."""
        if not self.exists:
            return None
        try:
            table = pq.read_table(
                self._parquet_path, columns=["date"]
            )
            if table.num_rows == 0:
                return None
            dates = table.column("date").to_pylist()
            return max(dates)
        except Exception as exc:
            logger.warning("data_store_read_error", error=str(exc))
            return None

    def get_date_range(self) -> tuple[date | None, date | None]:
        """Return (earliest, latest) dates in the store."""
        if not self.exists:
            return None, None
        try:
            table = pq.read_table(self._parquet_path, columns=["date"])
            if table.num_rows == 0:
                return None, None
            dates = table.column("date").to_pylist()
            return min(dates), max(dates)
        except Exception as exc:
            logger.warning("data_store_range_error", error=str(exc))
            return None, None

    def get_ticker_count(self) -> int:
        """Return count of unique tickers in the store."""
        if not self.exists:
            return 0
        try:
            table = pq.read_table(self._parquet_path, columns=["ticker"])
            return len(table.column("ticker").unique())
        except Exception:
            return 0

    def write_bars(self, bars: list[DailyBar]) -> int:
        """Append daily bars to the store, deduplicating on (ticker, date).

        Returns the number of new rows added.
        """
        if not bars:
            return 0

        new_df = pd.DataFrame(
            [
                {
                    "ticker": b.ticker,
                    "date": b.date,
                    "open": b.open,
                    "high": b.high,
                    "low": b.low,
                    "close": b.close,
                    "volume": b.volume,
                    "vwap": b.vwap,
                }
                for b in bars
            ]
        )
        new_df["date"] = pd.to_datetime(new_df["date"]).dt.date

        if self.exists:
            existing_df = pd.read_parquet(self._parquet_path)
            existing_df["date"] = pd.to_datetime(existing_df["date"]).dt.date
            combined = pd.concat([existing_df, new_df], ignore_index=True)
            combined = combined.drop_duplicates(
                subset=["ticker", "date"], keep="last"
            )
            rows_added = len(combined) - len(existing_df)
        else:
            combined = new_df.drop_duplicates(
                subset=["ticker", "date"], keep="last"
            )
            rows_added = len(combined)

        combined = combined.sort_values(["ticker", "date"]).reset_index(drop=True)

        table = pa.Table.from_pandas(combined, schema=OHLCV_SCHEMA)
        pq.write_table(table, self._parquet_path, compression="snappy")

        logger.info(
            "data_store_write",
            rows_added=rows_added,
            total_rows=len(combined),
            path=str(self._parquet_path),
        )
        return rows_added

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read OHLCV data for a single ticker, sorted by date ascending.

        Returns DataFrame with columns: date, open, high, low, close, volume, vwap
        """
        if not self.exists:
            return pd.DataFrame(
                columns=["date", "open", "high", "low", "close", "volume", "vwap"]
            )

        df = pd.read_parquet(self._parquet_path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        mask = df["ticker"] == ticker
        if start_date:
            mask &= df["date"] >= start_date
        if end_date:
            mask &= df["date"] <= end_date

        result = df[mask].drop(columns=["ticker"]).sort_values("date").reset_index(drop=True)
        return result

    def read_tickers(
        self,
        tickers: list[str],
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> dict[str, pd.DataFrame]:
        """Read OHLCV data for multiple tickers at once."""
        if not self.exists:
            return {}

        df = pd.read_parquet(self._parquet_path)
        df["date"] = pd.to_datetime(df["date"]).dt.date
        mask = df["ticker"].isin(tickers)
        if start_date:
            mask &= df["date"] >= start_date
        if end_date:
            mask &= df["date"] <= end_date

        filtered = df[mask]
        result: dict[str, pd.DataFrame] = {}
        for ticker, group in filtered.groupby("ticker"):
            result[str(ticker)] = (
                group.drop(columns=["ticker"])
                .sort_values("date")
                .reset_index(drop=True)
            )
        return result

    def get_all_tickers(self) -> list[str]:
        """Return list of all tickers in the store."""
        if not self.exists:
            return []
        try:
            table = pq.read_table(self._parquet_path, columns=["ticker"])
            return sorted(table.column("ticker").unique().to_pylist())
        except Exception:
            return []

    def get_row_count(self) -> int:
        """Return total number of rows in the store."""
        if not self.exists:
            return 0
        try:
            meta = pq.read_metadata(self._parquet_path)
            return meta.num_rows
        except Exception:
            return 0

    def screen_universe(
        self,
        min_avg_volume: int = 500_000,
        min_price: float = 5.0,
        min_dollar_volume: float = 5_000_000.0,
        lookback_days: int = 20,
    ) -> list[str]:
        """Screen the stored universe locally using price and volume filters.

        Uses average daily dollar volume (close * volume) as a proxy
        for market cap — no extra API calls needed.

        Args:
            min_avg_volume: Minimum 20-day average daily share volume
            min_price: Minimum last closing price
            min_dollar_volume: Minimum avg daily dollar volume (proxy for market cap)
            lookback_days: Number of trading days for averaging

        Returns:
            Sorted list of ticker symbols passing all screens
        """
        if not self.exists:
            return []

        df = pd.read_parquet(self._parquet_path)
        df["date"] = pd.to_datetime(df["date"]).dt.date

        latest_date = df["date"].max()
        cutoff = latest_date - timedelta(days=int(lookback_days * 1.5))
        recent = df[df["date"] >= cutoff]

        stats = recent.groupby("ticker").agg(
            avg_volume=("volume", "mean"),
            last_close=("close", "last"),
            avg_dollar_vol=pd.NamedAgg(
                column="close",
                aggfunc=lambda x: (x * recent.loc[x.index, "volume"]).mean(),
            ),
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
