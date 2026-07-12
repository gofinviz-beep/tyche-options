"""Unit tests for the Stock Deep Dive route's read-through cache/store logic."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

from tyche.analysis.ticker_deep_dive import TickerDeepDive
from tyche.api.routes.deep_dive import (
    _cache,
    get_ticker_deep_dive,
    invalidate_deep_dive_cache,
)
from tyche.config import TycheSettings
from tyche.schemas.deep_dive import TickerDeepDiveResponse, to_response


@pytest.fixture(autouse=True)
def _clear_cache():
    invalidate_deep_dive_cache()
    yield
    invalidate_deep_dive_cache()


def _settings(**overrides) -> TycheSettings:
    defaults = dict(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_backend="local",
        deep_dive_max_staleness_sessions=2,
    )
    defaults.update(overrides)
    return TycheSettings(**defaults)


def _ohlcv_store(latest: date) -> MagicMock:
    store = MagicMock()
    store.get_latest_date.return_value = latest
    return store


def _fake_payload(ticker: str = "AAPL", as_of: str = "") -> TickerDeepDiveResponse:
    result = TickerDeepDive(ticker=ticker)
    result.last_close = 150.0
    result.as_of_date = as_of or date.today().isoformat()
    return to_response(result)


class TestInMemoryCacheHit:
    @pytest.mark.asyncio
    async def test_cache_hit_skips_store_and_engine(self):
        latest = date.today()
        payload = _fake_payload(as_of=latest.isoformat())
        _cache[("AAPL", latest.isoformat())] = payload

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.side_effect = AssertionError("store must not be read")

        resp = await get_ticker_deep_dive(
            ticker="AAPL",
            force=False,
            settings=_settings(),
            ohlcv_store=_ohlcv_store(latest),
            meta_store=MagicMock(),
            deep_dive_store=deep_dive_store,
        )

        assert resp is payload


class TestStoreHitFresh:
    @pytest.mark.asyncio
    async def test_fresh_store_payload_is_served_without_recompute(self):
        latest = date.today()
        payload = _fake_payload(as_of=latest.isoformat())

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = (payload, latest.isoformat())

        with patch(
            "tyche.analysis.ticker_deep_dive.TickerDeepDiveEngine.analyze",
            side_effect=AssertionError("engine must not run on a fresh store hit"),
        ):
            resp = await get_ticker_deep_dive(
                ticker="AAPL",
                force=False,
                settings=_settings(),
                ohlcv_store=_ohlcv_store(latest),
                meta_store=MagicMock(),
                deep_dive_store=deep_dive_store,
            )

        assert resp is payload
        assert ("AAPL", latest.isoformat()) in _cache


class TestStaleStoreFallsBackToCompute:
    @pytest.mark.asyncio
    async def test_stale_payload_triggers_recompute_and_write_back(self):
        latest = date.today()
        stale_as_of = (latest - timedelta(days=30)).isoformat()
        stale_payload = _fake_payload(as_of=stale_as_of)

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = (stale_payload, stale_as_of)

        fresh_result = TickerDeepDive(ticker="AAPL")
        fresh_result.last_close = 200.0
        fresh_result.as_of_date = latest.isoformat()

        with patch(
            "tyche.analysis.ticker_deep_dive.TickerDeepDiveEngine.analyze",
            return_value=fresh_result,
        ):
            resp = await get_ticker_deep_dive(
                ticker="AAPL",
                force=False,
                settings=_settings(),
                ohlcv_store=_ohlcv_store(latest),
                meta_store=MagicMock(),
                deep_dive_store=deep_dive_store,
            )

        assert resp.last_close == 200.0
        deep_dive_store.write_ticker.assert_called_once()


class TestForceBypass:
    @pytest.mark.asyncio
    async def test_force_bypasses_cache_and_store(self):
        latest = date.today()
        cached_payload = _fake_payload(as_of=latest.isoformat())
        _cache[("AAPL", latest.isoformat())] = cached_payload

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = (cached_payload, latest.isoformat())

        fresh_result = TickerDeepDive(ticker="AAPL")
        fresh_result.last_close = 321.0
        fresh_result.as_of_date = latest.isoformat()

        with patch(
            "tyche.analysis.ticker_deep_dive.TickerDeepDiveEngine.analyze",
            return_value=fresh_result,
        ):
            resp = await get_ticker_deep_dive(
                ticker="AAPL",
                force=True,
                settings=_settings(),
                ohlcv_store=_ohlcv_store(latest),
                meta_store=MagicMock(),
                deep_dive_store=deep_dive_store,
            )

        deep_dive_store.read_ticker.assert_not_called()
        assert resp.last_close == 321.0


class TestCloudModeServesStale:
    @pytest.mark.asyncio
    async def test_cloud_mode_serves_stale_payload_without_recompute(self):
        latest = date.today()
        very_stale_as_of = (latest - timedelta(days=365)).isoformat()
        stale_payload = _fake_payload(as_of=very_stale_as_of)

        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = (stale_payload, very_stale_as_of)

        settings = _settings(data_backend="gcs", allow_inline_scan=False)

        with patch(
            "tyche.analysis.ticker_deep_dive.TickerDeepDiveEngine.analyze",
            side_effect=AssertionError("cloud mode must not recompute inline"),
        ):
            resp = await get_ticker_deep_dive(
                ticker="AAPL",
                force=False,
                settings=settings,
                ohlcv_store=_ohlcv_store(latest),
                meta_store=MagicMock(),
                deep_dive_store=deep_dive_store,
            )

        assert resp is stale_payload

    @pytest.mark.asyncio
    async def test_cloud_mode_404s_when_nothing_precomputed(self):
        latest = date.today()
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        settings = _settings(data_backend="gcs", allow_inline_scan=False)

        with pytest.raises(HTTPException) as exc_info:
            await get_ticker_deep_dive(
                ticker="NOPE",
                force=False,
                settings=settings,
                ohlcv_store=_ohlcv_store(latest),
                meta_store=MagicMock(),
                deep_dive_store=deep_dive_store,
            )

        assert exc_info.value.status_code == 404

    @pytest.mark.asyncio
    async def test_cloud_mode_force_returns_409(self):
        latest = date.today()
        deep_dive_store = MagicMock()
        settings = _settings(data_backend="gcs", allow_inline_scan=False)

        with pytest.raises(HTTPException) as exc_info:
            await get_ticker_deep_dive(
                ticker="AAPL",
                force=True,
                settings=settings,
                ohlcv_store=_ohlcv_store(latest),
                meta_store=MagicMock(),
                deep_dive_store=deep_dive_store,
            )

        assert exc_info.value.status_code == 409
        deep_dive_store.read_ticker.assert_not_called()


class TestNoDataAvailable:
    @pytest.mark.asyncio
    async def test_zero_close_raises_404(self):
        latest = date.today()
        deep_dive_store = MagicMock()
        deep_dive_store.read_ticker.return_value = None

        zero_result = TickerDeepDive(ticker="DEAD")
        zero_result.last_close = 0.0

        with patch(
            "tyche.analysis.ticker_deep_dive.TickerDeepDiveEngine.analyze",
            return_value=zero_result,
        ):
            with pytest.raises(HTTPException) as exc_info:
                await get_ticker_deep_dive(
                    ticker="DEAD",
                    force=False,
                    settings=_settings(),
                    ohlcv_store=_ohlcv_store(latest),
                    meta_store=MagicMock(),
                    deep_dive_store=deep_dive_store,
                )

        assert exc_info.value.status_code == 404
