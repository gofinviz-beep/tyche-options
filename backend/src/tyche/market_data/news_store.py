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

import pandas as pd
import pyarrow as pa
import structlog

from tyche.storage.paths import StorageContext
from tyche.storage.store_io import StoreBackend

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

_CLASSIFICATION_COLS = (
    "event_type",
    "sentiment",
    "impact_score",
    "relevance",
    "classified_at",
)


def _empty_articles_df() -> pd.DataFrame:
    return pd.DataFrame(columns=[f.name for f in NEWS_ARTICLE_SCHEMA])


def _normalize_article_df(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return _empty_articles_df()
    out = df.copy()
    if "published_at" in out.columns:
        out["published_at"] = pd.to_datetime(out["published_at"], utc=True)
    if "classified_at" in out.columns:
        out["classified_at"] = pd.to_datetime(out["classified_at"], utc=True)
    return out


def _prepare_new_articles(df: pd.DataFrame, ticker: str) -> pd.DataFrame:
    out = df.copy()
    out["ticker"] = ticker.upper()
    if "published_at" in out.columns:
        out["published_at"] = pd.to_datetime(out["published_at"], utc=True)
    for col in _CLASSIFICATION_COLS:
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


class NewsArticleStore:
    """Manages per-ticker Parquet files of news articles.

    Layout: ``data/news_articles/{TICKER}.parquet``
    """

    def __init__(
        self,
        data_dir: str = "data",
        ctx: StorageContext | None = None,
    ) -> None:
        self._io = StoreBackend.create("news_articles", data_dir, ctx)

    @property
    def store_dir(self):
        return self._io.store_dir

    def _read_ticker_df(self, ticker: str) -> pd.DataFrame:
        df = self._io.read_df(self._io.ticker_rel(ticker))
        if df is None or df.empty:
            return _empty_articles_df()
        return _normalize_article_df(df)

    def _write_ticker_df(self, ticker: str, df: pd.DataFrame) -> int:
        if df.empty:
            return 0
        ordered = df[[f.name for f in NEWS_ARTICLE_SCHEMA]]
        self._io.write_df(
            self._io.ticker_rel(ticker),
            ordered,
            schema=NEWS_ARTICLE_SCHEMA,
        )
        return len(ordered)

    def write_articles(self, ticker: str, articles: list[dict]) -> int:
        """Persist articles for a ticker, merging with existing data.

        Deduplicates on ``article_id``.  Returns total row count after write.
        """
        if not articles:
            return 0

        df = _prepare_new_articles(pd.DataFrame(articles), ticker)
        return self._io.merge_write(
            self._io.ticker_rel(ticker),
            df,
            NEWS_ARTICLE_SCHEMA,
            ["article_id"],
            sort_cols=["published_at"],
        )

    def read_articles(
        self, ticker: str, since: datetime | None = None
    ) -> pd.DataFrame:
        """Read articles for a ticker, optionally filtered by publish date."""
        df = self._read_ticker_df(ticker)
        if df.empty:
            return df

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
        """Update classification fields for a single article."""
        df = self._read_ticker_df(ticker)
        if df.empty:
            return False

        mask = df["article_id"] == article_id
        if not mask.any():
            return False

        for key, value in classification.items():
            if key in df.columns:
                df.loc[mask, key] = value

        df.loc[mask, "classified_at"] = datetime.now(tz=timezone.utc)
        self._write_ticker_df(ticker, df)
        return True

    def bulk_update_classifications(
        self, ticker: str, classifications: dict[str, dict]
    ) -> int:
        """Update classification for multiple articles in one read-write cycle."""
        df = self._read_ticker_df(ticker)
        if df.empty:
            return 0

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
            self._write_ticker_df(ticker, df)

        return updated

    def list_tickers(self) -> list[str]:
        """List all tickers with stored articles."""
        return self._io.list_ticker_stems()

    def read_all_recent(self, hours: int = 48) -> pd.DataFrame:
        """Read recent articles across all tickers."""
        frames: list[pd.DataFrame] = []
        cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=hours)
        for rel in self._io.iter_parquet_rels():
            ticker = rel.rsplit("/", 1)[-1].replace(".parquet", "")
            df = self.read_articles(ticker, since=cutoff)
            if not df.empty:
                frames.append(df)
        if not frames:
            return _empty_articles_df()
        return (
            pd.concat(frames, ignore_index=True)
            .drop_duplicates(subset=["article_id", "ticker"], keep="last")
            .sort_values("published_at", ascending=False)
            .reset_index(drop=True)
        )
