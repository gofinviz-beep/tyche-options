"""Tests for demand-data client parsers (Polygon financials/short interest,
Finnhub estimates/revisions/surprises).

The clients' HTTP layer is exercised elsewhere; here we patch the internal
request method and assert the normalization/parsing into store-ready rows.
"""

from __future__ import annotations

from datetime import date
from unittest.mock import AsyncMock

import pytest

from tyche.exceptions import FinnhubAPIError, PolygonAPIError
from tyche.market_data.benzinga import (
    BenzingaAPIError,
    BenzingaClient,
    classify_guidance,
    derive_guidance_catalysts,
)
from tyche.market_data.finnhub import FinnhubClient
from tyche.market_data.polygon import PolygonClient


# ── Polygon financials ─────────────────────────────────────────────────


@pytest.fixture
def polygon():
    return PolygonClient(api_key="k", rate_limit_rpm=600, max_retries=1)


class TestPolygonFinancials:
    @pytest.mark.asyncio
    async def test_parses_statements(self, polygon):
        polygon._request = AsyncMock(
            return_value={
                "results": [
                    {
                        "end_date": "2025-12-31",
                        "filing_date": "2026-01-28",
                        "fiscal_year": 2025,
                        "fiscal_period": "Q4",
                        "timeframe": "quarterly",
                        "financials": {
                            "income_statement": {
                                "revenues": {"value": 1000},
                                "gross_profit": {"value": 400},
                                "operating_income_loss": {"value": 200},
                                "net_income_loss": {"value": 150},
                                "diluted_earnings_per_share": {"value": 1.5},
                                "diluted_average_shares": {"value": 100},
                            },
                            "balance_sheet": {
                                "assets": {"value": 5000},
                                "equity": {"value": 3000},
                            },
                            "cash_flow_statement": {
                                "net_cash_flow_from_operating_activities": {"value": 250},
                            },
                        },
                    }
                ]
            }
        )
        rows = await polygon.get_financials("MU")
        assert len(rows) == 1
        r = rows[0]
        assert r["period_end"] == "2025-12-31"
        assert r["filing_date"] == "2026-01-28"
        assert r["revenue"] == 1000.0
        assert r["net_income"] == 150.0
        assert r["total_assets"] == 5000.0
        assert r["shares_diluted"] == 100.0

    @pytest.mark.asyncio
    async def test_skips_rows_without_period(self, polygon):
        polygon._request = AsyncMock(
            return_value={"results": [{"financials": {}}]}
        )
        assert await polygon.get_financials("MU") == []

    @pytest.mark.asyncio
    async def test_unavailable_endpoint_degrades(self, polygon):
        polygon._request = AsyncMock(side_effect=PolygonAPIError(403, "no sub"))
        assert await polygon.get_financials("MU") == []


class TestPolygonShortInterest:
    @pytest.mark.asyncio
    async def test_parses_rows(self, polygon):
        polygon._request = AsyncMock(
            return_value={
                "results": [
                    {
                        "settlement_date": "2026-05-15",
                        "short_interest": 10_000_000,
                        "avg_daily_volume": 2_000_000,
                        "days_to_cover": 5.0,
                    }
                ]
            }
        )
        rows = await polygon.get_short_interest("MU")
        assert len(rows) == 1
        assert rows[0]["settlement_date"] == "2026-05-15"
        assert rows[0]["short_interest"] == 10_000_000

    @pytest.mark.asyncio
    async def test_unavailable_degrades(self, polygon):
        polygon._request = AsyncMock(side_effect=PolygonAPIError(404, "n/a"))
        assert await polygon.get_short_interest("MU") == []


# ── Finnhub estimates ──────────────────────────────────────────────────


@pytest.fixture
def finnhub():
    return FinnhubClient(api_key="k", rate_limit_rpm=600, max_retries=1)


