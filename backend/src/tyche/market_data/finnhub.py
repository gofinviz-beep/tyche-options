"""Finnhub API client for company news.

Lightweight async client using httpx. Free tier allows 60 calls/min.
Only fetches company news — no other Finnhub endpoints are used.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from datetime import date, datetime

import httpx
import structlog

from tyche.exceptions import FinnhubAPIError

logger = structlog.get_logger()

_BASE_URL = "https://finnhub.io/api/v1"
_FREE_TIER_RPM = 60


@dataclass(frozen=True)
class FinnhubArticle:
    """Single news article from Finnhub /company-news."""

    id: str
    headline: str
    source: str
    url: str
    summary: str
    datetime_ts: int
    related: str
    category: str


class FinnhubClient:
    """Async HTTP client for Finnhub REST API.

    Implements rate limiting for the free tier (60 RPM).
    """

    def __init__(
        self,
        api_key: str,
        rate_limit_rpm: int = _FREE_TIER_RPM,
        timeout: float = 15.0,
        max_retries: int = 2,
    ) -> None:
        self._api_key = api_key
        self._timeout = timeout
        self._max_retries = max_retries
        self._min_interval = 60.0 / max(rate_limit_rpm, 1)
        self._last_request_time: float = 0.0
        self._request_lock = asyncio.Lock()

    async def _throttle(self) -> None:
        async with self._request_lock:
            now = time.monotonic()
            elapsed = now - self._last_request_time
            if elapsed < self._min_interval:
                await asyncio.sleep(self._min_interval - elapsed)
            self._last_request_time = time.monotonic()

    async def _request(
        self, path: str, params: dict | None = None
    ) -> list | dict:
        params = params or {}
        params["token"] = self._api_key
        url = f"{_BASE_URL}{path}"

        last_exc: Exception | None = None
        for attempt in range(self._max_retries):
            await self._throttle()
            try:
                async with httpx.AsyncClient(timeout=self._timeout) as client:
                    resp = await client.get(url, params=params)

                if resp.status_code == 429:
                    wait = 2 ** (attempt + 1)
                    logger.warning(
                        "finnhub_rate_limited", attempt=attempt + 1, wait_seconds=wait
                    )
                    await asyncio.sleep(wait)
                    continue

                if resp.status_code >= 400:
                    raise FinnhubAPIError(resp.status_code, resp.text[:200])

                return resp.json()

            except httpx.TimeoutException as exc:
                last_exc = exc
                logger.warning(
                    "finnhub_timeout", path=path, attempt=attempt + 1
                )
                await asyncio.sleep(2 ** attempt)

            except FinnhubAPIError:
                raise

            except Exception as exc:
                last_exc = exc
                logger.warning(
                    "finnhub_request_error",
                    path=path,
                    error=str(exc),
                    attempt=attempt + 1,
                )
                await asyncio.sleep(2 ** attempt)

        raise FinnhubAPIError(
            0, f"Request failed after {self._max_retries} retries: {last_exc}"
        )

    async def get_company_news(
        self,
        ticker: str,
        from_date: date,
        to_date: date,
    ) -> list[FinnhubArticle]:
        """Fetch company news for a ticker within a date range.

        Args:
            ticker: Stock symbol (e.g. "AAPL").
            from_date: Start date (inclusive).
            to_date: End date (inclusive).

        Returns:
            List of FinnhubArticle dataclasses.
        """
        params = {
            "symbol": ticker.upper(),
            "from": from_date.strftime("%Y-%m-%d"),
            "to": to_date.strftime("%Y-%m-%d"),
        }

        data = await self._request("/company-news", params=params)
        if not isinstance(data, list):
            return []

        articles: list[FinnhubArticle] = []
        for item in data:
            articles.append(
                FinnhubArticle(
                    id=str(item.get("id", "")),
                    headline=item.get("headline", ""),
                    source=item.get("source", ""),
                    url=item.get("url", ""),
                    summary=item.get("summary", "")[:500],
                    datetime_ts=item.get("datetime", 0),
                    related=item.get("related", ""),
                    category=item.get("category", ""),
                )
            )

        logger.debug(
            "finnhub_news_fetched",
            ticker=ticker,
            count=len(articles),
            from_date=str(from_date),
            to_date=str(to_date),
        )
        return articles

    # ── Estimates / Revisions / Surprises ──────────────────────────────
    #
    # All methods below return tidy rows ready for ``EstimatesStore``:
    #   {"snapshot_date": date, "metric": str, "period": str, "value": float}
    # Each degrades to ``[]`` on any API error so a missing/limited Finnhub
    # tier never breaks ingestion.

    async def _safe_get(self, path: str, params: dict) -> list | dict | None:
        try:
            return await self._request(path, params=params)
        except FinnhubAPIError as exc:
            logger.warning("finnhub_endpoint_unavailable", path=path, error=str(exc))
            return None

    @staticmethod
    def _num(value: object) -> float | None:
        try:
            if value is None:
                return None
            return float(value)
        except (TypeError, ValueError):
            return None

    async def get_recommendation_trends(self, ticker: str) -> list[dict]:
        """Analyst recommendation counts over time (monthly trend)."""
        data = await self._safe_get("/stock/recommendation", {"symbol": ticker.upper()})
        if not isinstance(data, list):
            return []

        rows: list[dict] = []
        key_map = {
            "strongBuy": "rec_strong_buy",
            "buy": "rec_buy",
            "hold": "rec_hold",
            "sell": "rec_sell",
            "strongSell": "rec_strong_sell",
        }
        for item in data:
            period = str(item.get("period", "")).strip()
            if not period:
                continue
            try:
                snap = datetime.strptime(period[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            for src, metric in key_map.items():
                val = self._num(item.get(src))
                if val is not None:
                    rows.append(
                        {"snapshot_date": snap, "metric": metric, "period": "", "value": val}
                    )
        return rows

    async def get_earnings_surprises(self, ticker: str, limit: int = 12) -> list[dict]:
        """Historical EPS actual vs. estimate surprises."""
        data = await self._safe_get(
            "/stock/earnings", {"symbol": ticker.upper(), "limit": limit}
        )
        if not isinstance(data, list):
            return []

        rows: list[dict] = []
        for item in data:
            period = str(item.get("period", "")).strip()
            if not period:
                continue
            try:
                snap = datetime.strptime(period[:10], "%Y-%m-%d").date()
            except ValueError:
                continue
            for src, metric in (
                ("actual", "eps_actual"),
                ("estimate", "eps_estimate"),
                ("surprise", "eps_surprise"),
                ("surprisePercent", "eps_surprise_pct"),
            ):
                val = self._num(item.get(src))
                if val is not None:
                    rows.append(
                        {"snapshot_date": snap, "metric": metric, "period": period, "value": val}
                    )
        return rows

    async def get_estimates(
        self,
        ticker: str,
        as_of: date | None = None,
        freq: str = "quarterly",
    ) -> list[dict]:
        """Forward EPS + revenue consensus estimates (current snapshot).

        Snapshotting daily builds an estimate-revision time series in the
        store (diff the same ``(metric, period)`` across snapshot dates).
        """
        snap = as_of or date.today()
        rows: list[dict] = []

        eps = await self._safe_get(
            "/stock/eps-estimate", {"symbol": ticker.upper(), "freq": freq}
        )
        if isinstance(eps, dict):
            for item in eps.get("data", []) or []:
                period = str(item.get("period", "")).strip()
                if not period:
                    continue
                for src, metric in (
                    ("epsAvg", "eps_est_avg"),
                    ("epsHigh", "eps_est_high"),
                    ("epsLow", "eps_est_low"),
                    ("numberAnalysts", "eps_est_count"),
                ):
                    val = self._num(item.get(src))
                    if val is not None:
                        rows.append(
                            {"snapshot_date": snap, "metric": metric, "period": period, "value": val}
                        )

        rev = await self._safe_get(
            "/stock/revenue-estimate", {"symbol": ticker.upper(), "freq": freq}
        )
        if isinstance(rev, dict):
            for item in rev.get("data", []) or []:
                period = str(item.get("period", "")).strip()
                if not period:
                    continue
                for src, metric in (
                    ("revenueAvg", "rev_est_avg"),
                    ("revenueHigh", "rev_est_high"),
                    ("revenueLow", "rev_est_low"),
                    ("numberAnalysts", "rev_est_count"),
                ):
                    val = self._num(item.get(src))
                    if val is not None:
                        rows.append(
                            {"snapshot_date": snap, "metric": metric, "period": period, "value": val}
                        )
        return rows

    async def get_price_target(self, ticker: str, as_of: date | None = None) -> list[dict]:
        """Consensus price target (current snapshot)."""
        snap = as_of or date.today()
        data = await self._safe_get("/stock/price-target", {"symbol": ticker.upper()})
        if not isinstance(data, dict):
            return []
        rows: list[dict] = []
        for src, metric in (
            ("targetMean", "price_target_mean"),
            ("targetHigh", "price_target_high"),
            ("targetLow", "price_target_low"),
            ("targetMedian", "price_target_median"),
        ):
            val = self._num(data.get(src))
            if val is not None:
                rows.append({"snapshot_date": snap, "metric": metric, "period": "", "value": val})
        return rows

    async def get_basic_financials(self, ticker: str, as_of: date | None = None) -> list[dict]:
        """Selected TTM growth/margin ratios from ``/stock/metric``."""
        snap = as_of or date.today()
        data = await self._safe_get(
            "/stock/metric", {"symbol": ticker.upper(), "metric": "all"}
        )
        if not isinstance(data, dict):
            return []
        metric_block = data.get("metric", {}) or {}
        rows: list[dict] = []
        wanted = {
            "revenueGrowthTTMYoy": "fin_revenue_growth_ttm_yoy",
            "revenueGrowthQuarterlyYoy": "fin_revenue_growth_q_yoy",
            "epsGrowthTTMYoy": "fin_eps_growth_ttm_yoy",
            "grossMarginTTM": "fin_gross_margin_ttm",
            "operatingMarginTTM": "fin_operating_margin_ttm",
            "netProfitMarginTTM": "fin_net_margin_ttm",
            "roeTTM": "fin_roe_ttm",
            "currentRatioQuarterly": "fin_current_ratio",
            "totalDebt/totalEquityQuarterly": "fin_debt_to_equity",
        }
        for src, metric in wanted.items():
            val = self._num(metric_block.get(src))
            if val is not None:
                rows.append({"snapshot_date": snap, "metric": metric, "period": "", "value": val})
        return rows

    # Standardized ``/stock/financials`` reports dollar amounts in millions;
    # EPS is per-share; share counts are in millions.
    _STD_MILLIONS = 1_000_000.0

    _STD_IC_FIELDS: tuple[tuple[str, str, bool], ...] = (
        ("revenue", "revenue", True),
        ("gross_profit", "grossIncome", True),
        ("operating_income", "ebit", True),
        ("net_income", "netIncome", True),
        ("eps_diluted", "dilutedEPS", False),
        ("shares_diluted", "dilutedAverageSharesOutstanding", True),
    )
    _STD_CF_FIELDS: tuple[tuple[str, str, bool], ...] = (
        ("operating_cash_flow", "netOperatingCashFlow", True),
        ("operating_cash_flow", "operatingCashFlow", True),
        ("capex", "capitalExpenditure", True),
    )
    _STD_BS_FIELDS: tuple[tuple[str, str, bool], ...] = (
        ("total_assets", "totalAssets", True),
        ("total_equity", "totalEquity", True),
        ("total_equity", "totalShareholderEquity", True),
        ("total_debt", "totalDebt", True),
        ("total_debt", "shortLongTermDebtTotal", True),
        ("cash_and_equivalents", "cashShortTermInvestments", True),
        ("cash_and_equivalents", "cashEquivalents", True),
    )

    @staticmethod
    def _std_val(item: dict, src_key: str, *, in_millions: bool) -> float | None:
        val = FinnhubClient._num(item.get(src_key))
        if val is None:
            return None
        return float(val) * FinnhubClient._STD_MILLIONS if in_millions else float(val)

    @classmethod
    def _apply_std_fields(
        cls,
        row: dict,
        item: dict,
        mapping: tuple[tuple[str, str, bool], ...],
    ) -> None:
        for dst, src, in_millions in mapping:
            if row.get(dst) is not None:
                continue
            val = cls._std_val(item, src, in_millions=in_millions)
            if val is not None:
                row[dst] = val

    async def get_standardized_financials(
        self,
        ticker: str,
        *,
        freq: str = "quarterly",
        limit: int = 20,
        preliminary: bool = True,
    ) -> list[dict]:
        """Standardized BS/IC/CF via ``/stock/financials`` → ``FundamentalsStore`` rows.

        Merges income, balance, and cash-flow statements by ``period`` end date.
        Dollar amounts and share counts are scaled from Finnhub's millions to
        absolute units. ``filing_date`` is set to ``period_end`` when Finnhub
        does not supply a filed date (conservative, leakage-safe default).
        """
        params_base: dict = {"symbol": ticker.upper(), "freq": freq}
        if preliminary:
            params_base["preliminary"] = "true"

        ic_data, bs_data, cf_data = await asyncio.gather(
            self._safe_get("/stock/financials", {**params_base, "statement": "ic"}),
            self._safe_get("/stock/financials", {**params_base, "statement": "bs"}),
            self._safe_get("/stock/financials", {**params_base, "statement": "cf"}),
        )

        by_period: dict[str, dict] = {}

        def _ingest(block: list | dict | None, mapping: tuple[tuple[str, str, bool], ...]) -> None:
            if not isinstance(block, dict):
                return
            items = block.get("financials") or []
            if not isinstance(items, list):
                return
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                period_raw = item.get("period")
                if not period_raw:
                    continue
                period_end = str(period_raw).split(" ")[0].split("T")[0]
                row = by_period.setdefault(
                    period_end,
                    {
                        "period_end": period_end,
                        "filing_date": period_end,
                        "fiscal_year": item.get("year"),
                        "fiscal_period": "",
                        "timeframe": freq if freq in ("quarterly", "annual", "ttm") else "quarterly",
                    },
                )
                q = item.get("quarter")
                if q not in (0, None, ""):
                    row["fiscal_period"] = f"Q{q}"
                elif not row.get("fiscal_period"):
                    row["fiscal_period"] = "FY" if freq == "annual" else ""
                if row.get("fiscal_year") in (None, 0, ""):
                    row["fiscal_year"] = item.get("year")
                self._apply_std_fields(row, item, mapping)

        _ingest(ic_data if isinstance(ic_data, dict) else None, self._STD_IC_FIELDS)
        _ingest(bs_data if isinstance(bs_data, dict) else None, self._STD_BS_FIELDS)
        _ingest(cf_data if isinstance(cf_data, dict) else None, self._STD_CF_FIELDS)

        rows: list[dict] = []
        for period_end in sorted(by_period.keys(), reverse=True)[:limit]:
            row = by_period[period_end]
            ocf = row.get("operating_cash_flow")
            capex = row.get("capex")
            if ocf is not None and capex is not None:
                row["free_cash_flow"] = ocf - abs(capex)
            rows.append(row)

        logger.debug(
            "finnhub_standardized_financials_fetched",
            ticker=ticker,
            freq=freq,
            periods=len(rows),
        )
        return rows

    # ── Financial Statements (Fundamental-1) ────────────────────────────
    #
    # ``/stock/financials-reported`` returns as-reported (SEC-tagged) line
    # items. Each period carries ``endDate`` (period end), ``filedDate`` (when
    # it became public — leakage-safe), ``year``/``quarter``/``form`` and a
    # ``report`` block of ``ic`` (income), ``bs`` (balance), ``cf`` (cash flow)
    # arrays of ``{concept, label, value, unit}``. We map the standard GAAP
    # concepts into the ``FundamentalsStore`` row shape (revenue, gross profit,
    # net income, EPS, FCF, shares, balance-sheet items).

    # Candidate GAAP concept tags per field (matched case-insensitively, first
    # hit wins). As-reported tags vary by filer, so we list common variants.
    _STATEMENT_CONCEPTS: dict[str, tuple[str, ...]] = {
        "revenue": (
            "RevenueFromContractWithCustomerExcludingAssessedTax",
            "RevenueFromContractWithCustomerIncludingAssessedTax",
            "Revenues",
            "SalesRevenueNet",
            "SalesRevenueGoodsNet",
            # Banks / insurers often report total revenue under custom extensions.
            "TotalRevenues",
            "TotalRevenue",
            # Net interest income is a reasonable revenue proxy when no total line exists.
            "InterestAndDividendIncomeOperating",
        ),
        "gross_profit": ("GrossProfit",),
        "operating_income": ("OperatingIncomeLoss",),
        "net_income": (
            "NetIncomeLoss",
            "ProfitLoss",
            "NetIncomeLossAvailableToCommonStockholdersBasic",
        ),
        "eps_diluted": (
            "EarningsPerShareDiluted",
            "EarningsPerShareBasicAndDiluted",
            "EarningsPerShareBasic",
        ),
        "shares_diluted": (
            "WeightedAverageNumberOfDilutedSharesOutstanding",
            "WeightedAverageNumberOfShareOutstandingBasicAndDiluted",
            "WeightedAverageNumberOfSharesOutstandingBasic",
        ),
        "operating_cash_flow": (
            "NetCashProvidedByUsedInOperatingActivities",
            "NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
        ),
        "capex": (
            "PaymentsToAcquirePropertyPlantAndEquipment",
            "PaymentsToAcquireProductiveAssets",
        ),
        "cash_and_equivalents": (
            "CashAndCashEquivalentsAtCarryingValue",
            "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
        ),
        "total_debt": ("LongTermDebtNoncurrent", "LongTermDebt", "DebtCurrent"),
        "total_assets": ("Assets",),
        "total_equity": (
            "StockholdersEquity",
            "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
        ),
    }

    @staticmethod
    def _concept_value(section: list | None, candidates: tuple[str, ...]) -> float | None:
        """First matching concept value from an as-reported statement section."""
        if not isinstance(section, list):
            return None
        wanted = [c.lower() for c in candidates]
        # Per-candidate: exact (suffix) match, then substring — so higher-priority
        # tags (e.g. TotalRevenues) win before lower-priority exact matches
        # (e.g. bank net-interest lines) on later candidates.
        for want in wanted:
            for item in section:
                concept = str(item.get("concept", "")).lower()
                tail = concept.split("_")[-1] if "_" in concept else concept
                if tail == want or concept == want:
                    val = item.get("value")
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        return None
            for item in section:
                concept = str(item.get("concept", "")).lower()
                if want in concept:
                    val = item.get("value")
                    try:
                        return float(val)
                    except (TypeError, ValueError):
                        return None
        return None

    async def get_financials_statements(
        self,
        ticker: str,
        freq: str = "quarterly",
        limit: int = 20,
    ) -> list[dict]:
        """Quarterly financial statements as ``FundamentalsStore`` row dicts.

        Uses ``/stock/financials-reported``. Returns rows (newest first)
        shaped exactly like ``PolygonClient.get_financials`` so the store and
        feature pipeline are source-agnostic. Degrades to ``[]`` on any error
        or missing subscription.
        """
        data = await self._safe_get(
            "/stock/financials-reported",
            {"symbol": ticker.upper(), "freq": freq},
        )
        if not isinstance(data, dict):
            return []

        periods = data.get("data") or []
        if not isinstance(periods, list):
            return []

        rows: list[dict] = []
        for item in periods[:limit]:
            period_end = item.get("endDate")
            if not period_end:
                continue
            # ``filedDate``/``acceptedDate`` may carry a time component.
            filed = item.get("filedDate") or item.get("acceptedDate") or period_end
            filed = str(filed).split(" ")[0].split("T")[0]
            period_end = str(period_end).split(" ")[0].split("T")[0]

            report = item.get("report", {}) or {}
            ic = report.get("ic")
            bs = report.get("bs")
            cf = report.get("cf")

            def _v(field: str) -> float | None:
                # Route each field to its statement section.
                src = (
                    cf
                    if field in ("operating_cash_flow", "capex")
                    else bs
                    if field in (
                        "cash_and_equivalents",
                        "total_debt",
                        "total_assets",
                        "total_equity",
                    )
                    else ic
                )
                return self._concept_value(src, self._STATEMENT_CONCEPTS[field])

            quarter = item.get("quarter")
            fiscal_period = (
                f"Q{quarter}" if quarter not in (0, None) else "FY"
            )
            ocf = _v("operating_cash_flow")
            capex = _v("capex")
            # As-reported capex is a positive payment; FCF = OCF − |capex|.
            # Compute explicitly so the store doesn't mis-add a positive capex.
            fcf = None
            if ocf is not None and capex is not None:
                fcf = ocf - abs(capex)
            rows.append(
                {
                    "period_end": period_end,
                    "filing_date": filed,
                    "fiscal_year": item.get("year"),
                    "fiscal_period": fiscal_period,
                    "timeframe": "annual" if freq == "annual" else "quarterly",
                    "revenue": _v("revenue"),
                    "gross_profit": _v("gross_profit"),
                    "operating_income": _v("operating_income"),
                    "net_income": _v("net_income"),
                    "eps_diluted": _v("eps_diluted"),
                    "operating_cash_flow": ocf,
                    "capex": capex,
                    "free_cash_flow": fcf,
                    "cash_and_equivalents": _v("cash_and_equivalents"),
                    "total_debt": _v("total_debt"),
                    "total_assets": _v("total_assets"),
                    "total_equity": _v("total_equity"),
                    "shares_diluted": _v("shares_diluted"),
                }
            )

        logger.debug("finnhub_statements_fetched", ticker=ticker, periods=len(rows))
        return rows
