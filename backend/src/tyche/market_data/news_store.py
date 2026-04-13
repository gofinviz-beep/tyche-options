"""Per-ticker Parquet store for news articles.

Storage layout:
  data/news_articles/{TICKER}.parquet — one file per ticker

Each file contains news articles that mention the ticker, with optional
Gemini-based classification fields (event_type, sentiment, impact_score).
A single article mentioning multiple tickers produces one row in each
ticker's Parquet file.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import structlog

logger = structlog.get_logger()

NEWS_ARTICLE_SCHEMA = pa.schema(
    [
        ("article_id", pa.string()),
        ("source", pa.string()),
        ("ticker", pa.string()),
        ("published_at", pa.timestamp("us", tz="UTC")),
        ("title", pa.string()),
        ("url", pa.string()),
        ("author", pa.string()),
        ("summary", pa.string()),
        ("event_type", pa.string()),
        ("sentiment", pa.string()),
        ("impact_score", pa.float64()),
        ("relevance", pa.string()),
        ("classified_at", pa.timestamp("us", tz="UTC")),
    ]
)

_CLASSIFICATION_COLS = ("event_type", "sentiment", "impact_score", "relevance", "classified_at")


class NewsArticleStore:
    """Manages per-ticker Parquet files of news articles.

    Layout: ``data/news_articles/{TICKER}.parquet``
    """

    def __init__(self, data_dir: str = "data") -> None:
        self._store_dir = Path(data_dir) / "news_articles"
        self._store_dir.mkdir(parents=True, exist_ok=True)

    @property
    def store_dir(self) -> Path:
        return self._store_dir

    def _ticker_path(self, ticker: str) -> Path:
        safe = ticker.upper().replace("/", "_").replace(" ", "_")
        return self._store_dir / f"{safe}.parquet"

    def write_articles(self, ticker: str, articles: list[dict]) -> int:
        """Persist articles for a ticker, merging with existing data.

        Deduplicates on ``article_id``.  Returns total row count after write.
        """
        if not articles:
            return 0

        df = pd.DataFrame(articles)
        df["ticker"] = ticker.upper()

        if "published_at" in df.columns:
            df["published_at"] = pd.to_datetime(df["published_at"], utc=True)

        for col in _CLASSIFICATION_COLS:
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
            existing["published_at"] = pd.to_datetime(
                existing["published_at"], utc=True
            )
            if "classified_at" in existing.columns:
                existing["classified_at"] = pd.to_datetime(
                    existing["classified_at"], utc=True
                )
            combined = pd.concat([existing, df], ignore_index=True)
        else:
            combined = df

        combined = (
            combined.drop_duplicates(subset=["article_id"], keep="last")
            .sort_values("published_at")
            .reset_index(drop=True)
        )

        schema_cols = [f.name for f in NEWS_ARTICLE_SCHEMA]
        for col in schema_cols:
            if col not in combined.columns:
                if col == "impact_score":
                    combined[col] = float("nan")
                elif col in ("published_at", "classified_at"):
                    combined[col] = pd.NaT
                else:
                    combined[col] = None

        combined = combined[schema_cols]

        table = pa.Table.from_pandas(combined, schema=NEWS_ARTICLE_SCHEMA, preserve_index=False)
        pq.write_table(table, path, compression="snappy")

        logger.debug("news_articles_written", ticker=ticker, rows=len(combined))
        return len(combined)

    def read_articles(
        self, ticker: str, since: datetime | None = None
    ) -> pd.DataFrame:
        """Read articles for a ticker, optionally filtered by publish date."""
        path = self._ticker_path(ticker)
        if not path.exists():
            return pd.DataFrame(columns=[f.name for f in NEWS_ARTICLE_SCHEMA])

        df = pd.read_parquet(path)
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
        if "classified_at" in df.columns:
            df["classified_at"] = pd.to_datetime(df["classified_at"], utc=True)

        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            df = df[df["published_at"] >= since]

        return df.sort_values("published_at", ascending=False).reset_index(drop=True)

    def read_recent(self, ticker: str, hours: int = 48) -> pd.DataFrame:
        """Read articles from the last N hours."""
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        return self.read_articles(ticker, since=cutoff)

    def read_unclassified(self, ticker: str) -> pd.DataFrame:
        """Read articles that haven't been classified yet."""
        df = self.read_articles(ticker)
        if df.empty:
            return df
        return df[df["event_type"].isna() | (df["event_type"] == "")].reset_index(
            drop=True
        )

    def update_classification(
        self, ticker: str, article_id: str, classification: dict
    ) -> bool:
        """Update classification fields for a single article.

        Returns True if the article was found and updated.
        """
        path = self._ticker_path(ticker)
        if not path.exists():
            return False

        df = pd.read_parquet(path)
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
        if "classified_at" in df.columns:
            df["classified_at"] = pd.to_datetime(df["classified_at"], utc=True)

        mask = df["article_id"] == article_id
        if not mask.any():
            return False

        for key, value in classification.items():
            if key in df.columns:
                df.loc[mask, key] = value

        df.loc[mask, "classified_at"] = datetime.now(tz=timezone.utc)

        schema_cols = [f.name for f in NEWS_ARTICLE_SCHEMA]
        df = df[schema_cols]
        table = pa.Table.from_pandas(df, schema=NEWS_ARTICLE_SCHEMA, preserve_index=False)
        pq.write_table(table, path, compression="snappy")
        return True

    def bulk_update_classifications(
        self, ticker: str, classifications: dict[str, dict]
    ) -> int:
        """Update classification for multiple articles in one read-write cycle.

        Args:
            ticker: The ticker symbol.
            classifications: Mapping of article_id -> classification dict.

        Returns:
            Number of articles updated.
        """
        path = self._ticker_path(ticker)
        if not path.exists():
            return 0

        df = pd.read_parquet(path)
        df["published_at"] = pd.to_datetime(df["published_at"], utc=True)
        if "classified_at" in df.columns:
            df["classified_at"] = pd.to_datetime(df["classified_at"], utc=True)

        now = datetime.now(tz=timezone.utc)
        updated = 0
        for article_id, cls_data in classifications.items():
            mask = df["article_id"] == article_id
            if not mask.any():
                continue
            for key, value in cls_data.items():
                if key in df.columns:
                    df.loc[mask, key] = value
            df.loc[mask, "classified_at"] = now
            updated += 1

        if updated > 0:
            schema_cols = [f.name for f in NEWS_ARTICLE_SCHEMA]
            df = df[schema_cols]
            table = pa.Table.from_pandas(
                df, schema=NEWS_ARTICLE_SCHEMA, preserve_index=False
            )
            pq.write_table(table, path, compression="snappy")

        return updated

    def list_tickers(self) -> list[str]:
        """List all tickers with stored articles."""
        return sorted(
            p.stem for p in self._store_dir.glob("*.parquet")
        )

    def read_all_recent(self, hours: int = 48) -> pd.DataFrame:
        """Read recent articles across all tickers."""
        frames: list[pd.DataFrame] = []
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        for path in self._store_dir.glob("*.parquet"):
            ticker = path.stem
            df = self.read_articles(ticker, since=cutoff)
            if not df.empty:
                frames.append(df)
        if not frames:
            return pd.DataFrame(columns=[f.name for f in NEWS_ARTICLE_SCHEMA])
        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["article_id", "ticker"], keep="last")
            .sort_values("published_at", ascending=False)
            .reset_index(drop=True)
        )