class TestFinnhubEstimates:
    @pytest.mark.asyncio
    async def test_recommendation_trends(self, finnhub):
        finnhub._request = AsyncMock(
            return_value=[
                {"period": "2026-05-01", "strongBuy": 12, "buy": 8, "hold": 3, "sell": 1, "strongSell": 0},
            ]
        )
        rows = await finnhub.get_recommendation_trends("MU")
        metrics = {r["metric"]: r["value"] for r in rows}
        assert metrics["rec_strong_buy"] == 12.0
        assert metrics["rec_hold"] == 3.0
        assert all(r["snapshot_date"] == date(2026, 5, 1) for r in rows)

    @pytest.mark.asyncio
    async def test_earnings_surprises(self, finnhub):
        finnhub._request = AsyncMock(
            return_value=[
                {"period": "2025-12-31", "actual": 1.6, "estimate": 1.5, "surprise": 0.1, "surprisePercent": 6.67},
            ]
        )
        rows = await finnhub.get_earnings_surprises("MU")
        metrics = {r["metric"]: r["value"] for r in rows}
        assert metrics["eps_actual"] == pytest.approx(1.6)
        assert metrics["eps_surprise_pct"] == pytest.approx(6.67)

    @pytest.mark.asyncio
    async def test_estimates_eps_and_revenue(self, finnhub):
        def _side_effect(path, params=None):
            if path == "/stock/eps-estimate":
                return {"data": [{"period": "2026-12-31", "epsAvg": 5.5, "numberAnalysts": 20}]}
            if path == "/stock/revenue-estimate":
                return {"data": [{"period": "2026-12-31", "revenueAvg": 30000.0, "numberAnalysts": 18}]}
            return {}

        finnhub._request = AsyncMock(side_effect=_side_effect)
        rows = await finnhub.get_estimates("MU", as_of=date(2026, 5, 1))
        metrics = {r["metric"]: r["value"] for r in rows}
        assert metrics["eps_est_avg"] == pytest.approx(5.5)
        assert metrics["rev_est_avg"] == pytest.approx(30000.0)
        assert metrics["eps_est_count"] == pytest.approx(20.0)

    @pytest.mark.asyncio
    async def test_price_target(self, finnhub):
        finnhub._request = AsyncMock(
            return_value={"targetMean": 120.0, "targetHigh": 150.0, "targetLow": 90.0}
        )
        rows = await finnhub.get_price_target("MU", as_of=date(2026, 5, 1))
        metrics = {r["metric"]: r["value"] for r in rows}
        assert metrics["price_target_mean"] == pytest.approx(120.0)

    @pytest.mark.asyncio
    async def test_basic_financials(self, finnhub):
        finnhub._request = AsyncMock(
            return_value={"metric": {"revenueGrowthTTMYoy": 45.2, "grossMarginTTM": 38.0}}
        )
        rows = await finnhub.get_basic_financials("MU", as_of=date(2026, 5, 1))
        metrics = {r["metric"]: r["value"] for r in rows}
        assert metrics["fin_revenue_growth_ttm_yoy"] == pytest.approx(45.2)
        assert metrics["fin_gross_margin_ttm"] == pytest.approx(38.0)

    @pytest.mark.asyncio
    async def test_endpoint_unavailable_degrades(self, finnhub):
        finnhub._request = AsyncMock(side_effect=FinnhubAPIError(403, "no access"))
        assert await finnhub.get_recommendation_trends("MU") == []
        assert await finnhub.get_estimates("MU") == []


# ── Finnhub financial statements (Fundamental-1) ───────────────────────


def _finnhub_statements_payload() -> dict:
    """As-reported ``/stock/financials-reported`` shape for one quarter."""
    return {
        "symbol": "MU",
        "data": [
            {
                "year": 2025,
                "quarter": 4,
                "form": "10-Q",
                "endDate": "2025-08-28 00:00:00",
                "filedDate": "2025-10-01 16:30:00",
                "report": {
                    "ic": [
                        {"concept": "us-gaap_Revenues", "value": 1000},
                        {"concept": "GrossProfit", "value": 400},
                        {"concept": "OperatingIncomeLoss", "value": 200},
                        {"concept": "NetIncomeLoss", "value": 150},
                        {"concept": "EarningsPerShareDiluted", "value": 1.5},
                        {
                            "concept": "WeightedAverageNumberOfDilutedSharesOutstanding",
                            "value": 100,
                        },
                    ],
                    "bs": [
                        {"concept": "Assets", "value": 5000},
                        {"concept": "StockholdersEquity", "value": 3000},
                        {"concept": "CashAndCashEquivalentsAtCarryingValue", "value": 800},
                        {"concept": "LongTermDebtNoncurrent", "value": 1200},
                    ],
                    "cf": [
                        {
                            "concept": "NetCashProvidedByUsedInOperatingActivities",
                            "value": 250,
                        },
                        {
                            "concept": "PaymentsToAcquirePropertyPlantAndEquipment",
                            "value": 60,
                        },
                    ],
                },
            }
        ],
    }


