"""Tests for cloud-mode deep dips + history artifact serving (Slice 2)."""

from __future__ import annotations

from math import nan
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tyche.config import TycheSettings
from tyche.market_data.stocks_deep_dips_store import (
    STOCKS_DEEP_DIPS_REL,
    load_deep_dips_scan,
    write_deep_dips_parquet,
)
from tyche.market_data.stocks_history_store import (
    write_history_summary_parquet,
    write_transitions_parquet,
)
from tyche.persistence.published_routes import (
    get_stocks_deep_dips_scan,
    get_stocks_history_payload,
)
from tyche.schemas.alerts import (
    DeepDipAlertResponse,
    DeepDipScanResponse,
    DipClassificationResponse,
    MarketContextResponse,
    RecoverySignalResponse,
)
from tyche.storage import read_json
from tyche.storage.paths import StorageContext
from tyche.workflow.history_summary import STOCKS_HISTORY_SUMMARY_REL
from tyche.workflow.publish_signals import (
    PublishConfig,
    publish_stocks_deep_dips,
    publish_stocks_history,
    run_publish_signals,
)


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


def _sample_deep_dip_scan() -> DeepDipScanResponse:
    return DeepDipScanResponse(
        alerts=[
            DeepDipAlertResponse(
                ticker="GOOG",
                alert_type="oversold_21ema",
                severity="high",
                trend_state="oversold_21ema",
                conviction_level="medium",
                last_close=273.0,
                ema_8=290.0,
                ema_21=305.0,
                ema_50=310.0,
                ema_21_slope=-0.2,
                rsi_14=38.0,
                prior_streak=12,
                dip_pct=10.5,
                price_to_21ema_pct=-10.5,
                iv_rank=nan,
                market_cap=2_000_000_000_000.0,
                dip_classification=DipClassificationResponse(
                    catalyst="market_fear",
                    risk_level="low",
                    reasons=["Broad selloff"],
                    actionable=True,
                ),
                recovery_signal=RecoverySignalResponse(
                    actionable=True,
                    meets_all_thresholds=True,
                    recovery_20d_est="55-58%",
                    threshold_checks=["rsi_ok", "slope_ok"],
                    suggested_cc_dte="14-30",
                ),
            )
        ],
        total_analyzed=1500,
        total_oversold=42,
        total_actionable=8,
        market_context=MarketContextResponse(
            concurrent_dips=120,
            total_universe=1500,
            market_dip_breadth=0.08,
            is_broad_selloff=True,
        ),
        as_of_date="2026-06-09",
    )


def _sample_history_rows() -> list[dict]:
    return [
        {
            "ticker": "AAPL",
            "as_of": "2026-06-09",
            "last_price": 183.0,
            "return_1d": 0.5,
            "return_5d": 1.2,
            "return_1m": 3.4,
            "return_3m": 8.1,
            "return_6m": 12.0,
            "return_1y": 18.5,
            "high_52w": 200.0,
            "low_52w": 150.0,
            "pct_off_52w_high": -8.5,
            "atr_14": 3.2,
            "avg_volume_30d": 65_000_000,
            "trend_state": "uptrend",
            "generated_at": "2026-06-09T16:20:00+00:00",
            "source_run_id": "run-test",
        }
    ]


def _seed_derived_parquet(local_ctx: StorageContext) -> None:
    write_deep_dips_parquet(_sample_deep_dip_scan(), ctx=local_ctx)
    write_history_summary_parquet(_sample_history_rows(), ctx=local_ctx)
    write_transitions_parquet(
        [
            {
                "id": "t1",
                "ticker": "AAPL",
                "from_state": "uptrend",
                "to_state": "pullback_to_8ema",
                "transition_date": "2026-06-08",
                "last_close": 182.0,
                "ema_8": 183.0,
                "ema_21": 180.0,
                "ema_8_slope": 0.3,
                "ema_21_slope": 0.2,
                "conviction_level": "medium",
                "detected_at": "2026-06-08T16:08:00+00:00",
            }
        ],
        ctx=local_ctx,
    )


class TestDeepDipsParquetRoundTrip:
    def test_as_of_date_survives_load(self, local_ctx: StorageContext) -> None:
        write_deep_dips_parquet(_sample_deep_dip_scan(), ctx=local_ctx)
        loaded = load_deep_dips_scan(ctx=local_ctx)
        assert loaded is not None
        assert loaded.as_of_date == "2026-06-09"
        assert len(loaded.alerts) == 1
        assert loaded.alerts[0].ticker == "GOOG"
        assert loaded.alerts[0].iv_rank is None
        assert loaded.alerts[0].recovery_signal is not None
        assert loaded.alerts[0].recovery_signal.meets_all_thresholds is True


