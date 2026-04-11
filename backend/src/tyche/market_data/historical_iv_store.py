"""Per-ticker Parquet store for historical implied volatility data.

Storage layout:
  data/options_iv/{TICKER}.parquet — one file per ticker

Each file contains daily ATM put IV observations computed from historical
option contract bars via Black-Scholes inverse.  Deduplicated on ``date``.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()

HISTORICAL_IV_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("strike", pa.float64()),
        ("expiration", pa.date32()),
        ("contract_ticker", pa.string()),
        ("option_close", pa.float64()),
        ("underlying_close", pa.float64()),
        ("dte", pa.int32()),
        ("implied_volatility", pa.float64()),
    ]
)


class HistoricalIVStore:
    """Manages per-ticker Parquet files of historical ATM IV data.

    Layout: ``data/options_iv/{TICKER}.parquet``
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "options_iv"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

    def write_iv_data(self, ticker: str, records: list[dict]) -> int:
        """Write IV records for a ticker, merging with existing data.

        Records must contain all fields from ``HISTORICAL_IV_SCHEMA``.
        Returns the number of rows in the final file.
        """
        if not records:
            return 0

        new_df = pd.DataFrame(records)
        for col in ("date", "expiration"):
            new_df[col] = pd.to_datetime(new_df[col]).dt.date

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["date"] = pd.to_datetime(existing["date"]).dt.date
            existing["expiration"] = pd.to_datetime(existing["expiration"]).dt.date
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined = (
            combined.drop_duplicates(subset=["date"], keep="last")
            .sort_values("date")
            .reset_index(drop=True)
        )

        table = pa.Table.from_pandas(combined, schema=HISTORICAL_IV_SCHEMA)
        pq.write_table(table, path, compression="snappy")

        logger.debug(
            "historical_iv_written",
            ticker=ticker,
            rows=len(combined),
        )
        return len(combined)

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read IV data for a single ticker, optionally filtered by date range."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in HISTORICAL_IV_SCHEMA])

        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date

        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]

        return df

    def get_latest_date(self, ticker: str) -> date | None:
        """Return the most recent IV date for a ticker, or None if no data."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return None
        try:
            df = pd.read_parquet(path, columns=["date"])
            if df.empty:
                return None
            max_val = pd.to_datetime(df["date"]).max()
            return max_val.date() if pd.notna(max_val) else None
        except Exception:
            return None

    def get_all_tickers(self) -> list[str]:
        """Return all tickers with IV data on disk."""
        return sorted(
            p.stem.upper()
            for p in self._store_dir.glob("*.parquet")
            if not p.name.startswith("_")
        )

    def get_ticker_count(self) -> int:
        return len(self.get_all_tickers())

    def get_row_count(self) -> int:
        """Total rows across all ticker files."""
        total = 0
        for p in self._store_dir.glob("*.parquet"):
            if p.name.startswith("_"):
                continue
            try:
                total += pq.read_metadata(p).num_rows
            except Exception:
                continue
        return total

    def get_stats(self) -> dict:
        """Summary statistics for the store."""
        tickers = self.get_all_tickers()
        if not tickers:
            return {"ticker_count": 0, "total_rows": 0}

        total_rows = 0
        earliest: date | None = None
        latest: date | None = None

        for t in tickers:
            path = self._ticker_path(t)
            try:
                df = pd.read_parquet(path, columns=["date"])
                if df.empty:
                    continue
                dates = pd.to_datetime(df["date"])
                total_rows += len(df)
                t_min, t_max = dates.min().date(), dates.max().date()
                if earliest is None or t_min < earliest:
                    earliest = t_min
                if latest is None or t_max > latest:
                    latest = t_max
            except Exception:
                continue

        return {
            "ticker_count": len(tickers),
            "total_rows": total_rows,
            "earliest_date": str(earliest) if earliest else None,
            "latest_date": str(latest) if latest else None,
        }

    # ── IV extraction checkpoint ─────────────────────────────────────

    @property
    def _checkpoint_path(self) -> Path:
        return self._store_dir / "_iv_checkpoint.json"

    def get_checkpoint(self) -> dict | None:
        """Return the last IV extraction checkpoint, or None if never run."""
        if not self._checkpoint_path.exists():
            return None
        try:
            return json.loads(self._checkpoint_path.read_text())
        except (json.JSONDecodeError, OSError):
            return None

    def write_checkpoint(
        self,
        *,
        last_options_date: str,
        tickers_processed: int,
        iv_points: int,
    ) -> None:
        """Atomically persist an IV extraction checkpoint."""
        payload = {
            "last_run_iso": datetime.now(timezone.utc).isoformat(),
            "last_options_date": last_options_date,
            "tickers_processed": tickers_processed,
            "iv_points": iv_points,
        }
        tmp = self._checkpoint_path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2))
        tmp.replace(self._checkpoint_path)