def _finnhub_standardized_payloads() -> tuple[dict, dict, dict]:
    ic = {
        "symbol": "PL",
        "financials": [
            {
                "period": "2026-01-31",
                "year": 2026,
                "quarter": 4,
                "revenue": 86.822,
                "grossIncome": 47.03,
                "ebit": -36.002,
                "netIncome": -152.455,
                "dilutedEPS": -0.4803,
                "dilutedAverageSharesOutstanding": 317.4112,
            }
        ],
    }
    bs = {
        "symbol": "PL",
        "financials": [
            {
                "period": "2026-01-31",
                "totalAssets": 1200.5,
                "totalEquity": 800.25,
                "totalDebt": 100.0,
                "cashShortTermInvestments": 50.0,
            }
        ],
    }
    cf = {
        "symbol": "PL",
        "financials": [
            {
                "period": "2026-01-31",
                "netOperatingCashFlow": 10.0,
                "capitalExpenditure": -2.5,
            }
        ],
    }
    return ic, bs, cf


class TestFinnhubStandardizedFinancials:
    @pytest.mark.asyncio
    async def test_merges_ic_bs_cf_and_scales_millions(self, finnhub):
        ic, bs, cf = _finnhub_standardized_payloads()

        async def _side_effect(path, params=None):
            stmt = (params or {}).get("statement")
            if stmt == "ic":
                return ic
            if stmt == "bs":
                return bs
            if stmt == "cf":
                return cf
            return {}

        finnhub._request = AsyncMock(side_effect=_side_effect)
        rows = await finnhub.get_standardized_financials("PL")
        assert len(rows) == 1
        r = rows[0]
        assert r["period_end"] == "2026-01-31"
        assert r["filing_date"] == "2026-01-31"
        assert r["fiscal_period"] == "Q4"
        assert r["revenue"] == pytest.approx(86.822e6)
        assert r["gross_profit"] == pytest.approx(47.03e6)
        assert r["eps_diluted"] == pytest.approx(-0.4803)
        assert r["shares_diluted"] == pytest.approx(317.4112e6)
        assert r["total_assets"] == pytest.approx(1200.5e6)
        assert r["operating_cash_flow"] == pytest.approx(10.0e6)
        assert r["capex"] == pytest.approx(-2.5e6)
        assert r["free_cash_flow"] == pytest.approx(7.5e6)


class TestFinnhubStatements:
    @pytest.mark.asyncio
    async def test_parses_statements(self, finnhub):
        finnhub._request = AsyncMock(return_value=_finnhub_statements_payload())
        rows = await finnhub.get_financials_statements("MU")
        assert len(rows) == 1
        r = rows[0]
        # Date/time components are stripped to plain dates.
        assert r["period_end"] == "2025-08-28"
        assert r["filing_date"] == "2025-10-01"
        assert r["fiscal_year"] == 2025
        assert r["fiscal_period"] == "Q4"
        assert r["timeframe"] == "quarterly"
        # Concept mapping (namespaced + bare both match).
        assert r["revenue"] == 1000.0
        assert r["gross_profit"] == 400.0
        assert r["net_income"] == 150.0
        assert r["eps_diluted"] == 1.5
        assert r["shares_diluted"] == 100.0
        assert r["total_assets"] == 5000.0
        assert r["total_equity"] == 3000.0
        assert r["cash_and_equivalents"] == 800.0
        # FCF = OCF − |capex| computed explicitly (capex reported positive).
        assert r["operating_cash_flow"] == 250.0
        assert r["capex"] == 60.0
        assert r["free_cash_flow"] == 190.0

    @pytest.mark.asyncio
    async def test_round_trips_into_store(self, finnhub, tmp_path):
        import pandas as pd

        from tyche.market_data.fundamentals_store import FundamentalsStore

        finnhub._request = AsyncMock(return_value=_finnhub_statements_payload())
        rows = await finnhub.get_financials_statements("MU")

        store = FundamentalsStore(data_dir=str(tmp_path))
        store.write_financials("MU", pd.DataFrame(rows))
        df = store.read_ticker("MU", timeframe="quarterly")
        assert len(df) == 1
        assert df.iloc[0]["revenue"] == 1000.0
        # Margins derived by the store from revenue.
        assert df.iloc[0]["gross_margin"] == pytest.approx(40.0)
        assert df.iloc[0]["free_cash_flow"] == 190.0

    @pytest.mark.asyncio
    async def test_skips_rows_without_period(self, finnhub):
        finnhub._request = AsyncMock(return_value={"data": [{"report": {}}]})
        assert await finnhub.get_financials_statements("MU") == []

    @pytest.mark.asyncio
    async def test_unavailable_endpoint_degrades(self, finnhub):
        finnhub._request = AsyncMock(side_effect=FinnhubAPIError(403, "no sub"))
        assert await finnhub.get_financials_statements("MU") == []


