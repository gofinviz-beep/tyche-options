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
import pyarrow.parquet as pq
import structlog

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

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "short_interest"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

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

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["settlement_date"] = pd.to_datetime(existing["settlement_date"]).dt.date
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        combined = (
            combined.drop_duplicates(subset=["settlement_date"], keep="last")
            .sort_values("settlement_date")
            .reset_index(drop=True)
        )

        ordered = combined[[f.name for f in SHORT_INTEREST_SCHEMA]]
        table = pa.Table.from_pandas(ordered, schema=SHORT_INTEREST_SCHEMA, preserve_index=False)
        pq.write_table(table, path, compression="snappy")

        logger.debug("short_interest_written", ticker=ticker, rows=len(combined))
        return len(combined)

    def read_ticker(
        self,
        ticker: str,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Read short-interest history, optionally point-in-time filtered."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in SHORT_INTEREST_SCHEMA])

        df = pd.read_parquet(path)
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
        return sorted(
            p.stem.upper()
            for p in self._store_dir.glob("*.parquet")
            if not p.name.startswith("_")
        )

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        total_rows = 0
        for t in tickers:
            try:
                total_rows += pq.read_metadata(self._ticker_path(t)).num_rows
            except Exception:
                continue
        return {"ticker_count": len(tickers), "total_rows": total_rows}
