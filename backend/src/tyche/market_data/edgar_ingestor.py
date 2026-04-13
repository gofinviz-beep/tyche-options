"""EDGAR filing ingestion service — fetches 8-K and Form 4 from SEC, persists.

Orchestrates CIK resolution, filing fetching, Form 4 XML parsing, and
storage to per-ticker Parquet stores. 8-K classification is handled
separately by the classifier step.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from tyche.market_data.edgar import EdgarClient, EdgarFiling
from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore
from tyche.market_data.form4_parser import parse_form4_xml, transaction_to_dict

logger = structlog.get_logger()


@dataclass
class EdgarIngestResult:
    """Summary of an EDGAR ingestion run."""

    tickers_resolved: int = 0
    tickers_failed_cik: int = 0
    eightk_fetched: int = 0
    eightk_persisted: int = 0
    form4_fetched: int = 0
    insider_tx_persisted: int = 0
    errors: list[str] = field(default_factory=list)
    duration_ms: float = 0.0


class EdgarIngestor:
    """Orchestrates EDGAR 8-K and Form 4 ingestion for a ticker universe."""

    def __init__(
        self,
        client: EdgarClient,
        filing_store: Filing8KStore,
        insider_store: InsiderTxStore,
        tickers: list[str],
        concurrency: int = 5,
        lookback_days: int = 30,
    ) -> None:
        self._client = client
        self._filing_store = filing_store
        self._insider_store = insider_store
        self._tickers = [t.upper() for t in tickers]
        self._semaphore = asyncio.Semaphore(concurrency)
        self._lookback_days = lookback_days

    async def ingest(self) -> EdgarIngestResult:
        """Fetch 8-K and Form 4 filings for all tickers."""
        start = datetime.now(tz=timezone.utc)
        result = EdgarIngestResult()

        cik_map = await self._client.resolve_ciks(self._tickers)
        result.tickers_resolved = len(cik_map)
        result.tickers_failed_cik = len(self._tickers) - len(cik_map)

        if not cik_map:
            logger.warning("edgar_no_ciks_resolved")
            return result

        resolved_tickers = list(cik_map.keys())

        tasks = [
            self._ingest_ticker(ticker, result)
            for ticker in resolved_tickers
        ]
        await asyncio.gather(*tasks, return_exceptions=True)

        elapsed = (datetime.now(tz=timezone.utc) - start).total_seconds() * 1000
        result.duration_ms = round(elapsed, 1)

        logger.info(
            "edgar_ingest_complete",
            tickers=result.tickers_resolved,
            eightk=result.eightk_persisted,
            form4_tx=result.insider_tx_persisted,
            errors=len(result.errors),
            duration_ms=result.duration_ms,
        )
        return result

    async def _ingest_ticker(
        self, ticker: str, result: EdgarIngestResult
    ) -> None:
        async with self._semaphore:
            try:
                await self._ingest_8k(ticker, result)
                await self._ingest_form4(ticker, result)
            except Exception as exc:
                msg = f"{ticker}: {exc}"
                result.errors.append(msg)
                logger.warning("edgar_ticker_error", ticker=ticker, error=str(exc))

    async def _ingest_8k(
        self, ticker: str, result: EdgarIngestResult
    ) -> None:
        filings = await self._client.get_recent_filings(
            ticker, form_types=["8-K", "8-K/A"], days_back=self._lookback_days
        )
        if not filings:
            return

        result.eightk_fetched += len(filings)

        filing_dicts: list[dict] = []
        for f in filings:
            content = await self._client.get_filing_content(f, max_chars=2000)
            filing_dicts.append(
                {
                    "accession_no": f.accession_no,
                    "cik": f.cik,
                    "filed_at": f.filed_at,
                    "form_type": f.form_type,
                    "description": f.description,
                    "filing_url": f.primary_doc,
                    "items_reported": "",
                    "content_summary": content.content[:2000],
                }
            )

        count = self._filing_store.write_filings(ticker, filing_dicts)
        result.eightk_persisted += count

    async def _ingest_form4(
        self, ticker: str, result: EdgarIngestResult
    ) -> None:
        filings = await self._client.get_recent_filings(
            ticker, form_types=["4"], days_back=self._lookback_days
        )
        if not filings:
            return

        result.form4_fetched += len(filings)

        all_tx_dicts: list[dict] = []
        for f in filings:
            content = await self._client.get_filing_content(f)
            if not content.content:
                continue

            transactions = parse_form4_xml(content.content)
            for tx in transactions:
                all_tx_dicts.append(
                    transaction_to_dict(
                        tx,
                        accession_no=f.accession_no,
                        ticker=ticker,
                        cik=f.cik,
                        filed_at=f.filed_at,
                    )
                )

        if all_tx_dicts:
            count = self._insider_store.write_transactions(ticker, all_tx_dicts)
            result.insider_tx_persisted += count