# ── Benzinga corporate guidance ────────────────────────────────────────


@pytest.fixture
def benzinga():
    return BenzingaClient(api_key="k", rate_limit_rpm=600, max_retries=1)


class TestBenzingaGuidance:
    @pytest.mark.asyncio
    async def test_parses_records(self, benzinga):
        # Mirrors the documented sample response (BBWI FY2025).
        benzinga._request = AsyncMock(
            return_value={
                "count": 1,
                "request_id": 1,
                "status": "OK",
                "results": [
                    {
                        "benzinga_id": "682b28a9c068240001a9fded",
                        "company_name": "Bath & Body Works",
                        "currency": "USD",
                        "date": "2025-05-19",
                        "time": "08:30:00",
                        "eps_method": "adj",
                        "estimated_eps_guidance": 3.52,
                        "estimated_revenue_guidance": 7470000000,
                        "fiscal_period": "FY",
                        "fiscal_year": 2025,
                        "importance": 3,
                        "max_eps_guidance": 3.6,
                        "max_revenue_guidance": 7526000000,
                        "min_eps_guidance": 3.25,
                        "min_revenue_guidance": 7380000000,
                        "positioning": "primary",
                        "previous_max_eps_guidance": 3.6,
                        "previous_max_revenue_guidance": 7526000000,
                        "previous_min_eps_guidance": 3.25,
                        "previous_min_revenue_guidance": 7380000000,
                        "release_type": "preliminary",
                        "ticker": "BBWI",
                    }
                ],
            }
        )
        rows = await benzinga.get_corporate_guidance("BBWI")
        assert len(rows) == 1
        r = rows[0]
        assert r["benzinga_id"] == "682b28a9c068240001a9fded"
        assert r["date"] == "2025-05-19"
        assert r["fiscal_period"] == "FY"
        assert r["positioning"] == "primary"
        assert r["estimated_revenue_guidance"] == 7470000000
        assert r["previous_min_revenue_guidance"] == 7380000000

    @pytest.mark.asyncio
    async def test_sample_response_is_reiteration(self, benzinga):
        # The documented sample's previous range == current range → no signal.
        benzinga._request = AsyncMock(
            return_value={
                "results": [
                    {
                        "benzinga_id": "x",
                        "date": "2025-05-19",
                        "positioning": "primary",
                        "estimated_revenue_guidance": 7470000000,
                        "min_revenue_guidance": 7380000000,
                        "max_revenue_guidance": 7526000000,
                        "previous_min_revenue_guidance": 7380000000,
                        "previous_max_revenue_guidance": 7526000000,
                    }
                ]
            }
        )
        rows = await benzinga.get_corporate_guidance("BBWI")
        assert classify_guidance(rows[0]) is None

    @pytest.mark.asyncio
    async def test_unavailable_degrades(self, benzinga):
        benzinga._request = AsyncMock(side_effect=BenzingaAPIError(403, "no sub"))
        assert await benzinga.get_corporate_guidance("MU") == []


