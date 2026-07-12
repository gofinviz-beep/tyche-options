"""Unit tests for ``GET /stocks/screener`` (mocked persistence layer)."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from tyche.api.routes.screener import get_screener
from tyche.config import TycheSettings
from tyche.schemas.screener import ScreenerResponse, ScreenerRow


def _settings(**overrides) -> TycheSettings:
    defaults = dict(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_backend="local",
    )
    defaults.update(overrides)
    return TycheSettings(**defaults)


def _row(ticker: str, **overrides) -> ScreenerRow:
    defaults = dict(
        ticker=ticker,
        name=f"{ticker} Inc",
        sector="Technology",
        as_of_date="2026-07-10",
        last_close=100.0,
        market_cap=10_000_000_000,
        institutional_pct=50.0,
        rsi_daily=45.0,
        rsi_weekly=55.0,
        rsi_monthly=55.0,
        rsi_quarterly=60.0,
        pct_vs_ema_8=2.0,
        stack_score=2,
        above_sma_200=True,
        setup_score=75.0,
        setup_label="Prime Pullback",
    )
    defaults.update(overrides)
    return ScreenerRow(**defaults)


def _scan(rows: list[ScreenerRow]) -> ScreenerResponse:
    return ScreenerResponse(
        scanned_at="2026-07-10T12:00:00+00:00",
        as_of_date="2026-07-10",
        computed_at="2026-07-10T12:00:00+00:00",
        total=len(rows),
        stale=False,
        rows=rows,
    )


# Direct function calls bypass FastAPI's dependency-injection layer, so
# every ``Query(...)`` parameter must be supplied explicitly (its "default"
# is otherwise the raw ``Query`` sentinel object, not the resolved value).
_DEFAULT_QUERY = dict(
    q_rsi_min=None,
    q_rsi_max=None,
    m_rsi_min=None,
    m_rsi_max=None,
    w_rsi_min=None,
    w_rsi_max=None,
    d_rsi_min=None,
    d_rsi_max=None,
    above_sma200=None,
    stack_score_min=None,
    ext_max_pct=None,
    min_market_cap_millions=None,
    sector=None,
    setup_label=None,
    setup_score_min=None,
    sort="setup_score",
    desc=True,
    limit=200,
)


async def _call_screener(loaded, **query):
    merged = {**_DEFAULT_QUERY, **query}
    with patch(
        "tyche.api.routes.screener.get_stocks_screener_scan",
        return_value=loaded,
    ):
        return await get_screener(settings=_settings(), **merged)


class TestEmptyIndex:
    @pytest.mark.asyncio
    async def test_missing_index_returns_200_empty_stale(self):
        resp = await _call_screener(None)
        assert resp.total == 0
        assert resp.rows == []
        assert resp.stale is True

    @pytest.mark.asyncio
    async def test_empty_rows_marks_stale(self):
        resp = await _call_screener((_scan([]), "signals"))
        assert resp.total == 0
        assert resp.stale is True


class TestRSIFilters:
    @pytest.mark.asyncio
    async def test_quarterly_rsi_range(self):
        rows = [
            _row("AAA", rsi_quarterly=50.0),
            _row("BBB", rsi_quarterly=65.0),
            _row("CCC", rsi_quarterly=80.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), q_rsi_min=55.0, q_rsi_max=70.0)
        assert [r.ticker for r in resp.rows] == ["BBB"]

    @pytest.mark.asyncio
    async def test_daily_rsi_range(self):
        rows = [
            _row("AAA", rsi_daily=20.0),
            _row("BBB", rsi_daily=42.0),
            _row("CCC", rsi_daily=80.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), d_rsi_min=35.0, d_rsi_max=52.0)
        assert [r.ticker for r in resp.rows] == ["BBB"]

    @pytest.mark.asyncio
    async def test_weekly_and_monthly_rsi_ranges(self):
        rows = [
            _row("AAA", rsi_weekly=30.0, rsi_monthly=30.0),
            _row("BBB", rsi_weekly=55.0, rsi_monthly=55.0),
        ]
        resp = await _call_screener(
            (_scan(rows), "signals"), w_rsi_min=50.0, m_rsi_min=50.0
        )
        assert [r.ticker for r in resp.rows] == ["BBB"]


class TestBooleanAndScoreFilters:
    @pytest.mark.asyncio
    async def test_above_sma200_filter(self):
        rows = [
            _row("AAA", above_sma_200=True),
            _row("BBB", above_sma_200=False),
        ]
        resp = await _call_screener((_scan(rows), "signals"), above_sma200=True)
        assert [r.ticker for r in resp.rows] == ["AAA"]

    @pytest.mark.asyncio
    async def test_stack_score_min(self):
        rows = [
            _row("AAA", stack_score=1),
            _row("BBB", stack_score=3),
        ]
        resp = await _call_screener((_scan(rows), "signals"), stack_score_min=2)
        assert [r.ticker for r in resp.rows] == ["BBB"]

    @pytest.mark.asyncio
    async def test_ext_max_pct_caps_overextension(self):
        rows = [
            _row("AAA", pct_vs_ema_8=3.0),
            _row("BBB", pct_vs_ema_8=15.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), ext_max_pct=6.0)
        assert [r.ticker for r in resp.rows] == ["AAA"]

    @pytest.mark.asyncio
    async def test_setup_score_min(self):
        rows = [
            _row("AAA", setup_score=40.0),
            _row("BBB", setup_score=80.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), setup_score_min=70.0)
        assert [r.ticker for r in resp.rows] == ["BBB"]


class TestMarketCapAndSectorFilters:
    @pytest.mark.asyncio
    async def test_min_market_cap_millions(self):
        rows = [
            _row("SMALL", market_cap=500_000_000),
            _row("BIG", market_cap=50_000_000_000),
        ]
        resp = await _call_screener(
            (_scan(rows), "signals"), min_market_cap_millions=4000.0
        )
        assert [r.ticker for r in resp.rows] == ["BIG"]

    @pytest.mark.asyncio
    async def test_sector_exact_match(self):
        rows = [
            _row("AAA", sector="Technology"),
            _row("BBB", sector="Healthcare"),
        ]
        resp = await _call_screener((_scan(rows), "signals"), sector="Healthcare")
        assert [r.ticker for r in resp.rows] == ["BBB"]


class TestSetupLabelFilter:
    @pytest.mark.asyncio
    async def test_single_label(self):
        rows = [
            _row("AAA", setup_label="Prime Pullback"),
            _row("BBB", setup_label="Overextended"),
        ]
        resp = await _call_screener(
            (_scan(rows), "signals"), setup_label="Prime Pullback"
        )
        assert [r.ticker for r in resp.rows] == ["AAA"]

    @pytest.mark.asyncio
    async def test_comma_separated_labels(self):
        rows = [
            _row("AAA", setup_label="Overextended"),
            _row("BBB", setup_label="Weak Structure"),
            _row("CCC", setup_label="Prime Pullback"),
        ]
        resp = await _call_screener(
            (_scan(rows), "signals"),
            setup_label="Overextended,Weak Structure",
        )
        assert {r.ticker for r in resp.rows} == {"AAA", "BBB"}


class TestSortAndLimit:
    @pytest.mark.asyncio
    async def test_default_sort_is_setup_score_desc(self):
        rows = [
            _row("LOW", setup_score=30.0),
            _row("HIGH", setup_score=90.0),
            _row("MID", setup_score=60.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"))
        assert [r.ticker for r in resp.rows] == ["HIGH", "MID", "LOW"]

    @pytest.mark.asyncio
    async def test_ascending_sort(self):
        rows = [
            _row("LOW", setup_score=30.0),
            _row("HIGH", setup_score=90.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), desc=False)
        assert [r.ticker for r in resp.rows] == ["LOW", "HIGH"]

    @pytest.mark.asyncio
    async def test_sort_by_custom_column(self):
        rows = [
            _row("AAA", rsi_quarterly=50.0),
            _row("BBB", rsi_quarterly=80.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), sort="rsi_quarterly")
        assert [r.ticker for r in resp.rows] == ["BBB", "AAA"]

    @pytest.mark.asyncio
    async def test_invalid_sort_column_falls_back_to_setup_score(self):
        rows = [
            _row("LOW", setup_score=30.0),
            _row("HIGH", setup_score=90.0),
        ]
        resp = await _call_screener((_scan(rows), "signals"), sort="not_a_real_column")
        assert [r.ticker for r in resp.rows] == ["HIGH", "LOW"]

    @pytest.mark.asyncio
    async def test_limit_truncates_but_total_reflects_filtered_count(self):
        rows = [_row(f"T{i}", setup_score=float(i)) for i in range(10)]
        resp = await _call_screener((_scan(rows), "signals"), limit=3)
        assert resp.total == 10
        assert len(resp.rows) == 3


class TestCombinedFilters:
    @pytest.mark.asyncio
    async def test_prime_pullback_recipe_combination(self):
        """Mirrors the frontend's 'Diamond — Prime Pullback' preset recipe."""
        rows = [
            _row(
                "DIAMOND",
                rsi_quarterly=62.0,
                rsi_daily=40.0,
                above_sma_200=True,
                stack_score=3,
                pct_vs_ema_8=2.0,
                market_cap=8_000_000_000,
                setup_score=85.0,
            ),
            _row(
                "TOO_EXTENDED",
                rsi_quarterly=62.0,
                rsi_daily=40.0,
                above_sma_200=True,
                stack_score=3,
                pct_vs_ema_8=12.0,
                market_cap=8_000_000_000,
                setup_score=60.0,
            ),
            _row(
                "TOO_HOT",
                rsi_quarterly=62.0,
                rsi_daily=75.0,
                above_sma_200=True,
                stack_score=3,
                pct_vs_ema_8=2.0,
                market_cap=8_000_000_000,
                setup_score=45.0,
            ),
        ]
        resp = await _call_screener(
            (_scan(rows), "signals"),
            q_rsi_min=58.0,
            d_rsi_min=35.0,
            d_rsi_max=52.0,
            above_sma200=True,
            stack_score_min=2,
            ext_max_pct=6.0,
            min_market_cap_millions=4000.0,
        )
        assert [r.ticker for r in resp.rows] == ["DIAMOND"]
