"""Per-ticker Parquet store for derived volatility metrics.

Storage layout:
  data/derived/{TICKER}.parquet — one file per ticker

Each file contains daily derived metrics computed from the historical
IV store and OHLCV data: IV Rank, IV Percentile, realised volatility,
and volatility risk premium.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

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

_ROLLING_WINDOW = 252  # ~1 year of trading days for IV Rank/Percentile
_RV_WINDOW = 20  # 20-day realised volatility


class DerivedMetricsStore:
    """Manages per-ticker Parquet files of derived volatility metrics.

    Layout: ``data/derived/{TICKER}.parquet``
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "derived"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

    def write_metrics(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist a DataFrame of derived metrics for a ticker.

        Merges with existing data and deduplicates on ``date``.
        Returns the total row count after write.
        """
        if df.empty:
            return 0

        df = df.copy()
        df["date"] = pd.to_datetime(df["date"]).dt.date

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["date"] = pd.to_datetime(existing["date"]).dt.date
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        combined = (
            combined.drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )

        table = pa.Table.from_pandas(combined, schema=DERIVED_METRICS_SCHEMA)
        pq.write_table(table, path, compression="snappy")

        logger.debug("derived_metrics_written", ticker=ticker, rows=len(combined))
        return len(combined)

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read derived metrics for a single ticker."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in DERIVED_METRICS_SCHEMA])

        df = pd.read_parquet(path)
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
        """Read the latest derived metrics row for each ticker on or before as_of_date.

        Returns:
            ``{ticker: {iv_rank, iv_percentile, atm_iv, vrp}}`` for tickers
            that have data.  Tickers without a Parquet file or without any
            row on/before ``as_of_date`` are silently omitted.
        """
        result: dict[str, dict] = {}
        for ticker in tickers:
            path = self._ticker_path(ticker)
            if not path.exists():
                continue
            try:
                df = pd.read_parquet(path)
                df["date"] = pd.to_datetime(df["date"]).dt.date
                df = df[df["date"] <= as_of_date]
                if df.empty:
                    continue
                row = df.sort_values("date").iloc[-1]
                result[ticker.upper()] = {
                    "iv_rank": None if pd.isna(row.get("iv_rank")) else float(row["iv_rank"]),
                    "iv_percentile": None if pd.isna(row.get("iv_percentile")) else float(row["iv_percentile"]),
                    "atm_iv": None if pd.isna(row.get("atm_iv")) else float(row["atm_iv"]),
                    "vrp": None if pd.isna(row.get("vrp")) else float(row["vrp"]),
                }
            except Exception:
                logger.warning("derived_metrics_read_failed", ticker=ticker, exc_info=True)
                continue
        return result

    def get_all_tickers(self) -> list[str]:
        return sorted(
            p.stem.upper()
            for p in self._store_dir.glob("*.parquet")
            if not p.name.startswith("_")
        )

    def get_ticker_count(self) -> int:
        return len(self.get_all_tickers())

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        if not tickers:
            return {"ticker_count": 0, "total_rows": 0}

        total_rows = 0
        for t in tickers:
            path = self._ticker_path(t)
            try:
                total_rows += pq.read_metadata(path).num_rows
            except Exception:
                continue

        return {"ticker_count": len(tickers), "total_rows": total_rows}

    @staticmethod
    def compute_metrics(
        iv_df: pd.DataFrame,
        ohlcv_df: pd.DataFrame,
    ) -> pd.DataFrame:
        """Compute IV Rank, IV Percentile, RV(20d), and VRP from raw data.

        Args:
            iv_df: Historical IV data for one ticker (from HistoricalIVStore).
                   Must contain columns ``date`` and ``implied_volatility``.
            ohlcv_df: OHLCV data for the same ticker (from OHLCVStore).
                      Must contain columns ``date`` and ``close``.

        Returns:
            DataFrame with columns matching ``DERIVED_METRICS_SCHEMA``.
        """
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

        result = merged[["date", "atm_iv", "iv_rank", "iv_percentile", "rv_20d", "vrp"]].copy()
        result["date"] = result["date"].dt.date
        result = result.dropna(subset=["atm_iv"]).reset_index(drop=True)

        return result
