"""Per-ticker Parquet store for full historical options chain data."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage import read_json, write_json
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

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
_PROGRESS_REL = "options_history/_progress.json"


class OptionsHistoryStore:
    """Manages per-ticker Parquet files of full historical options data."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("options_history", data_dir, ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    @property
    def _progress_path(self) -> Path:
        """Local path for tests/tools that write progress JSON directly."""
        return self._io.store_dir / "_progress.json"

    def write_ticker_data(self, ticker: str, df: pd.DataFrame) -> int:
        """Append options data for a ticker, merging with existing file."""
        if df.empty:
            return 0

        new_df = df.copy()
        for col in ("date", "expiration"):
            if col in new_df.columns:
                new_df[col] = pd.to_datetime(new_df[col]).dt.date

        rows = self._io.merge_write(
            self._io.ticker_rel(ticker),
            new_df,
            OPTIONS_HISTORY_SCHEMA,
            _DEDUP_COLS,
            sort_cols=["date", "option_ticker"],
        )
        logger.debug("options_history_written", ticker=ticker, rows=rows)
        return rows

    def write_batch(self, batch: dict[str, pd.DataFrame]) -> dict[str, int]:
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
        empty = pd.DataFrame(columns=[f.name for f in OPTIONS_HISTORY_SCHEMA])
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty

        df["date"] = pd.to_datetime(df["date"]).dt.date
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]
        return df

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_ticker_count(self) -> int:
        return len(self.get_all_tickers())

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        if not tickers:
            return {"ticker_count": 0, "total_rows": 0}

        total_rows = sum(
            self._io.parquet_rows(self._io.ticker_rel(t)) for t in tickers
        )
        return {
            "ticker_count": len(tickers),
            "total_rows": total_rows,
            "completed_dates": len(self.get_completed_dates()),
        }

    def get_completed_dates(self) -> set[str]:
        if not self._io.exists(_PROGRESS_REL):
            return set()
        try:
            data = read_json(_PROGRESS_REL, ctx=self._io.ctx)
            if isinstance(data, dict):
                return set(data.get("completed_dates", []))
        except Exception:
            return set()
        return set()

    def mark_dates_completed(self, dates: list[str]) -> None:
        completed = self.get_completed_dates()
        completed.update(dates)
        write_json(
            {"completed_dates": sorted(completed)},
            _PROGRESS_REL,
            atomic=True,
            ctx=self._io.ctx,
        )
        logger.debug("progress_updated", new_dates=len(dates), total=len(completed))
