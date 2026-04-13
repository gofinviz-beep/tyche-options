"""EDGAR pipeline orchestrator — ingestion, classification, signal rebuild.

Single entry point for both the scheduled job and the manual API trigger.
Wires together EdgarIngestor, NewsClassifier (8-K mode), and filing signal builder.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

import structlog

from tyche.config import TycheSettings, get_settings

logger = structlog.get_logger()

_run_lock = asyncio.Lock()


@dataclass
class EdgarPipelineResult:
    """Summary of a full EDGAR pipeline run."""

    tickers_resolved: int = 0
    tickers_failed_cik: int = 0
    eightk_fetched: int = 0
    eightk_persisted: int = 0
    form4_fetched: int = 0
    insider_tx_persisted: int = 0
    eightk_classified: int = 0
    signals_rebuilt: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


async def run_edgar_pipeline(
    settings: TycheSettings | None = None,
) -> EdgarPipelineResult:
    """Execute one full EDGAR pipeline cycle.

    1. Build ticker universe from OHLCV store (market cap filtered)
    2. Ingest 8-K and Form 4 filings from SEC EDGAR
    3. Classify unclassified 8-K filings with Gemini
    4. Rebuild per-ticker filing signals
    """
    if settings is None:
        settings = get_settings()

    if _run_lock.locked():
        logger.info("edgar_pipeline_skipped", reason="already_running")
        return EdgarPipelineResult(errors=["Pipeline already running"])

    async with _run_lock:
        return await _run_edgar_pipeline_locked(settings)


async def _run_edgar_pipeline_locked(settings: TycheSettings) -> EdgarPipelineResult:
    result = EdgarPipelineResult()

    try:
        tickers = _get_universe(settings)
        if not tickers:
            logger.warning("edgar_pipeline_no_tickers")
            return result

        edgar_client = _get_edgar_client(settings)
        if edgar_client is None:
            logger.warning("edgar_pipeline_no_client", reason="missing edgar_user_agent_email")
            result.errors.append("edgar_user_agent_email not configured")
            return result

        filing_store = _get_filing_store(settings)
        insider_store = _get_insider_store(settings)

        from tyche.market_data.edgar_ingestor import EdgarIngestor

        ingestor = EdgarIngestor(
            client=edgar_client,
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=tickers,
            lookback_days=settings.edgar_lookback_days,
        )

        ingest_result = await ingestor.ingest()
        result.tickers_resolved = ingest_result.tickers_resolved
        result.tickers_failed_cik = ingest_result.tickers_failed_cik
        result.eightk_fetched = ingest_result.eightk_fetched
        result.eightk_persisted = ingest_result.eightk_persisted
        result.form4_fetched = ingest_result.form4_fetched
        result.insider_tx_persisted = ingest_result.insider_tx_persisted
        result.errors.extend(ingest_result.errors)
        result.duration_ms = ingest_result.duration_ms

        classifier = _get_classifier(settings)
        if classifier is not None:
            classified = await _classify_unclassified_8k(
                filing_store, classifier, tickers
            )
            result.eightk_classified = classified
        else:
            logger.info("edgar_pipeline_no_classifier")

        from tyche.market_data.filing_signals import rebuild_filing_signals

        rebuilt = await rebuild_filing_signals(
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=tickers,
            lookback_days=settings.edgar_lookback_days,
        )
        result.signals_rebuilt = rebuilt

    except Exception as exc:
        msg = f"EDGAR pipeline error: {exc}"
        logger.error("edgar_pipeline_failed", error=str(exc), exc_info=True)
        result.errors.append(msg)

    logger.info(
        "edgar_pipeline_complete",
        tickers_resolved=result.tickers_resolved,
        eightk_fetched=result.eightk_fetched,
        eightk_classified=result.eightk_classified,
        form4_fetched=result.form4_fetched,
        insider_tx_persisted=result.insider_tx_persisted,
        signals_rebuilt=result.signals_rebuilt,
        errors=len(result.errors),
    )
    return result


async def _classify_unclassified_8k(filing_store, classifier, tickers: list[str]) -> int:
    """Classify all unclassified 8-K filings across tickers."""
    total_classified = 0

    for ticker in tickers:
        unclassified = filing_store.read_unclassified(ticker)
        if unclassified.empty:
            continue

        filings_to_classify = [
            {
                "accession_no": row["accession_no"],
                "ticker": ticker,
                "form_type": row.get("form_type", "8-K"),
                "filed_at": str(row.get("filed_at", "")),
                "description": row.get("description", ""),
                "items_reported": row.get("items_reported", ""),
                "content_summary": row.get("content_summary", ""),
            }
            for _, row in unclassified.iterrows()
        ]

        try:
            results = await classifier.classify_8k_batch(filings_to_classify)

            classifications: dict[str, dict] = {}
            for acc_no, classification in results.items():
                classifications[acc_no] = {
                    "event_type": classification.event_type,
                    "sentiment": classification.sentiment,
                    "impact_score": classification.impact_score,
                }

            updated = filing_store.bulk_update_classifications(ticker, classifications)
            total_classified += updated

        except Exception as exc:
            logger.warning(
                "eightk_classification_ticker_failed",
                ticker=ticker,
                error=str(exc),
            )

    return total_classified


def _get_universe(settings: TycheSettings) -> list[str]:
    """Build the ticker universe from the OHLCV store."""
    from tyche.market_data.data_store import OHLCVStore, TickerMetaStore

    ohlcv = OHLCVStore(data_dir=settings.data_dir)
    tickers = ohlcv.get_all_tickers()

    meta = TickerMetaStore(data_dir=settings.data_dir)
    tickers = meta.filter_equity_only(tickers)
    min_cap = settings.min_market_cap_millions * 1e6

    if meta.exists and min_cap > 0:
        caps = meta.get_market_caps(tickers)
        passed = []
        for t in tickers:
            cap = caps.get(t)
            if cap is None or cap == 0 or cap >= min_cap:
                passed.append(t)
        tickers = passed

    return tickers


def _get_edgar_client(settings: TycheSettings):
    if not settings.edgar_user_agent_email:
        return None
    from tyche.market_data.edgar import EdgarClient
    return EdgarClient(user_agent_email=settings.edgar_user_agent_email)


def _get_filing_store(settings: TycheSettings):
    from tyche.market_data.filing_store import Filing8KStore
    return Filing8KStore(data_dir=settings.data_dir)


def _get_insider_store(settings: TycheSettings):
    from tyche.market_data.filing_store import InsiderTxStore
    return InsiderTxStore(data_dir=settings.data_dir)


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