class TestClassifyGuidance:
    def test_revenue_raise_from_range_midpoints(self):
        # Current midpoint 11000 vs prior midpoint 10000 → +10% saturated raise.
        verdict = classify_guidance(
            {
                "positioning": "primary",
                "min_revenue_guidance": 10800.0,
                "max_revenue_guidance": 11200.0,
                "previous_min_revenue_guidance": 9800.0,
                "previous_max_revenue_guidance": 10200.0,
            }
        )
        assert verdict is not None
        catalyst, impact = verdict
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx(1.0)

    def test_explicit_midpoint_preferred(self):
        verdict = classify_guidance(
            {
                "estimated_revenue_guidance": 10500.0,
                "previous_min_revenue_guidance": 9800.0,
                "previous_max_revenue_guidance": 10200.0,
            }
        )
        # 10500 vs prior mid 10000 → +5% → mid magnitude.
        catalyst, impact = verdict
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx(0.5)

    def test_eps_cut_fallback(self):
        # No revenue prior → falls back to EPS midpoints; −5% → cut.
        verdict = classify_guidance(
            {
                "estimated_revenue_guidance": 9500.0,
                "estimated_eps_guidance": 1.9,
                "previous_min_eps_guidance": 1.95,
                "previous_max_eps_guidance": 2.05,
            }
        )
        catalyst, impact = verdict
        assert catalyst == "guidance_cut"
        assert impact == pytest.approx(0.5)

    def test_reiteration_is_none(self):
        assert (
            classify_guidance(
                {
                    "estimated_revenue_guidance": 10020.0,
                    "previous_min_revenue_guidance": 9990.0,
                    "previous_max_revenue_guidance": 10010.0,
                }
            )
            is None
        )

    def test_no_prior_is_none(self):
        assert (
            classify_guidance(
                {
                    "estimated_revenue_guidance": 10000.0,
                    "estimated_eps_guidance": 2.0,
                }
            )
            is None
        )

    def test_secondary_positioning_skipped(self):
        assert (
            classify_guidance(
                {
                    "positioning": "secondary",
                    "estimated_revenue_guidance": 11000.0,
                    "previous_min_revenue_guidance": 9800.0,
                    "previous_max_revenue_guidance": 10200.0,
                }
            )
            is None
        )

    def test_signed_impact_through_taxonomy(self):
        from tyche.market_data.catalyst_store import records_from_classification

        catalyst, impact = classify_guidance(
            {
                "positioning": "primary",
                "estimated_revenue_guidance": 11000.0,
                "previous_min_revenue_guidance": 9800.0,
                "previous_max_revenue_guidance": 10200.0,
            }
        )
        rows = records_from_classification(
            ticker="MU",
            event_date=date(2026, 5, 20),
            demand_catalyst=catalyst,
            policy_tag="none",
            impact_score=impact,
            source="guidance",
            ref_id="682b28a9",
        )
        assert len(rows) == 1
        assert rows[0]["kind"] == "demand"
        assert rows[0]["tag"] == "guidance_raise"
        assert rows[0]["signed_impact"] == pytest.approx(0.9)


class TestDeriveGuidanceCatalysts:
    @staticmethod
    def _rec(date_str, fy, fp, rev):
        return {
            "benzinga_id": f"{fy}{fp}",
            "date": date_str,
            "fiscal_year": fy,
            "fiscal_period": fp,
            "positioning": "primary",
            "estimated_revenue_guidance": rev,
        }

    def test_yoy_demand_ramp(self):
        # NVDA-style forward quarterly guides — each is for the next quarter,
        # so a directional signal must come from the same period a year prior.
        recs = [
            self._rec("2025-02-26", 2026, "Q1", 42.05e9),
            self._rec("2025-05-28", 2026, "Q2", 45.66e9),
            self._rec("2026-02-25", 2027, "Q1", 65.96e9),  # vs Q1 FY2026 → +57%
            self._rec("2026-05-20", 2027, "Q2", 86.44e9),  # vs Q2 FY2026 → +89%
        ]
        out = derive_guidance_catalysts(recs)
        # The two FY2027 guides have a year-ago comparator; the FY2026 ones do not.
        dates = {r["date"] for r, _, _ in out}
        assert dates == {"2026-02-25", "2026-05-20"}
        for _, catalyst, impact in out:
            assert catalyst == "guidance_raise"
            assert impact == pytest.approx(1.0)  # >30% YoY saturates

    def test_first_period_has_no_comparator(self):
        recs = [self._rec("2025-02-26", 2026, "Q1", 42.05e9)]
        assert derive_guidance_catalysts(recs) == []

    def test_revision_preferred_over_yoy(self):
        # A same-period revision (previous_*) takes priority over YoY.
        recs = [
            self._rec("2025-02-26", 2026, "Q1", 100.0),
            {
                "benzinga_id": "rev",
                "date": "2026-02-25",
                "fiscal_year": 2027,
                "fiscal_period": "Q1",
                "positioning": "primary",
                "estimated_revenue_guidance": 130.0,  # +30% YoY vs 100
                "previous_min_revenue_guidance": 124.0,
                "previous_max_revenue_guidance": 126.0,  # prior mid 125 → +4% revision
            },
        ]
        out = derive_guidance_catalysts(recs)
        assert len(out) == 1
        _, catalyst, impact = out[0]
        assert catalyst == "guidance_raise"
        # Revision scale (±10%): +4% → 0.4, not the YoY +30% → 1.0.
        assert impact == pytest.approx(0.4)

    def test_yoy_cut(self):
        recs = [
            self._rec("2025-02-26", 2026, "Q1", 100.0),
            self._rec("2026-02-25", 2027, "Q1", 60.0),  # −40% YoY
        ]
        out = derive_guidance_catalysts(recs)
        assert len(out) == 1
        _, catalyst, impact = out[0]
        assert catalyst == "guidance_cut"
        assert impact == pytest.approx(1.0)

    def test_secondary_skipped(self):
        recs = [
            self._rec("2025-02-26", 2026, "Q1", 100.0),
            {**self._rec("2026-02-25", 2027, "Q1", 200.0), "positioning": "secondary"},
        ]
        assert derive_guidance_catalysts(recs) == []


