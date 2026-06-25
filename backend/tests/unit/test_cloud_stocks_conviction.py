"""Tests for cloud-mode stocks conviction artifact serving (Slice 1)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import nan
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest

from tyche.config import TycheSettings
from tyche.market_data.stocks_conviction_store import (
    STOCKS_CONVICTION_REL,
    load_stocks_conviction_parquet,
)
from tyche.persistence.published_routes import get_stocks_conviction_rows
from tyche.schemas.stocks import ConvictionSnapshotResponse
from tyche.storage import read_json, write_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.publish_signals import PublishConfig, publish_stocks_conviction, run_publish_signals


def _sample_conviction_row(*, ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "as_of_date": "2026-06-09",
        "trend_state": "pullback_to_8ema",
        "conviction_level": "high",
        "raw_conviction": "high",
        "csp_eligible": True,
        "last_close": 183.0,
        "ema_8": 184.0,
        "ema_21": 180.0,
        "ema_8_slope": 0.4,
        "ema_21_slope": 0.3,
        "price_to_8ema_pct": -0.54,
        "price_to_21ema_pct": 1.67,
        "volume_declining": True,
        "days_above_both_emas": 7,
        "prior_streak": 8,
        "avg_volume_20d": 65_000_000,
        "latest_volume": 55_000_000,
        "ema_50": 175.0,
        "ema_50_slope": 0.2,
        "rsi_14": 45.0,
        "iv_rank": 55.0,
        "iv_percentile": nan,
        "atm_iv": 0.32,
        "vrp": 0.08,
        "conviction_score": 0.72,
        "csp_safety_prob": 0.88,
        "computed_at": "2026-06-09T16:08:00+00:00",
        "market_cap": 3_000_000_000_000.0,
        "institutional_pct": 0.62,
        "sector": "Technology",
        "generated_at": "2026-06-09T16:08:00+00:00",
        "source_run_id": "run-test",
    }


@pytest.fixture
def gcs_settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="gcs",
        gcs_bucket="test-bucket",
        api_prefer_published_signals=True,
        api_allow_curated_fallback=False,
        api_allow_local_db_fallback=False,
        alpha_min_market_cap_millions=0,
        published_max_age_minutes=180,
    )


@pytest.fixture
def local_ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


def _seed_alpha(tmp_path: Path) -> None:
    from tyche.market_data.alpha_store import AlphaSignalStore

    store = AlphaSignalStore(data_dir=str(tmp_path), variant="sustained")
    store.write(
        [{"ticker": "NVDA", "alpha_score": 70.0, "signal": "buy", "horizon": "swing"}],
        as_of=date(2026, 6, 9),
    )


def _seed_conviction_parquet(tmp_path: Path, local_ctx: StorageContext) -> None:
    df = pd.DataFrame(
        [
            _sample_conviction_row(ticker="AAPL"),
            _sample_conviction_row(ticker="MSFT"),
        ]
    )
    write_parquet(df, STOCKS_CONVICTION_REL, ctx=local_ctx)


class TestPublishStocksConviction:
    def test_publishes_route_from_signal_parquet(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_conviction_parquet(tmp_path, local_ctx)

        result = publish_stocks_conviction(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
                conviction_row_limit=5000,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )

        assert result.status == "ok"
        assert result.row_count == 2
        assert result.source_paths == [STOCKS_CONVICTION_REL]

        artifact = read_json("published/routes/stocks_conviction.json", ctx=local_ctx)
        assert artifact["route"] == "/stocks/conviction"
        assert artifact["row_count"] == 2
        assert artifact["source_paths"] == [STOCKS_CONVICTION_REL]
        assert artifact["data"]["total"] == 2
        assert artifact["data"]["snapshots"][0]["ticker"] == "AAPL"
        assert artifact["data"]["snapshots"][0]["iv_percentile"] is None

    def test_nan_sanitized_to_null_in_published_json(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_conviction_parquet(tmp_path, local_ctx)
        publish_stocks_conviction(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )
        raw = (tmp_path / "published/routes/stocks_conviction.json").read_text()
        assert "NaN" not in raw
        assert "null" in raw

    def test_full_publish_includes_stocks_conviction_from_parquet(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_alpha(tmp_path)
        _seed_conviction_parquet(tmp_path, local_ctx)

        result = run_publish_signals(
            PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
                strict=True,
            )
        )
        conviction = next(r for r in result.routes if r.route_key == "stocks_conviction")
        assert conviction.row_count == 2
        assert conviction.source_paths == [STOCKS_CONVICTION_REL]

    def test_publish_ignores_legacy_conviction_signals_parquet(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        """Legacy EMA cache at bucket root must not be used for cloud publish."""
        legacy = _sample_conviction_row(ticker="WST")
        for key in (
            "trend_state",
            "conviction_level",
            "raw_conviction",
            "csp_eligible",
            "volume_declining",
        ):
            legacy.pop(key, None)
        legacy["as_of_date"] = date(2026, 6, 5)
        legacy["volume_declining_on_pullback"] = True
        write_parquet(pd.DataFrame([legacy]), "conviction_signals.parquet", ctx=local_ctx)

        result = publish_stocks_conviction(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )

        assert result.status == "unavailable"
        assert result.row_count == 0
        assert result.source_paths == []


class TestConvictionParquetLoad:
    def test_coerces_date_as_of_from_parquet(
        self,
        local_ctx: StorageContext,
    ) -> None:
        row = _sample_conviction_row()
        row["as_of_date"] = date(2026, 6, 9)
        write_parquet(pd.DataFrame([row]), STOCKS_CONVICTION_REL, ctx=local_ctx)

        rows, as_of = load_stocks_conviction_parquet(ctx=local_ctx)
        assert len(rows) == 1
        assert rows[0].as_of_date == "2026-06-09"
        assert as_of == "2026-06-09"

    def test_skips_legacy_ema_cache_rows(
        self,
        local_ctx: StorageContext,
    ) -> None:
        legacy = {
            "ticker": "WST",
            "as_of_date": date(2026, 6, 5),
            "last_close": 100.0,
            "ema_8": 101.0,
            "ema_21": 99.0,
            "volume_declining_on_pullback": True,
        }
        write_parquet(pd.DataFrame([legacy]), STOCKS_CONVICTION_REL, ctx=local_ctx)

        rows, _as_of = load_stocks_conviction_parquet(ctx=local_ctx)
        assert rows == []


class TestCloudStocksConvictionReadPath:
    def test_get_rows_from_published_json(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_alpha(tmp_path)
        _seed_conviction_parquet(tmp_path, local_ctx)
        run_publish_signals(
            PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
                strict=True,
            )
        )

        loaded = get_stocks_conviction_rows(
            settings=gcs_settings,
            ctx=local_ctx,
        )
        assert loaded is not None
        rows, layer = loaded
        assert layer == "published"
        assert len(rows) == 2
        assert isinstance(rows[0], ConvictionSnapshotResponse)

    def test_get_rows_falls_back_to_signal_parquet(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_conviction_parquet(tmp_path, local_ctx)

        loaded = get_stocks_conviction_rows(
            settings=gcs_settings,
            ctx=local_ctx,
        )
        assert loaded is not None
        rows, layer = loaded
        assert layer == "signals"
        assert rows[0].ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_snapshots_endpoint_does_not_read_conviction_db(
        self,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
        tmp_path: Path,
    ) -> None:
        _seed_conviction_parquet(tmp_path, local_ctx)

        from tyche.api.routes.stocks import get_conviction_snapshots_endpoint

        meta = MagicMock()
        meta.exists = False

        with patch(
            "tyche.persistence.published_routes.storage_context_from_settings",
            return_value=local_ctx,
        ), patch(
            "tyche.persistence.conviction_repository.get_snapshots_for_date",
            new_callable=AsyncMock,
        ) as mock_db, patch(
            "tyche.persistence.conviction_repository.get_latest_snapshot_date",
            new_callable=AsyncMock,
        ) as mock_latest:
            resp = await get_conviction_snapshots_endpoint(
                as_of_date=None,
                settings=gcs_settings,
                meta_store=meta,
            )

        assert len(resp) == 2
        assert resp[0].ticker == "AAPL"
        mock_db.assert_not_called()
        mock_latest.assert_not_called()

    @pytest.mark.asyncio
    async def test_snapshots_endpoint_does_not_scan_ohlcv(
        self,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
        tmp_path: Path,
    ) -> None:
        _seed_conviction_parquet(tmp_path, local_ctx)

        from tyche.api.routes.stocks import get_conviction_snapshots_endpoint

        meta = MagicMock()
        meta.exists = False

        with patch(
            "tyche.persistence.published_routes.storage_context_from_settings",
            return_value=local_ctx,
        ), patch(
            "tyche.market_data.data_store.OHLCVStore.read_all",
            side_effect=AssertionError("read_all must not be called"),
        ):
            resp = await get_conviction_snapshots_endpoint(
                as_of_date=None,
                settings=gcs_settings,
                meta_store=meta,
            )

        assert len(resp) == 2

    @pytest.mark.asyncio
    async def test_cloud_mode_returns_empty_without_artifacts(
        self,
        gcs_settings: TycheSettings,
    ) -> None:
        from tyche.api.routes.stocks import get_conviction_snapshots_endpoint

        meta = MagicMock()
        meta.exists = False

        with patch(
            "tyche.persistence.published_routes.get_stocks_conviction_rows",
            return_value=None,
        ), patch(
            "tyche.persistence.conviction_repository.get_snapshots_for_date",
            new_callable=AsyncMock,
        ) as mock_db:
            resp = await get_conviction_snapshots_endpoint(
                as_of_date=None,
                settings=gcs_settings,
                meta_store=meta,
            )

        assert resp == []
        mock_db.assert_not_called()