class TestPublishStocksDerived:
    def test_publishes_deep_dips_from_parquet(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        write_deep_dips_parquet(_sample_deep_dip_scan(), ctx=local_ctx)

        result = publish_stocks_deep_dips(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )

        assert result.status == "ok"
        assert result.row_count == 1
        assert result.source_paths == [STOCKS_DEEP_DIPS_REL]

        artifact = read_json("published/routes/stocks_deep_dips.json", ctx=local_ctx)
        assert artifact["route"] == "/stocks/deep-dips"
        assert artifact["data"]["as_of_date"] == "2026-06-09"
        assert artifact["data"]["alerts"][0]["ticker"] == "GOOG"
        assert artifact["data"]["alerts"][0]["iv_rank"] is None

    def test_publishes_history_from_parquet(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_derived_parquet(local_ctx)

        result = publish_stocks_history(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )

        assert result.status == "ok"
        assert result.row_count == 1
        assert STOCKS_HISTORY_SUMMARY_REL in result.source_paths

        artifact = read_json("published/routes/stocks_history.json", ctx=local_ctx)
        assert artifact["data"]["total_summaries"] == 1
        assert artifact["data"]["total_transitions"] == 1
        assert artifact["data"]["summaries"][0]["ticker"] == "AAPL"

    def test_nan_sanitized_in_deep_dips_json(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        write_deep_dips_parquet(_sample_deep_dip_scan(), ctx=local_ctx)
        publish_stocks_deep_dips(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )
        raw = (tmp_path / "published/routes/stocks_deep_dips.json").read_text()
        assert "NaN" not in raw
        assert "null" in raw

    def test_full_publish_includes_derived_routes(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        from tyche.market_data.alpha_store import AlphaSignalStore
        from datetime import date

        AlphaSignalStore(data_dir=str(tmp_path), variant="sustained").write(
            [{"ticker": "NVDA", "alpha_score": 70.0, "signal": "buy", "horizon": "swing"}],
            as_of=date(2026, 6, 9),
        )
        _seed_derived_parquet(local_ctx)

        result = run_publish_signals(
            PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
                strict=False,
            )
        )
        keys = {r.route_key for r in result.routes}
        assert "stocks_deep_dips" in keys
        assert "stocks_history" in keys


class TestCloudDerivedReadPath:
    def test_get_deep_dips_from_published(
        self,
        tmp_path: Path,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_derived_parquet(local_ctx)
        publish_stocks_deep_dips(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )

        loaded = get_stocks_deep_dips_scan(settings=gcs_settings, ctx=local_ctx)
        assert loaded is not None
        scan, layer = loaded
        assert layer == "published"
        assert scan.as_of_date == "2026-06-09"
        assert scan.alerts[0].ticker == "GOOG"

    def test_get_history_from_signal_parquet(
        self,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
    ) -> None:
        _seed_derived_parquet(local_ctx)

        loaded = get_stocks_history_payload(settings=gcs_settings, ctx=local_ctx)
        assert loaded is not None
        data, layer = loaded
        assert layer == "signals"
        assert data["total_summaries"] == 1
        assert data["transitions"][0]["ticker"] == "AAPL"

    @pytest.mark.asyncio
    async def test_deep_dips_endpoint_does_not_scan_ohlcv(
        self,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
        tmp_path: Path,
    ) -> None:
        _seed_derived_parquet(local_ctx)

        from tyche.api.routes.stocks import get_deep_dip_candidates

        meta = MagicMock()

        with patch(
            "tyche.persistence.published_routes.storage_context_from_settings",
            return_value=local_ctx,
        ), patch(
            "tyche.market_data.data_store.OHLCVStore.read_all",
            side_effect=AssertionError("read_all must not be called"),
        ), patch(
            "tyche.workflow.deep_dip_scan.run_deep_dip_scan",
            new_callable=AsyncMock,
            side_effect=AssertionError("live scan must not run"),
        ):
            resp = await get_deep_dip_candidates(
                force=False,
                settings=gcs_settings,
                ticker_meta_store=meta,
            )

        assert resp.as_of_date == "2026-06-09"
        assert len(resp.alerts) == 1

    @pytest.mark.asyncio
    async def test_transitions_endpoint_uses_published_payload(
        self,
        gcs_settings: TycheSettings,
        local_ctx: StorageContext,
        tmp_path: Path,
    ) -> None:
        _seed_derived_parquet(local_ctx)
        publish_stocks_history(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=local_ctx,
                settings=gcs_settings,
            ),
            run_id="run-test",
            settings=gcs_settings,
        )

        from tyche.api.routes.stocks import get_transitions_endpoint

        with patch(
            "tyche.persistence.published_routes.storage_context_from_settings",
            return_value=local_ctx,
        ), patch(
            "tyche.persistence.conviction_repository.get_transitions",
            new_callable=AsyncMock,
            side_effect=AssertionError("SQLite transitions must not be queried"),
        ):
            resp = await get_transitions_endpoint(days=7, to_states=None, settings=gcs_settings)

        assert len(resp.transitions) == 1
        assert resp.transitions[0].ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_cloud_mode_returns_empty_deep_dips_without_artifacts(
        self,
        gcs_settings: TycheSettings,
    ) -> None:
        from tyche.api.routes.stocks import get_deep_dip_candidates

        meta = MagicMock()

        with patch(
            "tyche.persistence.published_routes.get_stocks_deep_dips_scan",
            return_value=None,
        ), patch(
            "tyche.workflow.deep_dip_scan.run_deep_dip_scan",
            new_callable=AsyncMock,
            side_effect=AssertionError("live scan must not run"),
        ):
            resp = await get_deep_dip_candidates(
                force=True,
                settings=gcs_settings,
                ticker_meta_store=meta,
            )

        assert resp.alerts == []
        assert resp.total_analyzed == 0
