"""Point-in-time Finnhub EPS/revenue consensus snapshots (wide format).

Layout: ``data/estimate_snapshots/{TICKER}.parquet``

Each ingest appends rows keyed by
``(ticker, vendor, metric, freq, period, snapshot_date)`` so same-period
revision velocity can be computed from local history.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

ESTIMATE_SNAPSHOT_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("vendor_symbol", pa.string()),
        ("vendor", pa.string()),
        ("metric", pa.string()),
        ("snapshot_date", pa.date32()),
        ("ingested_at", pa.timestamp("us", tz="UTC")),
        ("freq", pa.string()),
        ("period", pa.string()),
        ("fiscal_year", pa.int32()),
        ("fiscal_quarter", pa.int32()),
        ("estimate_avg", pa.float64()),
        ("estimate_high", pa.float64()),
        ("estimate_low", pa.float64()),
        ("number_analysts", pa.int32()),
        ("raw_payload_hash", pa.string()),
        ("source_endpoint", pa.string()),
    ]
)

_DEDUP_COLS = [
    "ticker",
    "vendor",
    "metric",
    "freq",
    "period",
    "snapshot_date",
]


class EstimateSnapshotStore:
    """Wide-format consensus snapshot store (does not replace tidy EstimatesStore)."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("estimate_snapshots", data_dir, ctx)

    def write_snapshots(self, ticker: str, df: pd.DataFrame) -> int:
        """Upsert snapshot rows; never drops prior ``snapshot_date`` values."""
        if df is None or df.empty:
            return 0

        df = df.copy()
        df["ticker"] = ticker.upper()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
        if "ingested_at" not in df.columns:
            df["ingested_at"] = datetime.now(timezone.utc)
        else:
            df["ingested_at"] = pd.to_datetime(df["ingested_at"], utc=True)

        rows = self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            ESTIMATE_SNAPSHOT_SCHEMA,
            _DEDUP_COLS,
            sort_cols=["snapshot_date", "metric", "period"],
        )
        logger.debug("estimate_snapshots_written", ticker=ticker, rows=rows)
        return rows

    def read_ticker(
        self,
        ticker: str,
        metric: str | None = None,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        empty = pd.DataFrame(columns=[f.name for f in ESTIMATE_SNAPSHOT_SCHEMA])
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
        if metric is not None:
            df = df[df["metric"] == metric]
        if as_of is not None:
            df = df[df["snapshot_date"] <= as_of]
        return df.sort_values(["snapshot_date", "metric", "period"]).reset_index(drop=True)

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()


def payload_hash(payload: object) -> str:
    """Stable hash for debugging raw Finnhub consensus payloads."""
    blob = json.dumps(payload, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]
