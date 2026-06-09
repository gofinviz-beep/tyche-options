"""Per-ticker Parquet store for historical implied volatility data."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage import read_json, write_json
from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

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

_CHECKPOINT_REL = "options_iv/_iv_checkpoint.json"


class HistoricalIVStore:
    """Manages per-ticker Parquet files of historical ATM IV data."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("options_iv", data_dir, ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    @property
    def _checkpoint_path(self) -> Path:
        """Local path for tests/tools that write checkpoint JSON directly."""
        return self._io.store_dir / "_iv_checkpoint.json"

    def write_iv_data(self, ticker: str, records: list[dict]) -> int:
        if not records:
            return 0

        new_df = pd.DataFrame(records)
        for col in ("date", "expiration"):
            new_df[col] = pd.to_datetime(new_df[col]).dt.date

        rows = self._io.merge_write(
            self._io.ticker_rel(ticker),
            new_df,
            HISTORICAL_IV_SCHEMA,
            ["date"],
            sort_cols=["date"],
        )
        logger.debug("historical_iv_written", ticker=ticker, rows=rows)
        return rows

    def read_ticker(
        self,
        ticker: str,
        start_date: date | None = None,
        end_date: date | None = None,
    ) -> pd.DataFrame:
        empty = pd.DataFrame(columns=[f.name for f in HISTORICAL_IV_SCHEMA])
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty

        df["date"] = pd.to_datetime(df["date"]).dt.date
        if start_date:
            df = df[df["date"] >= start_date]
        if end_date:
            df = df[df["date"] <= end_date]
        return df

    def get_latest_date(self, ticker: str) -> date | None:
        try:
            df = self._io.read_df(
                self._io.ticker_rel(ticker), columns=["date"]
            )
            if df is None or df.empty:
                return None
            max_val = pd.to_datetime(df["date"]).max()
            return max_val.date() if pd.notna(max_val) else None
        except Exception:
            return None

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_ticker_count(self) -> int:
        return len(self.get_all_tickers())

    def get_row_count(self) -> int:
        return sum(
            self._io.parquet_rows(self._io.ticker_rel(t))
            for t in self.get_all_tickers()
        )

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        if not tickers:
            return {"ticker_count": 0, "total_rows": 0}

        total_rows = 0
        earliest: date | None = None
        latest: date | None = None

        for t in tickers:
            try:
                df = self._io.read_df(
                    self._io.ticker_rel(t), columns=["date"]
                )
                if df is None or df.empty:
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

    def get_checkpoint(self) -> dict | None:
        if not self._io.exists(_CHECKPOINT_REL):
            return None
        try:
            data = read_json(_CHECKPOINT_REL, ctx=self._io.ctx)
            return data if isinstance(data, dict) else None
        except Exception:
            return None

    def write_checkpoint(
        self,
        *,
        last_options_date: str,
        tickers_processed: int,
        iv_points: int,
    ) -> None:
        payload = {
            "last_run_iso": datetime.now(timezone.utc).isoformat(),
            "last_options_date": last_options_date,
            "tickers_processed": tickers_processed,
            "iv_points": iv_points,
        }
        write_json(payload, _CHECKPOINT_REL, atomic=True, ctx=self._io.ctx)
