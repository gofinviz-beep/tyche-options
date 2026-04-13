"""Tests for the EdgarClient."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from tyche.exceptions import EdgarAPIError
from tyche.market_data.edgar import EdgarClient, EdgarFiling, _strip_html


class TestEdgarClientInit:
    def test_requires_email(self):
        with pytest.raises(ValueError, match="User-Agent email"):
            EdgarClient(user_agent_email="")

    def test_accepts_valid_email(self):
        client = EdgarClient(user_agent_email="test@example.com")
        assert client._user_agent == "Tyche/1.0 (test@example.com)"


class TestCIKResolution:
    @pytest.fixture
    def client(self):
        return EdgarClient(user_agent_email="test@example.com")

    @pytest.mark.asyncio
    async def test_resolve_cik_found(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "0": {"cik_str": "320193", "ticker": "AAPL"},
            "1": {"cik_str": "789019", "ticker": "MSFT"},
        }

        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_resp):
            cik = await client.resolve_cik("AAPL")

        assert cik == "0000320193"

    @pytest.mark.asyncio
    async def test_resolve_cik_not_found(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "0": {"cik_str": "320193", "ticker": "AAPL"},
        }

        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_resp):
            cik = await client.resolve_cik("ZZZZ")

        assert cik is None

    @pytest.mark.asyncio
    async def test_resolve_ciks_batch(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "0": {"cik_str": "320193", "ticker": "AAPL"},
            "1": {"cik_str": "789019", "ticker": "MSFT"},
        }

        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_resp):
            result = await client.resolve_ciks(["AAPL", "MSFT", "ZZZZ"])

        assert "AAPL" in result
        assert "MSFT" in result
        assert "ZZZZ" not in result

    @pytest.mark.asyncio
    async def test_cik_map_cached(self, client):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "0": {"cik_str": "320193", "ticker": "AAPL"},
        }

        with patch.object(client, "_get", new_callable=AsyncMock, return_value=mock_resp) as mock_get:
            await client.resolve_cik("AAPL")
            await client.resolve_cik("AAPL")

        assert mock_get.call_count == 1


class TestGetRecentFilings:
    @pytest.fixture
    def client(self):
        return EdgarClient(user_agent_email="test@example.com")

    @pytest.mark.asyncio
    async def test_no_cik_returns_empty(self, client):
        with patch.object(client, "resolve_cik", new_callable=AsyncMock, return_value=None):
            filings = await client.get_recent_filings("ZZZZ", form_types=["8-K"])
        assert filings == []

    @pytest.mark.asyncio
    async def test_filters_by_form_type_and_date(self, client):
        today = date.today().isoformat()
        old_date = (date.today() - timedelta(days=60)).isoformat()

        submissions_data = {
            "filings": {
                "recent": {
                    "accessionNumber": ["001", "002", "003"],
                    "form": ["8-K", "10-Q", "8-K"],
                    "filingDate": [today, today, old_date],
                    "primaryDocument": ["doc1.htm", "doc2.htm", "doc3.htm"],
                    "primaryDocDescription": ["desc1", "desc2", "desc3"],
                }
            }
        }

        mock_subs_resp = MagicMock()
        mock_subs_resp.json.return_value = submissions_data

        with (
            patch.object(client, "resolve_cik", new_callable=AsyncMock, return_value="0000320193"),
            patch.object(client, "get_submissions", new_callable=AsyncMock, return_value=submissions_data),
        ):
            filings = await client.get_recent_filings(
                "AAPL", form_types=["8-K"], days_back=30
            )

        assert len(filings) == 1
        assert filings[0].accession_no == "001"
        assert filings[0].form_type == "8-K"

    @pytest.mark.asyncio
    async def test_api_error_returns_empty(self, client):
        with (
            patch.object(client, "resolve_cik", new_callable=AsyncMock, return_value="0000320193"),
            patch.object(client, "get_submissions", new_callable=AsyncMock, side_effect=EdgarAPIError(500, "fail")),
        ):
            filings = await client.get_recent_filings("AAPL", form_types=["8-K"])
        assert filings == []


class TestGetFilingContent:
    @pytest.fixture
    def client(self):
        return EdgarClient(user_agent_email="test@example.com")

    @pytest.mark.asyncio
    async def test_strips_html_for_8k(self, client):
        filing = EdgarFiling(
            accession_no="001",
            form_type="8-K",
            filed_at="2026-04-10",
            primary_doc="https://sec.gov/doc.htm",
            description="Test",
            cik="001",
            ticker="AAPL",
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html><body><p>Important filing</p></body></html>"

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            content = await client.get_filing_content(filing, max_chars=100)

        assert "Important filing" in content.content
        assert "<html>" not in content.content

    @pytest.mark.asyncio
    async def test_returns_raw_xml_for_form4(self, client):
        filing = EdgarFiling(
            accession_no="002",
            form_type="4",
            filed_at="2026-04-10",
            primary_doc="https://sec.gov/form4.xml",
            description="Form 4",
            cik="001",
            ticker="AAPL",
        )

        xml_content = "<ownershipDocument><issuer>AAPL</issuer></ownershipDocument>"
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = xml_content

        with patch("httpx.AsyncClient.get", new_callable=AsyncMock, return_value=mock_resp):
            content = await client.get_filing_content(filing)

        assert content.content == xml_content


class TestStripHtml:
    def test_strips_tags(self):
        assert "hello world" in _strip_html("<p>hello</p> <b>world</b>")

    def test_collapses_whitespace(self):
        result = _strip_html("<p>  lots    of   space  </p>")
        assert "  " not in result

    def test_empty_string(self):
        assert _strip_html("") == ""
