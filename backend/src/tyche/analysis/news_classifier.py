"""News article classifier using Gemini Flash for entity extraction + event classification.

Each article is classified individually (cheap with Flash, ~$0.001/article) to
avoid token overflow and produce clean per-article structured output.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

import structlog
from pydantic import BaseModel, Field

from tyche.analysis.client import GeminiClient
from tyche.exceptions import NewsClassificationError

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


@dataclass
class ClassificationResult:
    """Result of classifying a batch of articles."""

    classified: int = 0
    failed: int = 0
    errors: list[str] | None = None


class NewsClassifier:
    """Classifies news articles using Gemini Flash with structured output."""

    def __init__(
        self,
        gemini: GeminiClient,
        concurrency: int = 5,
    ) -> None:
        self._gemini = gemini
        self._concurrency = concurrency

    async def classify_article(
        self, title: str, summary: str, published_at: str
    ) -> ArticleClassification:
        """Classify a single article."""
        system = _SYSTEM_PROMPT.format(
            event_types=", ".join(_EVENT_TYPES)
        )
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
        )

        if result.impact_score < -1.0:
            result.impact_score = -1.0
        elif result.impact_score > 1.0:
            result.impact_score = 1.0

        valid_sentiments = {"positive", "negative", "neutral"}
        if result.sentiment not in valid_sentiments:
            result.sentiment = "neutral"

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
        """Classify a single 8-K filing.

        Reuses the same ArticleClassification schema — 8-K filings produce
        the same output structure (event_type, sentiment, impact_score).
        """
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
        )

        if result.impact_score < -1.0:
            result.impact_score = -1.0
        elif result.impact_score > 1.0:
            result.impact_score = 1.0

        valid_sentiments = {"positive", "negative", "neutral"}
        if result.sentiment not in valid_sentiments:
            result.sentiment = "neutral"

        return result

    async def classify_8k_batch(
        self,
        filings: list[dict],
    ) -> dict[str, ArticleClassification]:
        """Classify a batch of 8-K filings with concurrency control.

        Args:
            filings: List of dicts with 'accession_no', 'ticker', 'form_type',
                     'filed_at', 'description', 'items_reported', 'content_summary'.

        Returns:
            Mapping of accession_no -> ArticleClassification.
        """
        semaphore = asyncio.Semaphore(self._concurrency)
        results: dict[str, ArticleClassification] = {}
        lock = asyncio.Lock()
        errors: list[str] = []

        async def _classify_one(filing: dict) -> None:
            acc_no = filing["accession_no"]
            async with semaphore:
                try:
                    classification = await self.classify_8k_filing(
                        ticker=filing.get("ticker", ""),
                        form_type=filing.get("form_type", "8-K"),
                        filed_at=str(filing.get("filed_at", "")),
                        description=filing.get("description", ""),
                        items_reported=filing.get("items_reported", ""),
                        content=filing.get("content_summary", ""),
                    )
                    async with lock:
                        results[acc_no] = classification
                except Exception as exc:
                    msg = f"8-K classification failed for {acc_no}: {exc}"
                    logger.warning(
                        "eightk_classification_failed",
                        accession_no=acc_no,
                        error=str(exc),
                    )
                    async with lock:
                        errors.append(msg)

        tasks = [asyncio.create_task(_classify_one(f)) for f in filings]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            "eightk_classification_batch_complete",
            classified=len(results),
            failed=len(errors),
            total=len(filings),
        )
        return results

    async def classify_batch(
        self,
        articles: list[dict],
    ) -> dict[str, ArticleClassification]:
        """Classify a batch of articles with concurrency control.

        Args:
            articles: List of dicts with at least 'article_id', 'title',
                      'summary', 'published_at' keys.

        Returns:
            Mapping of article_id -> ArticleClassification.
        """
        semaphore = asyncio.Semaphore(self._concurrency)
        results: dict[str, ArticleClassification] = {}
        lock = asyncio.Lock()
        errors: list[str] = []

        async def _classify_one(article: dict) -> None:
            article_id = article["article_id"]
            async with semaphore:
                try:
                    classification = await self.classify_article(
                        title=article.get("title", ""),
                        summary=article.get("summary", ""),
                        published_at=str(article.get("published_at", "")),
                    )
                    async with lock:
                        results[article_id] = classification
                except Exception as exc:
                    msg = f"Classification failed for {article_id}: {exc}"
                    logger.warning(
                        "news_classification_failed",
                        article_id=article_id,
                        error=str(exc),
                    )
                    async with lock:
                        errors.append(msg)

        tasks = [asyncio.create_task(_classify_one(a)) for a in articles]
        await asyncio.gather(*tasks, return_exceptions=True)

        logger.info(
            "news_classification_batch_complete",
            classified=len(results),
            failed=len(errors),
            total=len(articles),
        )
        return results
