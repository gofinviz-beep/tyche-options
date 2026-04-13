"""SEC EDGAR API client for 8-K filings and Form 4 insider transactions.

Uses the free data.sec.gov APIs (no API key required). SEC mandates a
descriptive User-Agent header and a 10 requests/second rate limit.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

import httpx
import structlog

from tyche.exceptions import EdgarAPIError

logger = structlog.get_logger()

_SEC_BASE = "https://data.sec.gov"
_EFTS_BASE = "https://efts.sec.gov/LATEST"
_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
_MAX_RPS = 10


@dataclass(frozen=True)
class EdgarFiling:
    """Metadata for a single EDGAR filing."""

    accession_no: str
    form_type: str
    filed_at: str
    primary_doc: str
    description: str
    cik: str
    ticker: str


@dataclass(frozen=True)
class FilingContent:
    """Filing metadata plus its text content."""

    filing: EdgarFiling
    content: str


class EdgarClient:
    """Async HTTP client for SEC EDGAR REST APIs.

    Implements the SEC-mandated 10 req/sec rate limit and User-Agent policy.
    """

    def __init__(
        self,
        user_agent_email: str,
        max_rps: int = _MAX_RPS,
        timeout: float = 20.0,
        max_retries: int = 2,
    ) -> None:
        if not user_agent_email:
            raise ValueError("SEC requires a User-Agent email; set edgar_user_agent_email in config")
        self._user_agent = f"Tyche/1.0 ({user_agent_email})"
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval = 1.0 / max(max_rps, 1)
        self._last_request_time: float = 0.0
        self._request_lock = asyncio.Lock()

        self._cik_map: dict[str, str] | None = None

    async def _throttle(self) -> None:
        async with self._request_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _get(self, url: str) -> httpx.Response:
        """Execute a GET request with rate limiting and retries."""
        headers = {
            "User-Agent": self._user_agent,
            "Accept": "application/json",
        }
        last_exc: Exception | None = None

        for attempt in range(self._max_retries):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, headers=headers)

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning("edgar_rate_limited", attempt=attempt + 1, wait=wait)
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    raise EdgarAPIError(resp.status_code, resp.text[:200])

                return resp

            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning("edgar_timeout", url=url[:100], attempt=attempt + 1)
                await asyncio.sleep(2 ** attempt)

            except EdgarAPIError:
                raise

            except Exception as exc:
                last_exc = exc
                logger.warning("edgar_request_error", error=str(exc), attempt=attempt + 1)
                await asyncio.sleep(2 ** attempt)

        raise EdgarAPIError(
            0, f"Request failed after {self._max_retries} retries: {last_exc}"
        )

    # ── CIK Resolution ─────────────────────────────────────────────────

    async def _load_cik_map(self) -> dict[str, str]:
        """Download the SEC ticker-to-CIK mapping (cached after first call)."""
        if self._cik_map is not None:
            return self._cik_map

        resp = await self._get(_TICKERS_URL)
        data = resp.json()

        cik_map: dict[str, str] = {}
        for entry in data.values():
            ticker = str(entry.get("ticker", "")).upper()
            cik = str(entry.get("cik_str", ""))
            if ticker and cik:
                cik_map[ticker] = cik.zfill(10)

        self._cik_map = cik_map
        logger.info("edgar_cik_map_loaded", tickers=len(cik_map))
        return cik_map

    async def resolve_cik(self, ticker: str) -> str | None:
        """Resolve a ticker to its 10-digit CIK, or None if not found."""
        cik_map = await self._load_cik_map()
        return cik_map.get(ticker.upper())

    async def resolve_ciks(self, tickers: list[str]) -> dict[str, str]:
        """Resolve multiple tickers to CIKs. Returns only found tickers."""
        cik_map = await self._load_cik_map()
        result: dict[str, str] = {}
        for t in tickers:
            cik = cik_map.get(t.upper())
            if cik:
                result[t.upper()] = cik
        return result

    # ── Submissions ────────────────────────────────────────────────────

    async def get_submissions(self, cik: str) -> dict:
        """Fetch the full submission history for a CIK."""
        url = f"{_SEC_BASE}/submissions/CIK{cik.zfill(10)}.json"
        resp = await self._get(url)
        return resp.json()

    async def get_recent_filings(
        self,
        ticker: str,
        form_types: list[str],
        days_back: int = 30,
    ) -> list[EdgarFiling]:
        """Get recent filings of specified types for a ticker.

        Args:
            ticker: Stock symbol (e.g. "AAPL").
            form_types: Filing types to include (e.g. ["8-K", "4"]).
            days_back: How far back to look.

        Returns:
            List of EdgarFiling matching the filters.
        """
        cik = await self.resolve_cik(ticker)
        if not cik:
            return []

        try:
            data = await self.get_submissions(cik)
        except EdgarAPIError:
            logger.warning("edgar_submissions_failed", ticker=ticker, cik=cik)
            return []

        cutoff = (date.today() - timedelta(days=days_back)).isoformat()
        form_set = {f.upper() for f in form_types}
        filings: list[EdgarFiling] = []

        recent = data.get("filings", {}).get("recent", {})
        if not recent:
            return []

        accessions = recent.get("accessionNumber", [])
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        primary_docs = recent.get("primaryDocument", [])
        descriptions = recent.get("primaryDocDescription", [])

        for i in range(len(accessions)):
            form = forms[i] if i < len(forms) else ""
            filed = dates[i] if i < len(dates) else ""

            if form.upper() not in form_set:
                continue
            if filed < cutoff:
                continue

            acc = accessions[i]
            acc_path = acc.replace("-", "")
            doc = primary_docs[i] if i < len(primary_docs) else ""
            desc = descriptions[i] if i < len(descriptions) else ""

            filings.append(
                EdgarFiling(
                    accession_no=acc,
                    form_type=form,
                    filed_at=filed,
                    primary_doc=f"{_SEC_BASE}/Archives/edgar/data/{cik}/{acc_path}/{doc}",
                    description=desc,
                    cik=cik,
                    ticker=ticker.upper(),
                )
            )

        logger.debug(
            "edgar_filings_fetched",
            ticker=ticker,
            form_types=form_types,
            count=len(filings),
        )
        return filings

    # ── Filing Content ─────────────────────────────────────────────────

    async def get_filing_content(
        self, filing: EdgarFiling, max_chars: int = 2000
    ) -> FilingContent:
        """Fetch the primary document content of a filing.

        For 8-K filings, returns the first ``max_chars`` characters of the
        HTML/text document (stripped of tags). For Form 4 XML, returns the
        raw XML content.
        """
        headers = {"User-Agent": self._user_agent}
        await self._throttle()

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(filing.primary_doc, headers=headers)

            if resp.status_code >= 400:
                return FilingContent(filing=filing, content="")

            raw = resp.text
            if filing.form_type.upper().startswith("4"):
                return FilingContent(filing=filing, content=raw)

            text = _strip_html(raw)[:max_chars]
            return FilingContent(filing=filing, content=text)

        except Exception as exc:
            logger.warning(
                "edgar_content_fetch_failed",
                url=filing.primary_doc[:80],
                error=str(exc),
            )
            return FilingContent(filing=filing, content="")


_TAG_RE = re.compile(r"<[^>]+>")
_MULTI_SPACE = re.compile(r"\s+")


def _strip_html(html: str) -> str:
    """Naive HTML tag stripping for filing content extraction."""
    text = _TAG_RE.sub(" ", html)
    text = _MULTI_SPACE.sub(" ", text)
    return text.strip()
