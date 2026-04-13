"""News article classifier using Gemini for entity extraction + event classification.

Uses gemini-2.5-flash-lite by default (cheap structured extraction). Articles are
batched (10 per API call) and processed through a rate-paced asyncio.Queue to stay
within RPM limits and avoid 429 errors.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, Field

from tyche.analysis.client import GeminiClient

logger = structlog.get_logger()

_EVENT_TYPES = (
    "earnings",
    "regulatory",
    "executive",
    "legal",
    "product",
    "macro_tariff",
    "analyst",
    "m_and_a",
    "operational",
    "offering",
    "dividend",
    "financial_results",
    "material_agreement",
    "other",
)

_SYSTEM_PROMPT = """You are a financial news classifier for an options trading system.
Your job is to analyze news articles and extract structured information.

Rules:
- Extract all stock tickers mentioned and classify their relevance:
  - "primary": the article is primarily about this ticker
  - "secondary": significant mention that could affect this ticker
  - "passing": listed in a market roundup or mentioned peripherally
- Classify the event type from this fixed taxonomy:
  {event_types}
- Score impact from -1.0 (catastrophic negative) to +1.0 (major positive catalyst)
- Be conservative: most market news is neutral (0.0 to +/-0.2)
- Reserve extreme scores (beyond +/-0.5) for truly material events:
  - Earnings miss/beat, regulatory action, CEO departure, M&A, major lawsuit
- Sentiment must be one of: positive, negative, neutral
- Provide a 1-sentence reasoning for your classification"""

_ARTICLE_PROMPT = """Classify this news article:

Title: {title}
Published: {published_at}
Summary: {summary}

Respond with the structured classification."""

_BATCH_SYSTEM_PROMPT = """You are a financial news classifier for an options trading system.
Your job is to analyze MULTIPLE news articles and return one classification per article.

Rules:
- For EACH article, extract tickers mentioned with relevance (primary/secondary/passing)
- Classify event type from: {event_types}
- Score impact from -1.0 to +1.0. Be conservative: most news is neutral (0.0 to +/-0.2)
- Reserve extreme scores (beyond +/-0.5) for truly material events
- Sentiment must be one of: positive, negative, neutral
- Return exactly one classification per article, preserving the article_id"""

_BATCH_ARTICLE_PROMPT = """Classify each of these {count} news articles:

{articles}

Return one classification per article with matching article_id."""

# 8-K item numbers mapped to event categories
_8K_ITEM_MAP = {
    "1.01": "material_agreement",
    "1.02": "material_agreement",
    "1.03": "regulatory",
    "2.01": "m_and_a",
    "2.02": "financial_results",
    "2.03": "offering",
    "2.04": "operational",
    "2.05": "operational",
    "2.06": "operational",
    "3.01": "regulatory",
    "3.02": "offering",
    "3.03": "dividend",
    "4.01": "regulatory",
    "4.02": "financial_results",
    "5.01": "executive",
    "5.02": "executive",
    "5.03": "regulatory",
    "5.05": "regulatory",
    "5.07": "regulatory",
    "7.01": "regulatory",
    "8.01": "other",
    "9.01": "other",
}

_8K_SYSTEM_PROMPT = """You are a financial filing classifier for an options trading system.
Your job is to analyze SEC 8-K filing content and assess its market impact.

Rules:
- This is an official SEC filing, not a news article. Treat it as primary source material.
- The ticker for this filing is: {ticker}
- Classify the event type from this fixed taxonomy:
  {event_types}
- Score impact from -1.0 (catastrophic negative) to +1.0 (major positive catalyst)
- 8-K filings are often material events — don't default to neutral unless truly routine
- Key high-impact items:
  - Item 2.02 (Results of Operations): earnings/revenue disclosure
  - Item 1.01/1.02 (Material Agreements): contracts, partnerships, terminations
  - Item 5.02 (Director/Officer changes): C-suite departures
  - Item 2.01 (Acquisitions/Dispositions): M&A events
  - Item 4.02 (Non-reliance on financials): always highly negative