class TestGuideVsConsensus:
    @staticmethod
    def _rec(date_str, fy, fp, rev):
        return {
            "benzinga_id": f"{fy}{fp}",
            "date": date_str,
            "fiscal_year": fy,
            "fiscal_period": fp,
            "positioning": "primary",
            "estimated_revenue_guidance": rev,
        }

    # NVDA-style fiscal year ends in January (FYE month = 1).
    NVDA_FYE = 1

    def test_beat_and_raise(self):
        # NVDA: guide $86.4B for fiscal Q2 FY2027 (ends ~Jul 2026, FYE=Jan) vs
        # Finnhub consensus $80.4B keyed at calendar 2026-06-30 → +7.5% beat.
        recs = [self._rec("2026-05-20", 2027, "Q2", 86.44e9)]
        consensus = [(date(2026, 6, 30), 80.43e9, None)]
        out = derive_guidance_catalysts(
            recs, consensus_by_period=consensus, fye_month=self.NVDA_FYE
        )
        assert len(out) == 1
        _, catalyst, impact = out[0]
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx(1.0)

    def test_disappointment(self):
        # Guide below consensus → guidance_cut.
        recs = [self._rec("2026-05-20", 2027, "Q2", 78.0e9)]
        consensus = [(date(2026, 6, 30), 80.0e9, None)]  # −2.5% → impact 0.5
        out = derive_guidance_catalysts(
            recs, consensus_by_period=consensus, fye_month=self.NVDA_FYE
        )
        _, catalyst, impact = out[0]
        assert catalyst == "guidance_cut"
        assert impact == pytest.approx(0.5)

    def test_consensus_preferred_over_yoy(self):
        # Two guides: YoY would saturate (1.0) on the second, but consensus
        # takes priority and uses the tighter ±5% scale.
        recs = [
            self._rec("2025-05-28", 2026, "Q2", 39.0e9),
            self._rec("2026-05-20", 2027, "Q2", 51.0e9),  # +30.8% YoY → would be 1.0
        ]
        # Consensus only for FY2027 Q2's quarter (calendar 2026-06-30): 50.0B.
        consensus = [(date(2026, 6, 30), 50.0e9, None)]  # +2.0% → 0.4
        out = derive_guidance_catalysts(
            recs, consensus_by_period=consensus, fye_month=self.NVDA_FYE
        )
        # Only the second guide's fiscal quarter is near a consensus period.
        matched = [o for o in out if o[0]["date"] == "2026-05-20"]
        assert len(matched) == 1
        _, catalyst, impact = matched[0]
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx(0.4)  # consensus scale, not YoY

    def test_no_matching_quarter_falls_back_to_yoy(self):
        # The guide's fiscal quarter has no nearby consensus period → falls back
        # to YoY.
        recs = [
            self._rec("2025-05-28", 2026, "Q2", 100.0),
            self._rec("2026-05-20", 2027, "Q2", 140.0),  # +40% YoY
        ]
        # Consensus only has a far-future period (>46d from any guided quarter).
        consensus = [(date(2027, 6, 30), 200.0, None)]
        out = derive_guidance_catalysts(
            recs, consensus_by_period=consensus, fye_month=self.NVDA_FYE
        )
        matched = [o for o in out if o[0]["date"] == "2026-05-20"]
        assert len(matched) == 1
        _, catalyst, impact = matched[0]
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx(1.0)  # YoY scale (>30% saturates)

    def test_offcalendar_fiscal_aligns_to_right_quarter(self):
        # AAPL (FYE September): fiscal Q1 FY2026 ends ~Dec 2025. The consensus
        # for that quarter is keyed at calendar 2025-12-31, NOT 2026-03-31. A
        # naive calendar assumption would match the wrong quarter.
        rec = self._rec("2025-10-30", 2026, "Q1", 130.0e9)
        consensus = [
            (date(2025, 12, 31), 124.0e9, None),  # correct quarter → +4.8% ≈ 0.97
            (date(2026, 3, 31), 95.0e9, None),  # wrong quarter (Apple's fiscal Q2)
        ]
        out = derive_guidance_catalysts(
            [rec], consensus_by_period=consensus, fye_month=9
        )
        assert len(out) == 1
        _, catalyst, impact = out[0]
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx((130.0 - 124.0) / 124.0 / 0.05, rel=1e-6)

    def test_no_fye_month_skips_consensus(self):
        # Without a fiscal-year-end month, consensus is skipped (no wrong-quarter
        # risk) — falls back to YoY.
        recs = [
            self._rec("2025-05-28", 2026, "Q2", 100.0),
            self._rec("2026-05-20", 2027, "Q2", 140.0),  # +40% YoY
        ]
        consensus = [(date(2026, 6, 30), 50.0e9, None)]
        out = derive_guidance_catalysts(recs, consensus_by_period=consensus, fye_month=None)
        matched = [o for o in out if o[0]["date"] == "2026-05-20"]
        assert len(matched) == 1
        _, catalyst, impact = matched[0]
        assert impact == pytest.approx(1.0)  # YoY, not consensus

    def test_reiteration_vs_consensus_is_none(self):
        # Guide essentially in line with consensus (<0.5%) → no catalyst.
        recs = [self._rec("2026-05-20", 2027, "Q2", 80.2e9)]
        consensus = [(date(2026, 6, 30), 80.0e9, None)]  # +0.25% < noise
        out = derive_guidance_catalysts(
            recs, consensus_by_period=consensus, fye_month=self.NVDA_FYE
        )
        assert out == []

    def test_eps_fallback_when_no_revenue_consensus(self):
        rec = {
            "benzinga_id": "x",
            "date": "2026-05-20",
            "fiscal_year": 2027,
            "fiscal_period": "Q2",
            "positioning": "primary",
            "estimated_eps_guidance": 2.2,
        }
        consensus = [(date(2026, 6, 30), None, 2.0)]  # eps +10% → saturates
        out = derive_guidance_catalysts(
            [rec], consensus_by_period=consensus, fye_month=self.NVDA_FYE
        )
        _, catalyst, impact = out[0]
        assert catalyst == "guidance_raise"
        assert impact == pytest.approx(1.0)


