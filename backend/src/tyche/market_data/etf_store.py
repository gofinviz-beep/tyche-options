"""Parquet-backed ETF constituent store.

Persists ETF membership and weights at ``data/etf_constituents.parquet``.
Populated from static curated lists (``etf_constituents.py``) and
optionally enriched with yfinance ``funds_data.top_holdings`` for weights.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()

ETF_SCHEMA = pa.schema([
    ("etf_ticker", pa.string()),
    ("constituent_ticker", pa.string()),
    ("weight", pa.float64()),
    ("as_of_date", pa.date32()),
])


class ETFConstituentStore:
    """Read/write ETF constituent data backed by a single Parquet file."""

    def __init__(self, data_dir: str = "data") -> None:
        self._path = Path(data_dir) / "etf_constituents.parquet"

    @property
    def exists(self) -> bool:
        return self._path.exists()

    def write_constituents(
        self,
        etf_ticker: str,
        constituents: list[dict],
        as_of: date | None = None,
    ) -> int:
        """Upsert constituents for one ETF.

        Args:
            etf_ticker: The ETF symbol (e.g. "SPY").
            constituents: List of dicts with keys ``ticker`` and
                optional ``weight``.
            as_of: Date stamp for this snapshot (defaults to today).

        Returns:
            Number of constituents written.
        """
        as_of = as_of or date.today()
        rows = [
            {
                "etf_ticker": etf_ticker,
                "constituent_ticker": c["ticker"],
                "weight": c.get("weight"),
                "as_of_date": as_of,
            }
            for c in constituents
        ]
        new_df = pd.DataFrame(rows)

        if self.exists:
            existing = pd.read_parquet(self._path)
            existing = existing[existing["etf_ticker"] != etf_ticker]
            combined = pd.concat([existing, new_df], ignore_index=True)
        else:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            combined = new_df

        combined = combined.sort_values(
            ["etf_ticker", "constituent_ticker"]
        ).reset_index(drop=True)
        combined["as_of_date"] = pd.to_datetime(combined["as_of_date"]).dt.date

        table = pa.Table.from_pandas(combined, schema=ETF_SCHEMA)
        pq.write_table(table, self._path)

        logger.info(
            "etf_constituents_written",
            etf=etf_ticker,
            count=len(rows),
            as_of=str(as_of),
        )
        return len(rows)

    def write_all(
        self,
        all_constituents: dict[str, list[dict]],
        as_of: date | None = None,
    ) -> int:
        """Bulk write constituents for multiple ETFs (replaces entire file)."""
        as_of = as_of or date.today()
        rows: list[dict] = []
        for etf, members in all_constituents.items():
            for c in members:
                rows.append({
                    "etf_ticker": etf,
                    "constituent_ticker": c["ticker"],
                    "weight": c.get("weight"),
                    "as_of_date": as_of,
                })

        if not rows:
            return 0

        self._path.parent.mkdir(parents=True, exist_ok=True)
        df = pd.DataFrame(rows)
        df = df.sort_values(["etf_ticker", "constituent_ticker"]).reset_index(drop=True)
        df["as_of_date"] = pd.to_datetime(df["as_of_date"]).dt.date

        table = pa.Table.from_pandas(df, schema=ETF_SCHEMA)
        pq.write_table(table, self._path)

        logger.info(
            "etf_constituents_bulk_written",
            etfs=len(all_constituents),
            total_rows=len(rows),
        )
        return len(rows)

    def read_all(self) -> pd.DataFrame:
        """Read the full constituents table."""
        if not self.exists:
            return pd.DataFrame(columns=["etf_ticker", "constituent_ticker", "weight", "as_of_date"])
        return pd.read_parquet(self._path)

    def get_etf_memberships(self, ticker: str) -> list[str]:
        """Return ETF symbols that contain *ticker*."""
        if not self.exists:
            return []
        df = pd.read_parquet(self._path, columns=["etf_ticker", "constituent_ticker"])
        matches = df[df["constituent_ticker"] == ticker]["etf_ticker"].unique().tolist()
        return sorted(matches)

    def get_constituents(self, etf_ticker: str) -> list[str]:
        """Return all constituent tickers for an ETF."""
        if not self.exists:
            return []
        df = pd.read_parquet(self._path, columns=["etf_ticker", "constituent_ticker"])
        return sorted(df[df["etf_ticker"] == etf_ticker]["constituent_ticker"].unique().tolist())

    def get_etf_weights(self, etf_ticker: str) -> dict[str, float]:
        """Return {ticker: weight} for an ETF.  Weight may be NaN."""
        if not self.exists:
            return {}
        df = pd.read_parquet(self._path, columns=["etf_ticker", "constituent_ticker", "weight"])
        sub = df[df["etf_ticker"] == etf_ticker]
        return dict(zip(sub["constituent_ticker"], sub["weight"]))

    def get_membership_counts(self) -> dict[str, int]:
        """Return {stock_ticker: count_of_etfs} for all stocks."""
        if not self.exists:
            return {}
        df = pd.read_parquet(self._path, columns=["etf_ticker", "constituent_ticker"])
        return df.groupby("constituent_ticker")["etf_ticker"].nunique().to_dict()

    def get_membership_matrix(self) -> dict[str, list[str]]:
        """Return {stock_ticker: [etf1, etf2, ...]} for all stocks."""
        if not self.exists:
            return {}
        df = pd.read_parquet(self._path, columns=["etf_ticker", "constituent_ticker"])
        result: dict[str, list[str]] = {}
        for _, row in df.iterrows():
            result.setdefault(row["constituent_ticker"], []).append(row["etf_ticker"])
        for v in result.values():
            v.sort()
        return result


def fetch_etf_holdings_yfinance(etf_ticker: str) -> list[dict]:
    """Fetch top holdings + weights from yfinance.

    Returns list of ``{"ticker": str, "weight": float}``.
    Falls back to empty list on error (yfinance is best-effort).
    """
    try:
        import yfinance as yf

        etf = yf.Ticker(etf_ticker)
        data = etf.funds_data
        holdings = data.top_holdings

        if holdings is None or holdings.empty:
            logger.debug("yfinance_no_holdings", etf=etf_ticker)
            return []

        results: list[dict] = []
        for symbol, row in holdings.iterrows():
            weight = row.get("Holding Percent", row.iloc[0]) if len(row) > 0 else None
            results.append({
                "ticker": str(symbol),
                "weight": float(weight) if weight is not None else None,
            })

        logger.info(
            "yfinance_holdings_fetched",
            etf=etf_ticker,
            count=len(results),
        )
        return results

    except Exception as exc:
        logger.warning("yfinance_holdings_error", etf=etf_ticker, error=str(exc))
        return []


def build_etf_data(
    etf_tickers: list[str] | None = None,
    use_yfinance: bool = True,
) -> dict[str, list[dict]]:
    """Build constituent data by merging static lists with yfinance weights.

    Static lists provide comprehensive membership; yfinance provides
    weights for the top holdings.  The result uses yfinance weights where
    available and None for tickers only in the static list.
    """
    from tyche.market_data.etf_constituents import (
        CURATED_ETFS,
        get_static_constituents,
    )

    etf_tickers = etf_tickers or CURATED_ETFS
    result: dict[str, list[dict]] = {}

    for etf in etf_tickers:
        static_members = get_static_constituents(etf)

        yf_weights: dict[str, float] = {}
        if use_yfinance:
            for item in fetch_etf_holdings_yfinance(etf):
                yf_weights[item["ticker"]] = item.get("weight")

        all_tickers = list(dict.fromkeys(
            list(yf_weights.keys()) + static_members
        ))

        constituents: list[dict] = []
        for ticker in all_tickers:
            constituents.append({
                "ticker": ticker,
                "weight": yf_weights.get(ticker),
            })

        result[etf] = constituents
        logger.info(
            "etf_data_built",
            etf=etf,
            static=len(static_members),
            yfinance=len(yf_weights),
            total=len(constituents),
        )

    return result