- Sentiment must be one of: positive, negative, neutral
- Provide a 1-sentence reasoning for your classification"""

_8K_FILING_PROMPT = """Classify this SEC 8-K filing:

Ticker: {ticker}
Form Type: {form_type}
Filed: {filed_at}
Description: {description}
Items Reported: {items_reported}

Content excerpt:
{content}

Respond with the structured classification."""

_8K_BATCH_SYSTEM_PROMPT = """You are a financial filing classifier for an options trading system.
Your job is to analyze MULTIPLE SEC 8-K filings and return one classification per filing.

Rules:
- For EACH filing, classify the event type from: {event_types}
- Score impact from -1.0 to +1.0
- 8-K filings are often material events — don't default to neutral unless truly routine
- Sentiment must be one of: positive, negative, neutral
- Return exactly one classification per filing, preserving the accession_no"""

_8K_BATCH_PROMPT = """Classify each of these {count} SEC 8-K filings:

{filings}

Return one classification per filing with matching accession_no."""

_ARTICLES_BATCH_SIZE = 10
_FILINGS_BATCH_SIZE = 5


class TickerMention(BaseModel):
    """A ticker mentioned in the article with relevance level."""

    ticker: str
    relevance: str = Field(description="primary, secondary, or passing")


class ArticleClassification(BaseModel):
    """Structured output from Gemini for a single news article."""

    tickers_mentioned: list[TickerMention] = Field(default_factory=list)
    event_type: str = Field(description="Event type from the fixed taxonomy")
    sentiment: str = Field(description="positive, negative, or neutral")
    impact_score: float = Field(
        description="Impact score from -1.0 to +1.0", ge=-1.0, le=1.0
    )
    reasoning: str = Field(description="1-sentence explanation")


class BatchArticleClassification(BaseModel):
    """Single entry in a batch classification response."""

    article_id: str = Field(description="ID of the classified article")
    tickers_mentioned: list[TickerMention] = Field(default_factory=list)
    event_type: str = Field(description="Event type from the fixed taxonomy")
    sentiment: str = Field(description="positive, negative, or neutral")
    impact_score: float = Field(
        description="Impact score from -1.0 to +1.0", ge=-1.0, le=1.0
    )
    reasoning: str = Field(description="1-sentence explanation")


class Batch8KClassification(BaseModel):
    """Single entry in a batch 8-K classification response."""

    accession_no: str = Field(description="Accession number of the filing")
    tickers_mentioned: list[TickerMention] = Field(default_factory=list)
    event_type: str = Field(description="Event type from the fixed taxonomy")
    sentiment: str = Field(description="positive, negative, or neutral")
    impact_score: float = Field(
        description="Impact score from -1.0 to +1.0", ge=-1.0, le=1.0
    )
    reasoning: str = Field(description="1-sentence explanation")


@dataclass
class ClassificationResult:
    """Result of classifying a batch of articles."""

    classified: int = 0
    failed: int = 0
    errors: list[str] | None = None


def _sanitize(cls: ArticleClassification | BatchArticleClassification | Batch8KClassification) -> None:
    """Clamp impact score and normalize sentiment in-place."""
    if cls.impact_score < -1.0:
        cls.impact_score = -1.0
    elif cls.impact_score > 1.0:
        cls.impact_score = 1.0
    if cls.sentiment not in {"positive", "negative", "neutral"}:
        cls.sentiment = "neutral"


def _to_article_classification(
    batch_item: BatchArticleClassification | Batch8KClassification,
) -> ArticleClassification:
    """Convert a batch item to the canonical ArticleClassification."""
    return ArticleClassification(
        tickers_mentioned=batch_item.tickers_mentioned,
        event_type=batch_item.event_type,
        sentiment=batch_item.sentiment,
        impact_score=batch_item.impact_score,
        reasoning=batch_item.reasoning,
    )


def _chunked(items: list, size: int) -> list[list]:
    """Split a list into chunks of at most *size* items."""
    return [items[i : i + size] for i in range(0, len(items), size)]


class NewsClassifier:
    """Classifies news articles and 8-K filings using Gemini with structured output.

    Uses a queue + rate-paced workers to stay within RPM limits. Articles are
    batched (default 10 per API call) to reduce request count.
    """

    def __init__(
        self,
        gemini: GeminiClient,
        classify_model: str = "gemini-2.5-flash-lite",
        workers: int = 2,
        rpm: int = 25,
    ) -> None:
        self._gemini = gemini
        self._classify_model = classify_model
        self._workers = workers
        self._rpm = rpm

    async def classify_article(
        self, title: str, summary: str, published_at: str
    ) -> ArticleClassification:
        """Classify a single article (used by tests and one-off calls)."""
        system = _SYSTEM_PROMPT.format(event_types=", ".join(_EVENT_TYPES))
        prompt = _ARTICLE_PROMPT.format(
            title=title,
            published_at=published_at,
            summary=summary[:500],
        )

        result = await self._gemini.analyze(
            prompt=prompt,
            response_model=ArticleClassification,
            system_prompt=system,
            use_deep=False,
            temperature=0.1,
            model_override=self._classify_model,
        )
        _sanitize(result)
        return result

    async def classify_8k_filing(
        self,
        ticker: str,
        form_type: str,
        filed_at: str,
        description: str,
        items_reported: str,
        content: str,
    ) -> ArticleClassification:
        """Classify a single 8-K filing."""
        system = _8K_SYSTEM_PROMPT.format(
            ticker=ticker,
            event_types=", ".join(_EVENT_TYPES),
        )
        prompt = _8K_FILING_PROMPT.format(
            ticker=ticker,
            form_type=form_type,
            filed_at=filed_at,
            description=description,
            items_reported=items_reported or "N/A",
            content=content[:1500],
        )

        result = await self._gemini.analyze(
            prompt=prompt,
            response_model=ArticleClassification,
            system_prompt=system,
            use_deep=False,
            temperature=0.1,
            model_override=self._classify_model,
        )
        _sanitize(result)
        return result

    async def _classify_article_chunk(
        self, articles: list[dict]
    ) -> dict[str, ArticleClassification]:
        """Classify a chunk of articles in a single API call."""
        if len(articles) == 1:
            a = articles[0]
            cls = await self.classify_article(
                title=a.get("title", ""),
                summary=a.get("summary", ""),
                published_at=str(a.get("published_at", "")),
            )
            return {a["article_id"]: cls}

        system = _BATCH_SYSTEM_PROMPT.format(
            event_types=", ".join(_EVENT_TYPES)
        )
        formatted = "\n\n".join(
            f"--- Article {i+1} ---\n"
            f"article_id: {a['article_id']}\n"
            f"Title: {a.get('title', '')}\n"
            f"Published: {a.get('published_at', '')}\n"
            f"Summary: {str(a.get('summary', ''))[:500]}"
            for i, a in enumerate(articles)
        )
        prompt = _BATCH_ARTICLE_PROMPT.format(
            count=len(articles), articles=formatted
        )

        items = await self._gemini.analyze_batch(
            prompt=prompt,
            response_model=BatchArticleClassification,
            system_prompt=system,
            temperature=0.1,
            model_override=self._classify_model,
        )

        results: dict[str, ArticleClassification] = {}
        for item in items:
            _sanitize(item)
            results[item.article_id] = _to_article_classification(item)
        return results

    async def _classify_8k_chunk(
        self, filings: list[dict]
    ) -> dict[str, ArticleClassification]:
        """Classify a chunk of 8-K filings in a single API call."""
        if len(filings) == 1:
            f = filings[0]
            cls = await self.classify_8k_filing(
                ticker=f.get("ticker", ""),
                form_type=f.get("form_type", "8-K"),
                filed_at=str(f.get("filed_at", "")),
                description=f.get("description", ""),
                items_reported=f.get("items_reported", ""),
                content=f.get("content_summary", ""),
            )
            return {f["accession_no"]: cls}

        system = _8K_BATCH_SYSTEM_PROMPT.format(
            event_types=", ".join(_EVENT_TYPES)
        )
        formatted = "\n\n".join(
            f"--- Filing {i+1} ---\n"
            f"accession_no: {f['accession_no']}\n"
            f"Ticker: {f.get('ticker', '')}\n"
            f"Form Type: {f.get('form_type', '8-K')}\n"
            f"Filed: {f.get('filed_at', '')}\n"
            f"Description: {f.get('description', '')}\n"
            f"Items Reported: {f.get('items_reported', 'N/A')}\n"
            f"Content excerpt: {str(f.get('content_summary', ''))[:1500]}"
            for i, f in enumerate(filings)
        )
        prompt = _8K_BATCH_PROMPT.format(
            count=len(filings), filings=formatted
        )

        items = await self._gemini.analyze_batch(
            prompt=prompt,
            response_model=Batch8KClassification,
            system_prompt=system,
            temperature=0.1,
            model_override=self._classify_model,
        )

        results: dict[str, ArticleClassification] = {}
        for item in items:
            _sanitize(item)
            results[item.accession_no] = _to_article_classification(item)
        return results

    async def classify_batch(
        self,
        articles: list[dict],
    ) -> dict[str, ArticleClassification]:
        """Classify articles using batched API calls and rate-paced workers.

        Articles are chunked (10 per call) and fed through an asyncio.Queue
        with N workers that pace requests to stay within RPM limits.
        """
        if not articles:
            return {}

        chunks = _chunked(articles, _ARTICLES_BATCH_SIZE)
        return await self._run_queue(
            chunks=chunks,
            classify_fn=self._classify_article_chunk,
            label="news",
        )

    async def classify_8k_batch(
        self,
        filings: list[dict],
    ) -> dict[str, ArticleClassification]:
        """Classify 8-K filings using batched API calls and rate-paced workers."""
        if not filings:
            return {}

        chunks = _chunked(filings, _FILINGS_BATCH_SIZE)
        return await self._run_queue(
            chunks=chunks,
            classify_fn=self._classify_8k_chunk,
            label="8k",
        )

    async def _run_queue(
        self,
        chunks: list[list[dict]],
        classify_fn,
        label: str,
    ) -> dict[str, ArticleClassification]:
        """Process chunks through a queue with rate-paced workers."""
        queue: asyncio.Queue[list[dict]] = asyncio.Queue()
        for chunk in chunks:
            queue.put_nowait(chunk)

        results: dict[str, ArticleClassification] = {}
        errors: list[str] = []
        lock = asyncio.Lock()
        interval = 60.0 / max(self._rpm, 1)

        async def _worker(worker_id: int) -> None:
            while True:
                try:
                    chunk = queue.get_nowait()
                except asyncio.QueueEmpty:
                    return
                try:
                    chunk_results = await classify_fn(chunk)
                    async with lock:
                        results.update(chunk_results)
                except Exception as exc:
                    ids = [
                        c.get("article_id") or c.get("accession_no", "?")
                        for c in chunk
                    ]
                    msg = f"Batch classification failed for {ids}: {exc}"
                    logger.warning(
                        f"{label}_batch_classification_failed",
                        ids=ids,
                        error=str(exc),
                    )
                    async with lock:
                        errors.append(msg)
                finally:
                    queue.task_done()
                    await asyncio.sleep(interval)

        num_workers = min(self._workers, len(chunks))
        worker_tasks = [
            asyncio.create_task(_worker(i)) for i in range(num_workers)
        ]
        await queue.join()
        for t in worker_tasks:
            t.cancel()

        total_items = sum(len(c) for c in chunks)
        logger.info(
            f"{label}_classification_batch_complete",
            classified=len(results),
            failed=len(errors),
            total=total_items,
            api_calls=len(chunks),
        )
        return results
