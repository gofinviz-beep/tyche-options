"""Tests for EdgarIngestor."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from tyche.market_data.edgar import EdgarClient, EdgarFiling, FilingContent
from tyche.market_data.edgar_ingestor import EdgarIngestor
from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore

_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <periodOfReport>2026-04-01</periodOfReport>
    <reportingOwner>
        <reportingOwnerId><rptOwnerName>Test CEO</rptOwnerName></reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>1</isOfficer><officerTitle>CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
            <transactionAmounts>
                <transactionShares><value>5000</value></transactionShares>
                <transactionPricePerShare><value>150.00</value></transactionPricePerShare>
                <transactionAcquiredDisposedCode><value>D</value></transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction><value>45000</value></sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""


def _make_filing(acc: str, form_type: str = "8-K") -> EdgarFiling:
    return EdgarFiling(
        accession_no=acc,
        form_type=form_type,
        filed_at="2026-04-10",
        primary_doc=f"https://sec.gov/{acc}",
        description=f"Test {form_type}",
        cik="0000320193",
        ticker="AAPL",
    )


class TestEdgarIngestor:
    @pytest.fixture
    def client(self):
        c = MagicMock(spec=EdgarClient)
        c.resolve_ciks = AsyncMock(return_value={"AAPL": "0000320193"})
        return c

    @pytest.fixture
    def filing_store(self, tmp_path):
        return Filing8KStore(data_dir=str(tmp_path))

    @pytest.fixture
    def insider_store(self, tmp_path):
        return InsiderTxStore(data_dir=str(tmp_path))

    @pytest.mark.asyncio
    async def test_ingest_8k_filings(self, client, filing_store, insider_store):
        filing = _make_filing("acc1", "8-K")
        client.get_recent_filings = AsyncMock(side_effect=[
            [filing],  # 8-K call
            [],         # Form 4 call
        ])
        client.get_filing_content = AsyncMock(
            return_value=FilingContent(filing=filing, content="Test filing content")
        )

        ingestor = EdgarIngestor(
            client=client,
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=["AAPL"],
        )
        result = await ingestor.ingest()

        assert result.tickers_resolved == 1
        assert result.eightk_fetched == 1
        assert result.eightk_persisted >= 1

    @pytest.mark.asyncio
    async def test_ingest_form4(self, client, filing_store, insider_store):
        form4 = _make_filing("acc2", "4")
        client.get_recent_filings = AsyncMock(side_effect=[
            [],         # 8-K call
            [form4],    # Form 4 call
        ])
        client.get_filing_content = AsyncMock(
            return_value=FilingContent(filing=form4, content=_FORM4_XML)
        )

        ingestor = EdgarIngestor(
            client=client,
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=["AAPL"],
        )
        result = await ingestor.ingest()

        assert result.form4_fetched == 1
        assert result.insider_tx_persisted >= 1

    @pytest.mark.asyncio
    async def test_no_cik_resolution(self, filing_store, insider_store):
        client = MagicMock(spec=EdgarClient)
        client.resolve_ciks = AsyncMock(return_value={})

        ingestor = EdgarIngestor(
            client=client,
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=["ZZZZ"],
        )
        result = await ingestor.ingest()

        assert result.tickers_resolved == 0
        assert result.tickers_failed_cik == 1

    @pytest.mark.asyncio
    async def test_ticker_error_captured(self, client, filing_store, insider_store):
        client.get_recent_filings = AsyncMock(side_effect=Exception("Network error"))

        ingestor = EdgarIngestor(
            client=client,
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=["AAPL"],
        )
        result = await ingestor.ingest()

        assert len(result.errors) >= 1
        assert "AAPL" in result.errors[0]

    @pytest.mark.asyncio
    async def test_empty_form4_content_skipped(self, client, filing_store, insider_store):
        form4 = _make_filing("acc3", "4")
        client.get_recent_filings = AsyncMock(side_effect=[
            [],         # 8-K call
            [form4],    # Form 4 call
        ])
        client.get_filing_content = AsyncMock(
            return_value=FilingContent(filing=form4, content="")
        )

        ingestor = EdgarIngestor(
            client=client,
            filing_store=filing_store,
            insider_store=insider_store,
            tickers=["AAPL"],
        )
        result = await ingestor.ingest()

        assert result.insider_tx_persisted == 0
