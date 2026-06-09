"""Per-ticker Parquet store for short interest history.

Storage layout:
  data/short_interest/{TICKER}.parquet — one file per ticker

Short interest is reported on a settlement-date cadence (FINRA publishes
bi-monthly). ``settlement_date`` is the point-in-time key. Days-to-cover and
short-interest-ratio are derived from average daily volume when not supplied.

Source: Polygon/Massive ``/stocks/v1/short-interest`` or a FINRA feed.
Source-agnostic — any caller producing matching rows can write.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

SHORT_INTEREST_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("settlement_date", pa.date32()),
        ("short_interest", pa.float64()),  # shares short
        ("avg_daily_volume", pa.float64()),
        ("days_to_cover", pa.float64()),
        ("short_interest_ratio", pa.float64()),  # SI / avg daily volume
        ("short_pct_float", pa.float64()),  # SI / float (may be NaN)
    ]
)

_NUMERIC_COLS = [
    "short_interest",
    "avg_daily_volume",
    "days_to_cover",
    "short_interest_ratio",
    "short_pct_float",
]


class ShortInterestStore:
    """Per-ticker short-interest history.

    Layout: ``data/short_interest/{TICKER}.parquet``. Deduplicated on
    ``settlement_date`` keeping the latest write.
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("short_interest", data_dir, ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    @staticmethod
    def _coerce(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
        for col in _NUMERIC_COLS:
            if col not in df.columns:
                df[col] = pd.NA
            df[col] = pd.to_numeric(df[col], errors="coerce")

        # Derive days-to-cover / ratio when volume is present.
        vol = df["avg_daily_volume"].where(df["avg_daily_volume"].abs() > 0)
        derived_dtc = df["short_interest"] / vol
        df["days_to_cover"] = df["days_to_cover"].fillna(derived_dtc)
        df["short_interest_ratio"] = df["short_interest_ratio"].fillna(derived_dtc)
        return df

    def write_records(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist short-interest rows for a ticker. Dedupes on settlement_date."""
        if df is None or df.empty:
            return 0

        df = self._coerce(df)
        df["ticker"] = ticker.upper()

        rows = self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            SHORT_INTEREST_SCHEMA,
            ["settlement_date"],
            sort_cols=["settlement_date"],
        )
        logger.debug("short_interest_written", ticker=ticker, rows=rows)
        return rows

    def read_ticker(
        self,
        ticker: str,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Read short-interest history, optionally point-in-time filtered."""
        empty = pd.DataFrame(columns=[f.name for f in SHORT_INTEREST_SCHEMA])
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty
        df["settlement_date"] = pd.to_datetime(df["settlement_date"]).dt.date
        if as_of is not None:
            df = df[df["settlement_date"] <= as_of]
        return df.sort_values("settlement_date").reset_index(drop=True)

    def latest(self, ticker: str, as_of: date | None = None) -> dict | None:
        """Return the most recent short-interest row as a dict (or None)."""
        df = self.read_ticker(ticker, as_of=as_of)
        if df.empty:
            return None
        row = df.iloc[-1]
        return {
            "settlement_date": row["settlement_date"],
            "short_interest": None if pd.isna(row["short_interest"]) else float(row["short_interest"]),
            "days_to_cover": None if pd.isna(row["days_to_cover"]) else float(row["days_to_cover"]),
            "short_interest_ratio": (
                None if pd.isna(row["short_interest_ratio"]) else float(row["short_interest_ratio"])
            ),
            "short_pct_float": None if pd.isna(row["short_pct_float"]) else float(row["short_pct_float"]),
        }

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        total_rows = sum(
            self._io.parquet_rows(self._io.ticker_rel(t)) for t in tickers
        )
        return {"ticker_count": len(tickers), "total_rows": total_rows}
