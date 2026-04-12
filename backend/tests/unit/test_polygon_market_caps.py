"""Tests for concurrent market cap fetching and bootstrap backfill integration."""

from __future__ import annotations

import asyncio
from datetime import date
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.market_data.polygon import PolygonClient


@pytest.fixture
def client():
    return PolygonClient(
        api_key="test_key",
        base_url="https://api.polygon.io",
        rate_limit_rpm=60_000,
        max_retries=1,
    )


def _mock_response(status_code: int, json_data: dict) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data
    return resp


class TestGetBatchMarketCapsConcurrent:
    """Tests for PolygonClient.get_batch_market_caps_concurrent."""

    @pytest.mark.asyncio
    async def test_empty_tickers_returns_empty(self, client):
        result = await client.get_batch_market_caps_concurrent([], concurrency=5)
        assert result == {}

    @pytest.mark.asyncio
    async def test_successful_fetch(self, client):
        responses = {
            "AAPL": _mock_response(200, {"results": {"market_cap": 3_400_000_000_000}}),
            "MSFT": _mock_response(200, {"results": {"market_cap": 2_800_000_000_000}}),
            "GOOG": _mock_response(200, {"results": {"market_cap": 1_900_000_000_000}}),
        }

        async def mock_get(url, params=None, **kwargs):
            ticker = url.rsplit("/", 1)[-1]
            return responses.get(ticker, _mock_response(200, {"results": {}}))

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["AAPL", "MSFT", "GOOG"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert result["AAPL"] == 3_400_000_000_000
        assert result["MSFT"] == 2_800_000_000_000
        assert result["GOOG"] == 1_900_000_000_000

    @pytest.mark.asyncio
    async def test_zero_cap_excluded(self, client):
        """Tickers with market_cap=0 or null are excluded from results."""
        async def mock_get(url, params=None, **kwargs):
            ticker = url.rsplit("/", 1)[-1]
            if ticker == "AAPL":
                return _mock_response(200, {"results": {"market_cap": 3e12}})
            return _mock_response(200, {"results": {"market_cap": 0}})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["AAPL", "NOCAP"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert "AAPL" in result
        assert "NOCAP" not in result

    @pytest.mark.asyncio
    async def test_null_cap_excluded(self, client):
        """Tickers with market_cap=None are excluded from results."""
        async def mock_get(url, params=None, **kwargs):
            return _mock_response(200, {"results": {"market_cap": None}})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["XYZ"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_partial_failure(self, client):
        """Some tickers fail (HTTP error), others succeed."""
        call_count = 0

        async def mock_get(url, params=None, **kwargs):
            nonlocal call_count
            call_count += 1
            ticker = url.rsplit("/", 1)[-1]
            if ticker == "BAD":
                return _mock_response(500, {})
            return _mock_response(200, {"results": {"market_cap": 1e10}})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["OK1", "BAD", "OK2"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert "OK1" in result
        assert "OK2" in result
        assert "BAD" not in result

    @pytest.mark.asyncio
    async def test_exception_handling(self, client):
        """Network exceptions don't crash the entire batch."""
        async def mock_get(url, params=None, **kwargs):
            ticker = url.rsplit("/", 1)[-1]
            if ticker == "CRASH":
                raise ConnectionError("network down")
            return _mock_response(200, {"results": {"market_cap": 5e9}})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["GOOD", "CRASH"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert "GOOD" in result
        assert "CRASH" not in result

    @pytest.mark.asyncio
    async def test_concurrency_bounded(self, client):
        """Verify semaphore actually limits concurrent requests."""
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def mock_get(url, params=None, **kwargs):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                if current_concurrent > max_concurrent:
                    max_concurrent = current_concurrent
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return _mock_response(200, {"results": {"market_cap": 1e9}})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            tickers = [f"T{i}" for i in range(30)]
            result = await client.get_batch_market_caps_concurrent(
                tickers,
                concurrency=3,
                rate_limit_rpm=60_000,
            )

        assert len(result) == 30
        assert max_concurrent <= 3

    @pytest.mark.asyncio
    async def test_429_retry(self, client):
        """Rate-limited (429) response triggers a retry."""
        call_count = {}

        async def mock_get(url, params=None, **kwargs):
            ticker = url.rsplit("/", 1)[-1]
            call_count[ticker] = call_count.get(ticker, 0) + 1
            if call_count[ticker] == 1:
                return _mock_response(429, {})
            return _mock_response(200, {"results": {"market_cap": 2e9}})

        with (
            patch("httpx.AsyncClient") as mock_cls,
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["RETRY"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert "RETRY" in result
        assert result["RETRY"] == 2e9

    @pytest.mark.asyncio
    async def test_missing_results_key(self, client):
        """Response with no 'results' key is handled gracefully."""
        async def mock_get(url, params=None, **kwargs):
            return _mock_response(200, {"status": "OK"})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            result = await client.get_batch_market_caps_concurrent(
                ["NORESULT"],
                concurrency=5,
                rate_limit_rpm=60_000,
            )

        assert result == {}

    @pytest.mark.asyncio
    async def test_large_batch(self, client):
        """Verify it handles a larger batch without errors."""
        async def mock_get(url, params=None, **kwargs):
            ticker = url.rsplit("/", 1)[-1]
            idx = int(ticker[1:])
            cap = (idx + 1) * 1e9
            return _mock_response(200, {"results": {"market_cap": cap}})

        with patch("httpx.AsyncClient") as mock_cls:
            ctx = AsyncMock()
            ctx.get = mock_get
            mock_cls.return_value.__aenter__ = AsyncMock(return_value=ctx)
            mock_cls.return_value.__aexit__ = AsyncMock(return_value=False)

            tickers = [f"T{i}" for i in range(500)]
            result = await client.get_batch_market_caps_concurrent(
                tickers,
                concurrency=20,
                rate_limit_rpm=60_000,
            )

        assert len(result) == 500


class TestBackfillMarketCaps:
    """Tests for the _backfill_market_caps helper in data_store."""

    @pytest.mark.asyncio
    async def test_skips_when_all_present(self):
        from tyche.market_data.data_store import _backfill_market_caps

        polygon = AsyncMock()
        meta_store = MagicMock()
        meta_store.get_market_caps.return_value = {"AAPL": 3e12, "MSFT": 2e12}

        updated = await _backfill_market_caps(polygon, meta_store)
        assert updated == 0
        polygon.get_batch_market_caps_concurrent.assert_not_called()

    @pytest.mark.asyncio
    async def test_fetches_missing_caps(self):
        from tyche.market_data.data_store import _backfill_market_caps

        polygon = AsyncMock()
        polygon.get_batch_market_caps_concurrent.return_value = {"GOOG": 1.9e12}

        meta_store = MagicMock()
        meta_store.get_market_caps.return_value = {
            "AAPL": 3e12,
            "GOOG": 0.0,
            "NOCAP": 0.0,
        }
        meta_store.update_market_caps.return_value = 1

        updated = await _backfill_market_caps(polygon, meta_store, concurrency=10, rate_limit_rpm=100)

        polygon.get_batch_market_caps_concurrent.assert_called_once_with(
            ["GOOG", "NOCAP"],
            concurrency=10,
            rate_limit_rpm=100,
        )
        meta_store.update_market_caps.assert_called_once_with({"GOOG": 1.9e12})
        assert updated == 1

    @pytest.mark.asyncio
    async def test_no_results_returns_zero(self):
        from tyche.market_data.data_store import _backfill_market_caps

        polygon = AsyncMock()
        polygon.get_batch_market_caps_concurrent.return_value = {}

        meta_store = MagicMock()
        meta_store.get_market_caps.return_value = {"X": 0.0}

        updated = await _backfill_market_caps(polygon, meta_store)
        assert updated == 0
        meta_store.update_market_caps.assert_not_called()


class TestBootstrapOhlcvNoMeta:
    """Verify bootstrap_ohlcv does NOT touch ticker metadata."""

    @pytest.mark.asyncio
    async def test_bootstrap_does_not_call_write_meta(self):
        from tyche.market_data.data_store import bootstrap_ohlcv

        polygon = AsyncMock()
        polygon.get_grouped_daily.return_value = []

        store = MagicMock()
        store.get_latest_date.return_value = None
        store.get_ticker_count.return_value = 0

        with patch("tyche.market_data.data_store.date") as mock_date:
            mock_date.today.return_value = date(2026, 4, 1)
            mock_date.side_effect = lambda *a, **kw: date(*a, **kw)

            await bootstrap_ohlcv(polygon, store, days=5)

        polygon.get_tickers.assert_not_called()
        polygon.get_batch_market_caps_concurrent.assert_not_called()


class TestRefreshTickerMeta:
    """Tests for the standalone refresh_ticker_meta function."""

    @pytest.mark.asyncio
    async def test_backfill_called_after_meta(self):
        from tyche.market_data.data_store import refresh_ticker_meta

        polygon = AsyncMock()
        polygon.get_tickers.return_value = []

        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.get_market_caps.return_value = {"A": 0.0, "B": 5e9}
        meta_store.write_meta.return_value = 0

        polygon.get_batch_market_caps_concurrent.return_value = {"A": 1e10}
        meta_store.update_market_caps.return_value = 1

        result = await refresh_ticker_meta(
            polygon, meta_store,
            backfill_market_caps=True,
            market_cap_concurrency=10,
            market_cap_rpm=100,
        )

        polygon.get_batch_market_caps_concurrent.assert_called_once()
        meta_store.update_market_caps.assert_called_once_with({"A": 1e10})

    @pytest.mark.asyncio
    async def test_backfill_skipped_when_disabled(self):
        from tyche.market_data.data_store import refresh_ticker_meta

        polygon = AsyncMock()
        polygon.get_tickers.return_value = []

        meta_store = MagicMock()
        meta_store.exists = True
        meta_store.write_meta.return_value = 0

        await refresh_ticker_meta(
            polygon, meta_store,
            backfill_market_caps=False,
        )

        polygon.get_batch_market_caps_concurrent.assert_not_called()


class TestWriteMetaPreservesMarketCap:
    """Verify write_meta preserves existing market caps when incoming is zero."""

    def test_existing_cap_not_overwritten_by_zero(self, tmp_path):
        from tyche.market_data.data_store import TickerMetaStore
        from tyche.market_data.polygon import TickerInfo

        store = TickerMetaStore(data_dir=str(tmp_path))

        good_tickers = [
            TickerInfo(ticker="AAPL", name="Apple", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=3e12),
            TickerInfo(ticker="MSFT", name="Microsoft", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=2.8e12),
        ]
        store.write_meta(good_tickers)

        caps = store.get_market_caps(["AAPL", "MSFT"])
        assert caps["AAPL"] == 3e12
        assert caps["MSFT"] == 2.8e12

        zero_tickers = [
            TickerInfo(ticker="AAPL", name="Apple Inc", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=0.0),
            TickerInfo(ticker="MSFT", name="Microsoft Corp", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=0.0),
            TickerInfo(ticker="GOOGL", name="Alphabet", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=0.0),
        ]
        store.write_meta(zero_tickers)

        caps = store.get_market_caps(["AAPL", "MSFT", "GOOGL"])
        assert caps["AAPL"] == 3e12, "Existing cap should be preserved"
        assert caps["MSFT"] == 2.8e12, "Existing cap should be preserved"
        assert caps["GOOGL"] == 0.0, "New ticker with zero is fine"

    def test_newer_positive_cap_replaces_old(self, tmp_path):
        from tyche.market_data.data_store import TickerMetaStore
        from tyche.market_data.polygon import TickerInfo

        store = TickerMetaStore(data_dir=str(tmp_path))

        store.write_meta([
            TickerInfo(ticker="AAPL", name="Apple", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=2.5e12),
        ])

        store.write_meta([
            TickerInfo(ticker="AAPL", name="Apple Inc", market="stocks", locale="us",
                       type="CS", active=True, primary_exchange="XNAS", market_cap=3e12),
        ])

        caps = store.get_market_caps(["AAPL"])
        assert caps["AAPL"] == 3e12, "Positive update should replace old value"


class TestConfigDefaults:
    """Verify new config fields have correct defaults."""

    def test_polygon_rate_limit_rpm_code_default(self):
        """Code default is 500; .env may override (known issue: .env wins)."""
        from tyche.config import TycheSettings

        s = TycheSettings(polygon_api_key="test", _env_file=None)
        assert s.polygon_rate_limit_rpm == 500

    def test_polygon_market_cap_concurrency_default(self):
        from tyche.config import TycheSettings

        s = TycheSettings(polygon_api_key="test", _env_file=None)
        assert s.polygon_market_cap_concurrency == 20
