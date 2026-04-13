"""News ingestion service — fetches from Polygon + Finnhub, deduplicates, persists.

Orchestrates multi-source news fetching for the conviction universe and
writes raw articles to the per-ticker Parquet store. Classification is
handled separately by NewsClassifier.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import structlog

from tyche.market_data.finnhub import FinnhubArticle, FinnhubClient
from tyche.market_data.news_store import NewsArticleStore
from tyche.market_data.polygon import NewsArticle as PolygonNewsArticle, PolygonClient

logger = structlog.get_logger()


@dataclass
class IngestResult:
    """Summary of a news ingestion run."""

    polygon_fetched: int = 0
    finnhub_fetched: int = 0
    total_persisted: int = 0
    tickers_updated: int = 0
    errors: list[str] = field(default_factory=list)


class NewsIngestor:
    """Fetches news articles from Polygon and Finnhub, deduplicates, and persists.

    Polygon is queried in a single broad call (returns articles mentioning
    any of our tickers). Finnhub requires per-ticker calls, rate-limited
    via its client's built-in throttle.
    """

    def __init__(
        self,
        polygon: PolygonClient | None,
        finnhub: FinnhubClient | None,
        store: NewsArticleStore,
        tickers: list[str],
        finnhub_concurrency: int = 5,
    ) -> None:
        self._polygon = polygon
        self._finnhub = finnhub
        self._store = store
        self._tickers = [t.upper() for t in tickers]
        self._ticker_set = set(self._tickers)
        self._finnhub_concurrency = finnhub_concurrency

    async def ingest(
        self, since: datetime | None = None
    ) -> IngestResult:
        """Fetch articles from all sources, deduplicate, and persist.

        Args:
            since: Only fetch articles published after this time.
                   Defaults to 48 hours ago.
        """
        if since is None:
            since = datetime.now(tz=timezone.utc) - timedelta(hours=48)

        result = IngestResult()
        articles_by_ticker: dict[str, list[dict]] = {}

        polygon_articles = await self._fetch_polygon(since, result)
        for article in polygon_articles:
            for ticker in article.get("tickers_raw", []):
                ticker_upper = ticker.upper()
                if ticker_upper in self._ticker_set:
                    row = {**article}
                    row["ticker"] = ticker_upper
                    row.pop("tickers_raw", None)
                    articles_by_ticker.setdefault(ticker_upper, []).append(row)

        finnhub_articles = await self._fetch_finnhub(since, result)
        for article in finnhub_articles:
            ticker_upper = article["ticker"]
            articles_by_ticker.setdefault(ticker_upper, []).append(article)

        tickers_updated = 0
        for ticker, articles in articles_by_ticker.items():
            seen_ids: set[str] = set()
            deduped: list[dict] = []
            for art in articles:
                aid = art["article_id"]
                if aid not in seen_ids:
                    seen_ids.add(aid)
                    deduped.append(art)

            count = self._store.write_articles(ticker, deduped)
            if count > 0:
                tickers_updated += 1
            result.total_persisted += len(deduped)

        result.tickers_updated = tickers_updated

        logger.info(
            "news_ingestion_complete",
            polygon_fetched=result.polygon_fetched,
            finnhub_fetched=result.finnhub_fetched,
            total_persisted=result.total_persisted,
            tickers_updated=result.tickers_updated,
            errors=len(result.errors),
        )
        return result

    async def _fetch_polygon(
        self, since: datetime, result: IngestResult
    ) -> list[dict]:
        """Fetch broad news from Polygon (not per-ticker)."""
        if self._polygon is None:
            return []

        try:
            raw = await self._polygon.get_news(
                published_after=since,
                limit=200,
            )
            result.polygon_fetched = len(raw)

            articles: list[dict] = []
            for item in raw:
                pub_dt = _parse_polygon_datetime(item.published_utc)
                articles.append({
                    "article_id": f"polygon_{item.id}",
                    "source": "polygon",
                    "published_at": pub_dt,
                    "title": item.title,
                    "url": item.article_url,
                    "author": item.author,
                    "summary": item.description,
                    "tickers_raw": item.tickers,
                })
            return articles

        except Exception as exc:
            msg = f"Polygon news fetch failed: {exc}"
            logger.warning("polygon_news_error", error=str(exc))
            result.errors.append(msg)
            return []

    async def _fetch_finnhub(
        self, since: datetime, result: IngestResult
    ) -> list[dict]:
        """Fetch per-ticker news from Finnhub with concurrency control."""
        if self._finnhub is None:
            return []

        from_date = since.date()
        to_date = date.today()
        semaphore = asyncio.Semaphore(self._finnhub_concurrency)
        all_articles: list[dict] = []
        lock = asyncio.Lock()

        async def _fetch_one(ticker: str) -> None:
            async with semaphore:
                try:
                    items = await self._finnhub.get_company_news(
                        ticker, from_date, to_date
                    )
                    mapped = _map_finnhub_articles(ticker, items)
                    async with lock:
                        all_articles.extend(mapped)
                        result.finnhub_fetched += len(items)
                except Exception as exc:
                    msg = f"Finnhub fetch failed for {ticker}: {exc}"
                    logger.debug("finnhub_ticker_error", ticker=ticker, error=str(exc))
                    async with lock:
                        result.errors.append(msg)

        tasks = [asyncio.create_task(_fetch_one(t)) for t in self._tickers]
        await asyncio.gather(*tasks, return_exceptions=True)

        return all_articles


def _parse_polygon_datetime(raw: str) -> datetime:
    """Parse Polygon's published_utc string to aware datetime."""
    if not raw:
        return datetime.now(tz=timezone.utc)
    try:
        if raw.endswith("Z"):
            raw = raw[:-1] + "+00:00"
        return datetime.fromisoformat(raw)
    except (ValueError, TypeError):
        return datetime.now(tz=timezone.utc)


def _map_finnhub_articles(
    ticker: str, items: list[FinnhubArticle]
) -> list[dict]:
    """Convert Finnhub articles to the common article dict format."""
    articles: list[dict] = []
    for item in items:
        pub_dt = datetime.fromtimestamp(item.datetime_ts, tz=timezone.utc)
        articles.append({
            "article_id": f"finnhub_{item.id}",
            "source": "finnhub",
            "ticker": ticker.upper(),
            "published_at": pub_dt,
            "title": item.headline,
            "url": item.url,
            "author": item.source,
            "summary": item.summary,
        })
    return articles
