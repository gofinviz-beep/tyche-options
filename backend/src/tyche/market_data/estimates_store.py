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
import pyarrow.parquet as pq
import structlog

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

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "estimates"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

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

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["snapshot_date"] = pd.to_datetime(existing["snapshot_date"]).dt.date
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        combined = (
            combined.drop_duplicates(
                subset=["snapshot_date", "metric", "period"], keep="last"
            )
            .sort_values(["snapshot_date", "metric", "period"])
            .reset_index(drop=True)
        )

        ordered = combined[[f.name for f in ESTIMATES_SCHEMA]]
        table = pa.Table.from_pandas(ordered, schema=ESTIMATES_SCHEMA, preserve_index=False)
        pq.write_table(table, path, compression="snappy")

        logger.debug("estimates_written", ticker=ticker, rows=len(combined))
        return len(combined)

    def read_ticker(
        self,
        ticker: str,
        metric: str | None = None,
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Read estimate records, optionally filtered by metric / as-of date."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in ESTIMATES_SCHEMA])

        df = pd.read_parquet(path)
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