class TestFiscalQuarterEnd:
    def test_january_fye_nvda(self):
        from tyche.market_data.benzinga import fiscal_quarter_end

        # NVDA/WMT FYE January: FY2027 ends Jan 2027.
        assert fiscal_quarter_end(2027, "Q1", 1) == date(2026, 4, 30)
        assert fiscal_quarter_end(2027, "Q2", 1) == date(2026, 7, 31)
        assert fiscal_quarter_end(2027, "Q3", 1) == date(2026, 10, 31)
        assert fiscal_quarter_end(2027, "Q4", 1) == date(2027, 1, 31)

    def test_december_fye_calendar(self):
        from tyche.market_data.benzinga import fiscal_quarter_end

        assert fiscal_quarter_end(2026, "Q1", 12) == date(2026, 3, 31)
        assert fiscal_quarter_end(2026, "Q2", 12) == date(2026, 6, 30)
        assert fiscal_quarter_end(2026, "Q4", 12) == date(2026, 12, 31)

    def test_september_fye_apple(self):
        from tyche.market_data.benzinga import fiscal_quarter_end

        # AAPL FYE September: fiscal Q1 FY2026 ends Dec 2025.
        assert fiscal_quarter_end(2026, "Q1", 9) == date(2025, 12, 31)
        assert fiscal_quarter_end(2026, "Q4", 9) == date(2026, 9, 30)

    def test_full_year_and_bad_inputs_return_none(self):
        from tyche.market_data.benzinga import fiscal_quarter_end

        assert fiscal_quarter_end(2027, "FY", 1) is None
        assert fiscal_quarter_end(2027, "", 1) is None
        assert fiscal_quarter_end(None, "Q1", 1) is None
        assert fiscal_quarter_end(2027, "Q1", 0) is None


