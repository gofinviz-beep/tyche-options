"""Per-ticker Parquet store for analyst estimates, revisions, and surprises.

Storage layout:
  data/estimates/{TICKER}.parquet — one file per ticker

Tidy long format — one row per ``(snapshot_date, metric, period)`` — so new
metrics can be added without schema migrations and estimate *revisions* are
computed by diffing the same ``(metric, period)`` across snapshot dates.

``snapshot_date`` is the day the value was observed/persisted and is the
point-in-time key: feature extraction reads the latest snapshot on or before
the as-of date.

Common metrics (not exhaustive):
  - ``rec_strong_buy`` / ``rec_buy`` / ``rec_hold`` / ``rec_sell`` /
    ``rec_strong_sell`` — analyst recommendation counts (period = month).
  - ``eps_surprise_pct`` / ``rev_surprise_pct`` — actual vs. estimate
    (period = the reported fiscal period end).
  - ``eps_est_avg`` / ``rev_est_avg`` / ``*_est_count`` — consensus estimates
    (period = the estimate's target period).
  - ``price_target_mean`` / ``price_target_high`` / ``price_target_low``.

Source: Finnhub (recommendation trends, earnings surprises, estimates,
price targets). Source-agnostic — any caller producing matching rows can write.
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

ESTIMATES_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("snapshot_date", pa.date32()),
        ("metric", pa.string()),
        ("period", pa.string()),  # estimate target / report period (may be "")
        ("value", pa.float64()),
    ]
)


class EstimatesStore:
    """Per-ticker tidy long-format estimate/revision/surprise store.

    Layout: ``data/estimates/{TICKER}.parquet``. Deduplicated on
    ``(snapshot_date, metric, period)`` keeping the latest write.
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("estimates", data_dir, ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    def write_records(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist tidy estimate records for a ticker.

        ``df`` must contain ``snapshot_date``, ``metric``, ``period`` (nullable),
        and ``value``. Merges + dedupes on ``(snapshot_date, metric, period)``.
        """
        if df is None or df.empty:
            return 0

        df = df.copy()
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date
        df["metric"] = df["metric"].astype(str)
        df["period"] = df.get("period", "").fillna("").astype(str)
        df["value"] = pd.to_numeric(df["value"], errors="coerce")
        df = df.dropna(subset=["value"])
        df["ticker"] = ticker.upper()

        if df.empty:
            return 0

        rows = self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            ESTIMATES_SCHEMA,
            ["snapshot_date", "metric", "period"],
            sort_cols=["snapshot_date", "metric", "period"],
        )
        logger.debug("estimates_written", ticker=ticker, rows=rows)
        return rows

    def read_ticker(
        self,
        ticker: str,
        metric: str | None = None,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Read estimate records, optionally filtered by metric / as-of date."""
        empty = pd.DataFrame(columns=[f.name for f in ESTIMATES_SCHEMA])
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty
        df["snapshot_date"] = pd.to_datetime(df["snapshot_date"]).dt.date

        if metric is not None:
            df = df[df["metric"] == metric]
        if as_of is not None:
            df = df[df["snapshot_date"] <= as_of]

        return df.sort_values(["snapshot_date", "metric", "period"]).reset_index(drop=True)

    def latest_values(
        self,
        ticker: str,
        as_of: date | None = None,
    ) -> dict[str, float]:
        """Return ``{metric: value}`` using the latest snapshot per metric.

        Collapses across ``period`` by taking the row with the newest
        ``snapshot_date`` for each metric (ties broken by period). Useful for
        single-snapshot metrics (recommendation counts, price targets,
        most-recent surprise).
        """
        df = self.read_ticker(ticker, as_of=as_of)
        if df.empty:
            return {}
        latest = (
            df.sort_values(["snapshot_date", "period"])
            .groupby("metric", as_index=False)
            .tail(1)
        )
        return dict(zip(latest["metric"], latest["value"]))

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        total_rows = sum(
            self._io.parquet_rows(self._io.ticker_rel(t)) for t in tickers
        )
        return {"ticker_count": len(tickers), "total_rows": total_rows}
