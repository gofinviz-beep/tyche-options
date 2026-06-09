"""Per-ticker Parquet stores for 8-K filings and insider transactions.

Storage layout:
  data/filings_8k/{TICKER}.parquet          — 8-K filing metadata + classification
  data/insider_transactions/{TICKER}.parquet — Form 4 structured transaction data
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

logger = structlog.get_logger()

FILING_8K_SCHEMA = pa.schema(
    [
        ("accession_no", pa.string()),
        ("ticker", pa.string()),
        ("cik", pa.string()),
        ("filed_at", pa.timestamp("us", tz="UTC")),
        ("form_type", pa.string()),
        ("description", pa.string()),
        ("filing_url", pa.string()),
        ("items_reported", pa.string()),
        ("content_summary", pa.string()),
        ("event_type", pa.string()),
        ("sentiment", pa.string()),
        ("impact_score", pa.float64()),
        ("classified_at", pa.timestamp("us", tz="UTC")),
    ]
)

_8K_CLASSIFICATION_COLS = ("event_type", "sentiment", "impact_score", "classified_at")

INSIDER_TX_SCHEMA = pa.schema(
    [
        ("accession_no", pa.string()),
        ("ticker", pa.string()),
        ("cik", pa.string()),
        ("filed_at", pa.timestamp("us", tz="UTC")),
        ("period_of_report", pa.date32()),
        ("insider_name", pa.string()),
        ("insider_title", pa.string()),
        ("is_officer", pa.bool_()),
        ("is_director", pa.bool_()),
        ("is_ten_pct_owner", pa.bool_()),
        ("transaction_type", pa.string()),
        ("shares", pa.float64()),
        ("price_per_share", pa.float64()),
        ("total_value", pa.float64()),
        ("shares_owned_after", pa.float64()),
        ("acquisition_or_disposition", pa.string()),
    ]
)


def _empty_8k_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[f.name for f in FILING_8K_SCHEMA])


def _empty_insider_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[f.name for f in INSIDER_TX_SCHEMA])


def _normalize_8k_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_8k_df()
    out = df.copy()
    out["filed_at"] = pd.to_datetime(out["filed_at"], utc=True)
    if "classified_at" in out.columns:
        out["classified_at"] = pd.to_datetime(out["classified_at"], utc=True)
    return out


def _prepare_8k_rows(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    out = df.copy()
    out["ticker"] = ticker.upper()
    if "filed_at" in out.columns:
        out["filed_at"] = pd.to_datetime(out["filed_at"], utc=True)
    for col in _8K_CLASSIFICATION_COLS:
        if col not in out.columns:
            if col == "impact_score":
                out[col] = float("nan")
            elif col == "classified_at":
                out[col] = pd.NaT
            else:
                out[col] = None
    if "classified_at" in out.columns:
        out["classified_at"] = pd.to_datetime(out["classified_at"], utc=True)
    return out


class Filing8KStore:
    """Per-ticker Parquet store for 8-K filing metadata + Gemini classification."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("filings_8k", data_dir, ctx)

    @property
    def store_dir(self):
        return self._io.store_dir

    def _read_ticker_df(self, ticker: str) -> pd.DataFrame:
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return _empty_8k_df()
        return _normalize_8k_df(df)

    def _write_ticker_df(self, ticker: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        ordered = df[[f.name for f in FILING_8K_SCHEMA]]
        self._io.write_df(
            self._io.ticker_rel(ticker),
            ordered,
            schema=FILING_8K_SCHEMA,
        )
        return len(ordered)

    def write_filings(self, ticker: str, filings: list[dict]) -> int:
        """Persist 8-K filings, deduplicating on accession_no."""
        if not filings:
            return 0

        df = _prepare_8k_rows(pd.DataFrame(filings), ticker)
        return self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            FILING_8K_SCHEMA,
            ["accession_no"],
            sort_cols=["filed_at"],
        )

    def read_filings(
        self, ticker: str, since: datetime | None = None
    ) -> pd.DataFrame:
        """Read 8-K filings for a ticker, optionally filtered by filed date."""
        df = self._read_ticker_df(ticker)
        if df.empty:
            return df

        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            df = df[df["filed_at"] >= since]

        return df.sort_values("filed_at", ascending=False).reset_index(drop=True)

    def read_recent(self, ticker: str, days: int = 30) -> pd.DataFrame:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return self.read_filings(ticker, since=cutoff)

    def read_unclassified(self, ticker: str) -> pd.DataFrame:
        df = self.read_filings(ticker)
        if df.empty:
            return df
        return df[df["event_type"].isna() | (df["event_type"] == "")].reset_index(
            drop=True
        )

    def bulk_update_classifications(
        self, ticker: str, classifications: dict[str, dict]
    ) -> int:
        """Update classification for multiple 8-K filings."""
        df = self._read_ticker_df(ticker)
        if df.empty:
            return 0

        now = datetime.now(tz=timezone.utc)
        updated = 0
        for acc_no, cls_data in classifications.items():
            mask = df["accession_no"] == acc_no
            if not mask.any():
                continue
            for key, value in cls_data.items():
                if key in df.columns:
                    df.loc[mask, key] = value
            df.loc[mask, "classified_at"] = now
            updated += 1

        if updated > 0:
            self._write_ticker_df(ticker, df)

        return updated

    def list_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()


class InsiderTxStore:
    """Per-ticker Parquet store for Form 4 insider transactions."""

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("insider_transactions", data_dir, ctx)

    @property
    def store_dir(self):
        return self._io.store_dir

    def _prepare_insider_rows(self, df: pd.DataFrame, ticker: str) -> pd.DataFrame:
        out = df.copy()
        out["ticker"] = ticker.upper()
        if "filed_at" in out.columns:
            out["filed_at"] = pd.to_datetime(out["filed_at"], utc=True)
        if "period_of_report" in out.columns:
            out["period_of_report"] = pd.to_datetime(out["period_of_report"]).dt.date
        for bool_col in ("is_officer", "is_director", "is_ten_pct_owner"):
            if bool_col in out.columns:
                out[bool_col] = out[bool_col].astype(bool)
            else:
                out[bool_col] = False
        return out

    def write_transactions(self, ticker: str, transactions: list[dict]) -> int:
        """Persist insider transactions, deduplicating on accession/insider/type."""
        if not transactions:
            return 0

        df = self._prepare_insider_rows(pd.DataFrame(transactions), ticker)
        return self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            INSIDER_TX_SCHEMA,
            ["accession_no", "insider_name", "transaction_type"],
            sort_cols=["filed_at"],
        )

    def read_transactions(
        self, ticker: str, since: datetime | None = None
    ) -> pd.DataFrame:
        """Read insider transactions for a ticker."""
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return _empty_insider_df()

        df = df.copy()
        df["filed_at"] = pd.to_datetime(df["filed_at"], utc=True)

        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            df = df[df["filed_at"] >= since]

        return df.sort_values("filed_at", ascending=False).reset_index(drop=True)

    def read_recent(self, ticker: str, days: int = 30) -> pd.DataFrame:
        cutoff = datetime.now(tz=timezone.utc) - timedelta(days=days)
        return self.read_transactions(ticker, since=cutoff)

    def list_tickers(self) -> list[str]:
        return self._io.list_ticker_stems()
