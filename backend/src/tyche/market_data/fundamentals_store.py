"""Per-ticker Parquet store for quarterly company fundamentals.

Storage layout:
  data/fundamentals/{TICKER}.parquet — one file per ticker

Each file is a point-in-time quarterly (and annual) financial time series:
revenue, margins, earnings, cash flow, balance-sheet, and share counts. The
``filing_date`` column records when each statement became public, so feature
extraction can avoid look-ahead bias by only consuming rows whose filing date
is on or before the as-of date.

Source: Finnhub ``/stock/financials`` (standardized, primary) or Polygon/Massive
``/vX/reference/financials`` fallback. As-reported Finnhub is used only when
standardized returns empty.
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

FUNDAMENTALS_SCHEMA = pa.schema(
    [
        ("ticker", pa.string()),
        ("period_end", pa.date32()),
        ("filing_date", pa.date32()),
        ("fiscal_year", pa.int64()),
        ("fiscal_period", pa.string()),  # Q1/Q2/Q3/Q4/FY
        ("timeframe", pa.string()),  # quarterly | annual | ttm
        ("revenue", pa.float64()),
        ("gross_profit", pa.float64()),
        ("operating_income", pa.float64()),
        ("net_income", pa.float64()),
        ("eps_diluted", pa.float64()),
        ("operating_cash_flow", pa.float64()),
        ("capex", pa.float64()),
        ("free_cash_flow", pa.float64()),
        ("cash_and_equivalents", pa.float64()),
        ("total_debt", pa.float64()),
        ("total_assets", pa.float64()),
        ("total_equity", pa.float64()),
        ("shares_diluted", pa.float64()),
        ("gross_margin", pa.float64()),
        ("operating_margin", pa.float64()),
        ("net_margin", pa.float64()),
    ]
)

_NUMERIC_COLS = [
    "revenue",
    "gross_profit",
    "operating_income",
    "net_income",
    "eps_diluted",
    "operating_cash_flow",
    "capex",
    "free_cash_flow",
    "cash_and_equivalents",
    "total_debt",
    "total_assets",
    "total_equity",
    "shares_diluted",
    "gross_margin",
    "operating_margin",
    "net_margin",
]


class FundamentalsStore:
    """Manages per-ticker Parquet files of quarterly fundamentals.

    Layout: ``data/fundamentals/{TICKER}.parquet``. Deduplicated on
    ``(period_end, timeframe)`` keeping the most recent write (handles
    restatements — a later filing for the same period replaces the earlier).
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("fundamentals", data_dir, ctx)

    @property
    def store_dir(self) -> Path:
        return self._io.store_dir

    @staticmethod
    def _coerce(df: pd.DataFrame) -> pd.DataFrame:
        """Normalise dtypes + derived margins, fill missing schema columns."""
        df = df.copy()
        df["period_end"] = pd.to_datetime(df["period_end"]).dt.date
        # filing_date defaults to period_end when unknown (conservative —
        # treats data as available only at period end, never earlier).
        if "filing_date" not in df.columns:
            df["filing_date"] = df["period_end"]
        df["filing_date"] = (
            pd.to_datetime(df["filing_date"]).fillna(pd.to_datetime(df["period_end"]))
        ).dt.date

        for col in _NUMERIC_COLS:
            if col not in df.columns:
                df[col] = pd.NA
            df[col] = pd.to_numeric(df[col], errors="coerce")

        if "fiscal_year" not in df.columns:
            df["fiscal_year"] = pd.to_datetime(df["period_end"]).dt.year
        df["fiscal_year"] = pd.to_numeric(df["fiscal_year"], errors="coerce").fillna(0).astype("int64")
        if "fiscal_period" not in df.columns:
            df["fiscal_period"] = ""
        df["fiscal_period"] = df["fiscal_period"].fillna("").astype(str)
        if "timeframe" not in df.columns:
            df["timeframe"] = "quarterly"
        df["timeframe"] = df["timeframe"].fillna("quarterly").astype(str)

        # Derive margins where revenue is present and a margin is missing.
        rev = df["revenue"]
        safe_rev = rev.where(rev.abs() > 0)
        for margin_col, num_col in (
            ("gross_margin", "gross_profit"),
            ("operating_margin", "operating_income"),
            ("net_margin", "net_income"),
        ):
            derived = df[num_col] / safe_rev * 100.0
            df[margin_col] = df[margin_col].fillna(derived)

        # Derive FCF when operating cash flow + capex are present.
        fcf_derived = df["operating_cash_flow"] + df["capex"]
        df["free_cash_flow"] = df["free_cash_flow"].fillna(fcf_derived)

        return df

    def write_financials(self, ticker: str, df: pd.DataFrame) -> int:
        """Persist a DataFrame of fundamentals for a ticker.

        Merges with existing data and deduplicates on
        ``(period_end, timeframe)`` keeping the latest. Returns total rows.
        """
        if df is None or df.empty:
            return 0

        df = self._coerce(df)
        df["ticker"] = ticker.upper()

        rows = self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            FUNDAMENTALS_SCHEMA,
            ["period_end", "timeframe"],
            sort_cols=["timeframe", "period_end"],
        )
        logger.debug("fundamentals_written", ticker=ticker, rows=rows)
        return rows

    def read_ticker(
        self,
        ticker: str,
        timeframe: str | None = "quarterly",
        as_of: date | None = None,
    ) -> pd.DataFrame:
        """Read fundamentals for a ticker, optionally point-in-time filtered.

        Args:
            ticker: Stock symbol.
            timeframe: ``quarterly`` (default), ``annual``, ``ttm``, or None
                for all rows.
            as_of: When set, only rows whose ``filing_date`` is on or before
                this date are returned (leakage-safe).
        """
        empty = pd.DataFrame(columns=[f.name for f in FUNDAMENTALS_SCHEMA])
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return empty
        df["period_end"] = pd.to_datetime(df["period_end"]).dt.date
        df["filing_date"] = pd.to_datetime(df["filing_date"]).dt.date

        if timeframe:
            df = df[df["timeframe"] == timeframe]
        if as_of is not None:
            df = df[df["filing_date"] <= as_of]

        return df.sort_values("period_end").reset_index(drop=True)

    def get_all_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()

    def get_latest_period_end(self, ticker: str) -> date | None:
        """Return the most recent ``period_end`` stored for a ticker."""
        df = self.read_ticker(ticker, timeframe=None)
        if df.empty:
            return None
        return df["period_end"].max()

    def get_stats(self) -> dict:
        tickers = self.get_all_tickers()
        total_rows = 0
        for t in tickers:
            total_rows += self._io.parquet_rows(self._io.ticker_rel(t))
        return {"ticker_count": len(tickers), "total_rows": total_rows}
