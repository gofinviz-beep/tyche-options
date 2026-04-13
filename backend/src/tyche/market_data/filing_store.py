"""Per-ticker Parquet stores for 8-K filings and insider transactions.

Storage layout:
  data/filings_8k/{TICKER}.parquet          — 8-K filing metadata + classification
  data/insider_transactions/{TICKER}.parquet — Form 4 structured transaction data
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()

# ── 8-K Schema ─────────────────────────────────────────────────────────

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

# ── Form 4 Schema ──────────────────────────────────────────────────────

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


def _safe_ticker(ticker: str) -> str:
    return ticker.upper().replace("/", "_").replace(" ", "_")


# ── Filing8KStore ──────────────────────────────────────────────────────


class Filing8KStore:
    """Per-ticker Parquet store for 8-K filing metadata + Gemini classification."""

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "filings_8k"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        return self._store_dir / f"{_safe_ticker(ticker)}.parquet"

    def write_filings(self, ticker: str, filings: list[dict]) -> int:
        """Persist 8-K filings, deduplicating on accession_no.

        Returns total row count after write.
        """
        if not filings:
            return 0

        df = pd.DataFrame(filings)
        df["ticker"] = ticker.upper()

        if "filed_at" in df.columns:
            df["filed_at"] = pd.to_datetime(df["filed_at"], utc=True)

        for col in _8K_CLASSIFICATION_COLS:
            if col not in df.columns:
                if col == "impact_score":
                    df[col] = float("nan")
                elif col == "classified_at":
                    df[col] = pd.NaT
                else:
                    df[col] = None

        if "classified_at" in df.columns:
            df["classified_at"] = pd.to_datetime(df["classified_at"], utc=True)

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["filed_at"] = pd.to_datetime(existing["filed_at"], utc=True)
            if "classified_at" in existing.columns:
                existing["classified_at"] = pd.to_datetime(
                    existing["classified_at"], utc=True
                )
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        combined = (
            combined.drop_duplicates(subset=["accession_no"], keep="last")
            .sort_values("filed_at")
            .reset_index(drop=True)
        )

        schema_cols = [f.name for f in FILING_8K_SCHEMA]
        for col in schema_cols:
            if col not in combined.columns:
                if col == "impact_score":
                    combined[col] = float("nan")
                elif col in ("filed_at", "classified_at"):
                    combined[col] = pd.NaT
                else:
                    combined[col] = None

        combined = combined[schema_cols]
        table = pa.Table.from_pandas(
            combined, schema=FILING_8K_SCHEMA, preserve_index=False
        )
        pq.write_table(table, path, compression="snappy")
        logger.debug("filings_8k_written", ticker=ticker, rows=len(combined))
        return len(combined)

    def read_filings(
        self, ticker: str, since: datetime | None = None
    ) -> pd.DataFrame:
        """Read 8-K filings for a ticker, optionally filtered by filed date."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in FILING_8K_SCHEMA])

        df = pd.read_parquet(path)
        df["filed_at"] = pd.to_datetime(df["filed_at"], utc=True)
        if "classified_at" in df.columns:
            df["classified_at"] = pd.to_datetime(df["classified_at"], utc=True)

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
        """Update classification for multiple 8-K filings.

        Args:
            ticker: The ticker symbol.
            classifications: Mapping of accession_no -> classification dict.

        Returns:
            Number of filings updated.
        """
        path = self._ticker_path(ticker)
        if not path.exists():
            return 0

        df = pd.read_parquet(path)
        df["filed_at"] = pd.to_datetime(df["filed_at"], utc=True)
        if "classified_at" in df.columns:
            df["classified_at"] = pd.to_datetime(df["classified_at"], utc=True)

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
            schema_cols = [f.name for f in FILING_8K_SCHEMA]
            df = df[schema_cols]
            table = pa.Table.from_pandas(
                df, schema=FILING_8K_SCHEMA, preserve_index=False
            )
            pq.write_table(table, path, compression="snappy")

        return updated

    def list_tickers(self) -> list[str]:
        return sorted(p.stem for p in self._store_dir.glob("*.parquet"))


# ── InsiderTxStore ─────────────────────────────────────────────────────


class InsiderTxStore:
    """Per-ticker Parquet store for Form 4 insider transactions."""

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "insider_transactions"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        return self._store_dir / f"{_safe_ticker(ticker)}.parquet"

    def write_transactions(self, ticker: str, transactions: list[dict]) -> int:
        """Persist insider transactions, deduplicating on
        (accession_no, insider_name, transaction_type).

        Returns total row count after write.
        """
        if not transactions:
            return 0

        df = pd.DataFrame(transactions)
        df["ticker"] = ticker.upper()

        if "filed_at" in df.columns:
            df["filed_at"] = pd.to_datetime(df["filed_at"], utc=True)
        if "period_of_report" in df.columns:
            df["period_of_report"] = pd.to_datetime(df["period_of_report"]).dt.date

        for bool_col in ("is_officer", "is_director", "is_ten_pct_owner"):
            if bool_col in df.columns:
                df[bool_col] = df[bool_col].astype(bool)
            else:
                df[bool_col] = False

        path = self._ticker_path(ticker)
        if path.exists():
            existing = pd.read_parquet(path)
            existing["filed_at"] = pd.to_datetime(existing["filed_at"], utc=True)
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        dedup_cols = ["accession_no", "insider_name", "transaction_type"]
        combined = (
            combined.drop_duplicates(subset=dedup_cols, keep="last")
            .sort_values("filed_at")
            .reset_index(drop=True)
        )

        schema_cols = [f.name for f in INSIDER_TX_SCHEMA]
        for col in schema_cols:
            if col not in combined.columns:
                if col in ("shares", "price_per_share", "total_value", "shares_owned_after"):
                    combined[col] = float("nan")
                elif col == "filed_at":
                    combined[col] = pd.NaT
                elif col == "period_of_report":
                    combined[col] = None
                elif col in ("is_officer", "is_director", "is_ten_pct_owner"):
                    combined[col] = False
                else:
                    combined[col] = None

        combined = combined[schema_cols]
        table = pa.Table.from_pandas(
            combined, schema=INSIDER_TX_SCHEMA, preserve_index=False
        )
        pq.write_table(table, path, compression="snappy")
        logger.debug("insider_tx_written", ticker=ticker, rows=len(combined))
        return len(combined)

    def read_transactions(
        self, ticker: str, since: datetime | None = None
    ) -> pd.DataFrame:
        """Read insider transactions for a ticker."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in INSIDER_TX_SCHEMA])

        df = pd.read_parquet(path)
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
        return sorted(p.stem for p in self._store_dir.glob("*.parquet"))
