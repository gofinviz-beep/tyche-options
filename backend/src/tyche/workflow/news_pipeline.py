"""News pipeline orchestrator — ingestion, classification, signal rebuild.

Single entry point for both the scheduled job and the manual API trigger.
Wires together NewsIngestor, NewsClassifier, and signal builder.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import structlog

from tyche.config import TycheSettings, get_settings

logger = structlog.get_logger()

_run_lock = asyncio.Lock()


@dataclass
class NewsPipelineResult:
    """Summary of a full news pipeline run."""

    polygon_fetched: int = 0
    finnhub_fetched: int = 0
    total_persisted: int = 0
    tickers_updated: int = 0
    articles_classified: int = 0
    signals_rebuilt: int = 0
    errors: list[str] = field(default_factory=list)


async def run_news_pipeline(
    settings: TycheSettings | None = None,
) -> NewsPipelineResult:
    """Execute one full news pipeline cycle.

    1. Build ticker universe from OHLCV store (market cap filtered)
    2. Fetch articles from Polygon + Finnhub
    3. Classify unclassified articles with Gemini
    4. Rebuild per-ticker news signals
    """
    if settings is None:
        settings = get_settings()

    if _run_lock.locked():
        logger.info("news_pipeline_skipped", reason="already_running")
        return NewsPipelineResult(errors=["Pipeline already running"])

    async with _run_lock:
        return await _run_news_pipeline_locked(settings)


async def _run_news_pipeline_locked(settings: TycheSettings) -> NewsPipelineResult:
    result = NewsPipelineResult()

    try:
        tickers = _get_universe(settings)
        if not tickers:
            logger.warning("news_pipeline_no_tickers")
            return result

        store = _get_store(settings)
        polygon = _get_polygon(settings)
        finnhub = _get_finnhub(settings)

        from tyche.market_data.news_ingestor import NewsIngestor

        ingestor = NewsIngestor(
            polygon=polygon,
            finnhub=finnhub,
            store=store,
            tickers=tickers,
        )

        from tyche.market_data.ingest_dates import resolve_ingest_end_date

        ingest_end = resolve_ingest_end_date(
            settings.ingest_window, job_name="ingest-news"
        )
        since = datetime.now(tz=timezone.utc) - timedelta(
            hours=settings.news_lookback_hours
        )
        ingest_result = await ingestor.ingest(since=since, to_date=ingest_end)

        result.polygon_fetched = ingest_result.polygon_fetched
        result.finnhub_fetched = ingest_result.finnhub_fetched
        result.total_persisted = ingest_result.total_persisted
        result.tickers_updated = ingest_result.tickers_updated
        result.errors.extend(ingest_result.errors)

        classifier = _get_classifier(settings)
        if classifier is not None:
            catalyst_store = None
            try:
                from tyche.market_data.catalyst_store import CatalystSignalStore

                catalyst_store = CatalystSignalStore(data_dir=settings.data_dir)
            except Exception:
                logger.warning("catalyst_store_init_failed", exc_info=True)
            classified = await _classify_unclassified(
                store, classifier, tickers, catalyst_store=catalyst_store
            )
            result.articles_classified = classified
        else:
            logger.info("news_pipeline_no_classifier")

        if settings.data_backend == "gcs":
            from tyche.ops.intelligence_export import export_news_signals_from_parquet

            export_summary = await export_news_signals_from_parquet(
                store=store,
                tickers=tickers,
                settings=settings,
            )
            result.signals_rebuilt = int(export_summary.get("rows", 0))
        else:
            from tyche.market_data.news_signals import rebuild_signals

            rebuilt = await rebuild_signals(
                store=store,
                tickers=tickers,
                lookback_hours=settings.news_lookback_hours,
            )
            result.signals_rebuilt = rebuilt

    except Exception as exc:
        msg = f"News pipeline error: {exc}"
        logger.error("news_pipeline_failed", error=str(exc), exc_info=True)
        result.errors.append(msg)

    logger.info(
        "news_pipeline_complete",
        polygon_fetched=result.polygon_fetched,
        finnhub_fetched=result.finnhub_fetched,
        total_persisted=result.total_persisted,
        articles_classified=result.articles_classified,
        signals_rebuilt=result.signals_rebuilt,
        errors=len(result.errors),
    )
    return result


async def _classify_unclassified(
    store, classifier, tickers: list[str], catalyst_store=None
) -> int:
    """Classify all unclassified articles across tickers.

    When ``catalyst_store`` is provided, demand-catalyst / policy tags from the
    classifier are persisted to the CatalystSignalStore (D-CAT / D-POL).
    """
    import pandas as pd

    from tyche.market_data.catalyst_store import records_from_classification

    total_classified = 0

    for ticker in tickers:
        unclassified = store.read_unclassified(ticker)
        if unclassified.empty:
            continue

        articles_to_classify = [
            {
                "article_id": row["article_id"],
                "title": row["title"],
                "summary": row.get("summary", ""),
                "published_at": str(row["published_at"]),
            }
            for _, row in unclassified.iterrows()
        ]
        pub_dates = {
            row["article_id"]: pd.to_datetime(row["published_at"]).date()
            for _, row in unclassified.iterrows()
        }

        try:
            results = await classifier.classify_batch(articles_to_classify)

            classifications: dict[str, dict] = {}
            catalyst_rows: list[dict] = []
            for article_id, classification in results.items():
                classifications[article_id] = {
                    "event_type": classification.event_type,
                    "sentiment": classification.sentiment,
                    "impact_score": classification.impact_score,
                    "relevance": _extract_relevance(classification, ticker),
                }
                if catalyst_store is not None:
                    ev_date = pub_dates.get(article_id)
                    if ev_date is not None:
                        catalyst_rows.extend(
                            records_from_classification(
                                ticker=ticker,
                                event_date=ev_date,
                                demand_catalyst=getattr(
                                    classification, "demand_catalyst", "none"
                                ),
                                policy_tag=getattr(classification, "policy_tag", "none"),
                                impact_score=classification.impact_score,
                                source="news",
                                ref_id=str(article_id),
                            )
                        )

            updated = store.bulk_update_classifications(ticker, classifications)
            total_classified += updated

            if catalyst_store is not None and catalyst_rows:
                try:
                    catalyst_store.write_records(ticker, pd.DataFrame(catalyst_rows))
                except Exception:
                    logger.warning("catalyst_persist_failed", ticker=ticker)

        except Exception as exc:
            logger.warning(
                "news_classification_ticker_failed",
                ticker=ticker,
                error=str(exc),
            )

    return total_classified


def _extract_relevance(classification, ticker: str) -> str:
    """Extract relevance for a specific ticker from the classification."""
    for mention in classification.tickers_mentioned:
        if mention.ticker.upper() == ticker.upper():
            return mention.relevance
    return "passing"


def _get_universe(settings: TycheSettings) -> list[str]:
    """Build the ticker universe from the OHLCV store."""
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore

    ohlcv = OHLCVStore(data_dir=settings.data_dir)
    tickers = ohlcv.get_all_tickers()

    meta = TickerMetaStore(data_dir=settings.data_dir)
    tickers = meta.filter_equity_only(tickers)
    min_cap = settings.min_market_cap_millions * 1e6
    tickers = _filter_by_market_cap(tickers, meta, min_cap)

    return tickers


def _filter_by_market_cap(
    tickers: list[str],
    meta_store,
    min_market_cap: float,
) -> list[str]:
    """Filter tickers by market cap. Tickers with no metadata pass through."""
    if meta_store is None or not meta_store.exists or min_market_cap <= 0:
        return tickers

    caps = meta_store.get_market_caps(tickers)
    passed = []
    for t in tickers:
        cap = caps.get(t)
        if cap is None or cap == 0:
            passed.append(t)
        elif cap >= min_market_cap:
            passed.append(t)
    return passed


def _get_store(settings: TycheSettings):
    from tyche.market_data.news_store import NewsArticleStore
    return NewsArticleStore(data_dir=settings.data_dir)


def _get_polygon(settings: TycheSettings):
    if not settings.polygon_api_key:
        return None
    from tyche.market_data.polygon import PolygonClient
    return PolygonClient(
        api_key=settings.polygon_api_key,
        base_url=settings.polygon_base_url,
        rate_limit_rpm=settings.polygon_rate_limit_rpm,
    )


def _get_finnhub(settings: TycheSettings):
    if not settings.finnhub_api_key or not settings.news_finnhub_enabled:
        return None
    from tyche.market_data.finnhub import FinnhubClient
    return FinnhubClient(api_key=settings.finnhub_api_key)


def _get_classifier(settings: TycheSettings):
    if not settings.gemini_api_key:
        return None
    from tyche.analysis.client import GeminiClient
    from tyche.analysis.news_classifier import NewsClassifier
    gemini = GeminiClient(
        api_key=settings.gemini_api_key,
        model_fast=settings.gemini_model_fast,
        model_deep=settings.gemini_model_deep,
    )
    return NewsClassifier(
        gemini=gemini,
        classify_model=settings.gemini_model_classify,
        workers=settings.news_classify_workers,
        rpm=settings.news_classify_rpm,
    )
