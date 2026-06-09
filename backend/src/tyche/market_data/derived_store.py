"""Per-ticker Parquet store for derived volatility metrics.

Storage layout:
  data/derived/{TICKER}.parquet — one file per ticker

Each file contains daily derived metrics computed from the historical
IV store and OHLCV data: IV Rank, IV Percentile, realised volatility,
and volatility risk premium.
"""

from __future__ import annotations

import math
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

DERIVED_METRICS_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("atm_iv", pa.float64()),
        ("iv_rank", pa.float64()),
        ("iv_percentile", pa.float64()),
        ("rv_20d", pa.float64()),
        ("vrp", pa.float64()),
    ]
)

_ROLLING_WINDOW = 252
_RV_WINDOW = 20


class DerivedMetricsStore:
    """Manages per-ticker Parquet files of derived volatility metrics."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("derived", data_dir, ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    def _ticker_rel(self, ticker: str) -> str:
        return self._io.ticker_rel(ticker)

    def write_metrics(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist derived metrics, merging with existing data on ``date``."""
        if df.empty:
            return 0

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date

        rows = self._io.merge_write(
            self._ticker_rel(ticker),
            df,
            DERIVED_METRICS_SCHEMA,
            ["date"],
            sort_cols=["date"],
        )
        logger.debug("derived_metrics_written", ticker=ticker, rows=rows)
        return rows

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read derived metrics for a single ticker."""
        empty = pd.DataFrame(columns=[f.name for f in DERIVED_METRICS_SCHEMA])
        df = self._io.read_df(self._ticker_rel(ticker))
        if df is None or df.empty:
            return empty

        df["date"] = pd.to_datetime(df["date"]).dt.date
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]
        return df

    def read_latest_batch(
        self,
        tickers: list[str],
        as_of_date: date,
    ) -> dict[str, dict]:
        """Read latest derived metrics on or before *as_of_date* per ticker."""
        result: dict[str, dict] = {}
        for ticker in tickers:
            try:
                df = self.read_ticker(ticker, end_date=as_of_date)
                if df.empty:
                    continue
                row = df.sort_values("date").iloc[-1]
                result[ticker.upper()] = {
                    "iv_rank": None
                    if pd.isna(row.get("iv_rank"))
                    else float(row["iv_rank"]),
                    "iv_percentile": None
                    if pd.isna(row.get("iv_percentile"))
                    else float(row["iv_percentile"]),
                    "atm_iv": None
                    if pd.isna(row.get("atm_iv"))
                    else float(row["atm_iv"]),
                    "vrp": None if pd.isna(row.get("vrp")) else float(row["vrp"]),
                }
            except Exception:
                logger.warning(
                    "derived_metrics_read_failed", ticker=ticker, exc_info=True
                )
        return result

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_ticker_count(self) -> int:
        return len(self.get_all_tickers())

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        if not tickers:
            return {"ticker_count": 0, "total_rows": 0}

        total_rows = sum(
            self._io.parquet_rows(self._ticker_rel(t)) for t in tickers
        )
        return {"ticker_count": len(tickers), "total_rows": total_rows}

    @staticmethod
    def compute_metrics(
        iv_df: pd.DataFrame,
        ohlcv_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Compute IV Rank, IV Percentile, RV(20d), and VRP from raw data."""
        if iv_df.empty:
            return pd.DataFrame(columns=[f.name for f in DERIVED_METRICS_SCHEMA])

        iv = iv_df[["date", "implied_volatility"]].copy()
        iv.columns = ["date", "atm_iv"]
        iv["date"] = pd.to_datetime(iv["date"])
        iv = iv.sort_values("date").drop_duplicates(subset=["date"], keep="last")

        ohlcv = ohlcv_df[["date", "close"]].copy()
        ohlcv["date"] = pd.to_datetime(ohlcv["date"])
        ohlcv = ohlcv.sort_values("date").drop_duplicates(subset=["date"], keep="last")

        log_ret = np.log(ohlcv["close"] / ohlcv["close"].shift(1))
        ohlcv["rv_20d"] = log_ret.rolling(_RV_WINDOW).std() * math.sqrt(252)

        merged = pd.merge(iv, ohlcv[["date", "rv_20d"]], on="date", how="left")

        rolling_high = merged["atm_iv"].rolling(_ROLLING_WINDOW, min_periods=20).max()
        rolling_low = merged["atm_iv"].rolling(_ROLLING_WINDOW, min_periods=20).min()
        iv_range = rolling_high - rolling_low
        merged["iv_rank"] = np.where(
            iv_range > 0,
            (merged["atm_iv"] - rolling_low) / iv_range * 100.0,
            50.0,
        )

        def _percentile_rank(series: pd.Series) -> pd.Series:
            result = pd.Series(np.nan, index=series.index)
            values = series.values
            for i in range(20, len(values)):
                window_start = max(0, i - _ROLLING_WINDOW)
                window = values[window_start:i]
                valid = window[~np.isnan(window)]
                if len(valid) == 0:
                    continue
                result.iloc[i] = (np.sum(valid < values[i]) / len(valid)) * 100.0
            return result

        merged["iv_percentile"] = _percentile_rank(merged["atm_iv"])
        merged["vrp"] = merged["atm_iv"] - merged["rv_20d"]

        result = merged[
            ["date", "atm_iv", "iv_rank", "iv_percentile", "rv_20d", "vrp"]
        ].copy()
        result["date"] = result["date"].dt.date
        result = result.dropna(subset=["atm_iv"]).reset_index(drop=True)
        return result
