"""Per-ticker Parquet store for full historical options chain data.

Storage layout:
  data/options_history/{TICKER}.parquet — one file per ticker

Each file contains daily OHLCV bars for every options contract that
traded on our filtered universe of underlying tickers.  This broad
dataset supports IV Rank (via ATM put extraction), skew analysis,
term structure, put/call volume ratios, and other future metrics.

Deduplication key: ``(date, option_ticker)`` — at most one bar per
contract per trading day.

A lightweight progress tracker (``_progress.json``) records which
trading dates have been fully ingested so the flat-file script can
resume after interruption.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()

OPTIONS_HISTORY_SCHEMA = pa.schema(
    [
        ("date", pa.date32()),
        ("option_ticker", pa.string()),
        ("underlying", pa.string()),
        ("expiration", pa.date32()),
        ("strike", pa.float64()),
        ("option_type", pa.string()),
        ("open", pa.float64()),
        ("close", pa.float64()),
        ("high", pa.float64()),
        ("low", pa.float64()),
        ("volume", pa.int64()),
        ("transactions", pa.int32()),
        ("dte", pa.int32()),
    ]
)

_DEDUP_COLS = ["date", "option_ticker"]


class OptionsHistoryStore:
    """Manages per-ticker Parquet files of full historical options data.

    Layout: ``data/options_history/{TICKER}.parquet``
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "options_history"
        self._store_dir.mkdir(parents=True, exist_ok=True)
        self._progress_path = self._store_dir / "_progress.json"

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    # ── ticker file helpers ──────────────────────────────────────────

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

    def write_ticker_data(self, ticker: str, df: pd.DataFrame) -> int:
        """Append options data for a ticker, merging with existing file.

        Deduplicates on ``(date, option_ticker)``.
        Returns the total row count after write.
        """
        if df.empty:
            return 0

        new_df = df.copy()
        for col in ("date", "expiration"):
            if col in new_df.columns:
                new_df[col] = pd.to_datetime(new_df[col]).dt.date

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            for col in ("date", "expiration"):
                if col in existing.columns:
                    existing[col] = pd.to_datetime(existing[col]).dt.date
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            combined = new_df

        combined = (
            combined.drop_duplicates(subset=_DEDUP_COLS, keep="last")
            .sort_values(["date", "option_ticker"])
            .reset_index(drop=True)
        )

        table = pa.Table.from_pandas(combined, schema=OPTIONS_HISTORY_SCHEMA)
        pq.write_table(table, path, compression="snappy")

        logger.debug(
            "options_history_written",
            ticker=ticker,
            rows=len(combined),
        )
        return len(combined)

    def write_batch(self, batch: dict[str, pd.DataFrame]) -> dict[str, int]:
        """Write data for multiple tickers at once.

        Args:
            batch: Mapping of underlying ticker → DataFrame of option rows.

        Returns:
            Mapping of ticker → final row count.
        """
        results: dict[str, int] = {}
        for ticker, df in batch.items():
            results[ticker] = self.write_ticker_data(ticker, df)
        return results

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        """Read options history for a single ticker."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in OPTIONS_HISTORY_SCHEMA])

        df = pd.read_parquet(path)
        df["date"] = pd.to_datetime(df["date"]).dt.date

        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]

        return df

    def get_all_tickers(self) -> list[str]:
        """Return all tickers with options history on disk."""
        return sorted(
            p.stem.upper()
            for p in self._store_dir.glob("*.parquet")
            if not p.name.startswith("_")
        )

    def get_ticker_count(self) -> int:
        return len(self.get_all_tickers())

    def get_stats(self) -> dict:
        """Summary statistics for the store."""
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

        return {
            "ticker_count": len(tickers),
            "total_rows": total_rows,
            "completed_dates": len(self.get_completed_dates()),
        }

    # ── progress tracking ────────────────────────────────────────────

    def get_completed_dates(self) -> set[str]:
        """Return set of ISO date strings that have been fully ingested."""
        if not self._progress_path.exists():
            return set()
        try:
            data = json.loads(self._progress_path.read_text())
            return set(data.get("completed_dates", []))
        except (json.JSONDecodeError, OSError):
            return set()

    def mark_dates_completed(self, dates: list[str]) -> None:
        """Atomically add dates to the completed set."""
        completed = self.get_completed_dates()
        completed.update(dates)

        tmp_path = self._progress_path.with_suffix(".tmp")
        payload = json.dumps(
            {"completed_dates": sorted(completed)},
            indent=2,
        )
        tmp_path.write_text(payload)
        tmp_path.replace(self._progress_path)

        logger.debug("progress_updated", new_dates=len(dates), total=len(completed))