class TestBuildConsensusByPeriod:
    def test_builds_sorted_list_latest_snapshot(self, tmp_path):
        import pandas as pd

        from tyche.market_data.estimates_store import EstimatesStore
        from tyche.workflow.demand_data import _build_consensus_by_period

        store = EstimatesStore(data_dir=str(tmp_path))
        store.write_records(
            "NVDA",
            pd.DataFrame(
                [
                    # Older snapshot — should be superseded by the newer one.
                    {"snapshot_date": date(2026, 5, 1), "metric": "rev_est_avg",
                     "period": "2026-06-30", "value": 79.0e9},
                    {"snapshot_date": date(2026, 5, 29), "metric": "rev_est_avg",
                     "period": "2026-06-30", "value": 80.43e9},
                    {"snapshot_date": date(2026, 5, 29), "metric": "eps_est_avg",
                     "period": "2026-06-30", "value": 1.05},
                    {"snapshot_date": date(2026, 5, 29), "metric": "rev_est_avg",
                     "period": "2026-09-30", "value": 90.0e9},
                ]
            ),
        )
        out = _build_consensus_by_period(store, "NVDA")
        assert out == [
            (date(2026, 6, 30), 80.43e9, 1.05),
            (date(2026, 9, 30), 90.0e9, None),
        ]

    def test_empty_when_no_store(self):
        from tyche.workflow.demand_data import _build_consensus_by_period

        assert _build_consensus_by_period(None, "NVDA") == []


class TestInferFyeMonth:
    def test_quarterly_labels_imply_january_fye(self, tmp_path):
        import pandas as pd

        from tyche.market_data.fundamentals_store import FundamentalsStore
        from tyche.workflow.demand_data import _infer_fye_month

        store = FundamentalsStore(data_dir=str(tmp_path))
        # NVDA-style: Q1 ends April, Q2 July, Q3 October → FYE January.
        store.write_financials(
            "NVDA",
            pd.DataFrame(
                [
                    {"period_end": date(2025, 4, 27), "fiscal_year": 2026,
                     "fiscal_period": "Q1", "timeframe": "quarterly", "revenue": 1.0},
                    {"period_end": date(2025, 7, 27), "fiscal_year": 2026,
                     "fiscal_period": "Q2", "timeframe": "quarterly", "revenue": 1.0},
                    {"period_end": date(2025, 10, 26), "fiscal_year": 2026,
                     "fiscal_period": "Q3", "timeframe": "quarterly", "revenue": 1.0},
                ]
            ),
        )
        assert _infer_fye_month(store, "NVDA") == 1

    def test_annual_row_gives_fye_directly(self, tmp_path):
        import pandas as pd

        from tyche.market_data.fundamentals_store import FundamentalsStore
        from tyche.workflow.demand_data import _infer_fye_month

        store = FundamentalsStore(data_dir=str(tmp_path))
        store.write_financials(
            "AAPL",
            pd.DataFrame(
                [
                    {"period_end": date(2025, 9, 27), "fiscal_year": 2025,
                     "fiscal_period": "FY", "timeframe": "annual", "revenue": 1.0},
                ]
            ),
        )
        assert _infer_fye_month(store, "AAPL") == 9

    def test_none_when_no_data(self, tmp_path):
        from tyche.market_data.fundamentals_store import FundamentalsStore
        from tyche.workflow.demand_data import _infer_fye_month

        store = FundamentalsStore(data_dir=str(tmp_path))
        assert _infer_fye_month(store, "ZZZZ") is None
        assert _infer_fye_month(None, "ZZZZ") is None
