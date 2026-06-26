"""Tests for cloud-mode options API routes (Slice 6)."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi import HTTPException

from tyche.config import TycheSettings
from tyche.market_data.options_scanner_store import (
    OPTIONS_SCANNER_REL,
    OPTIONS_SCANNER_REPORT_REL,
)
from tyche.market_data.stocks_conviction_store import STOCKS_CONVICTION_REL
from tyche.persistence.published_routes import (
    get_options_conviction_scan,
    get_options_scanner_payload,
)
from tyche.storage import write_json, write_parquet
from tyche.storage.paths import StorageContext
from tyche.workflow.publish_signals import PublishConfig, publish_options_conviction


def _gcs_settings(tmp_path: Path) -> TycheSettings:
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
        allow_inline_scan=False,
    )


def _sample_conviction_row(*, ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "as_of_date": "2026-06-26",
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
        "vrp": 0.08,
        "conviction_score": 0.72,
        "csp_safety_prob": 0.88,
        "computed_at": "2026-06-26T07:00:00+00:00",
        "market_cap": 3_000_000_000_000.0,
        "institutional_pct": 0.62,
        "sector": "Technology",
    }


def _seed_scanner_artifacts(ctx: StorageContext) -> None:
    write_parquet(
        pd.DataFrame(
            [
                {
                    "symbol": "AAA",
                    "option_symbol": "O:AAA260717P00090000",
                    "strike": 98.0,
                    "expiration": "2026-07-17",
                    "dte": 21,
                    "bid": 2.0,
                    "ask": 2.0,
                    "mid": 2.0,
                    "premium_per_contract": 200.0,
                    "collateral_required": 9800.0,
                    "annualized_return_pct": 50.0,
                    "score": 1.2,
                    "delta": -0.25,
                    "theta": -0.05,
                    "implied_volatility": 0.35,
                    "volume": 50,
                    "open_interest": 100,
                    "chain_source": "flatfile",
                    "as_of_date": "2026-06-26",
                }
            ]
        ),
        OPTIONS_SCANNER_REL,
        ctx=ctx,
    )
    write_json(
        {
            "scan_id": "scan-test",
            "as_of_date": "2026-06-26",
            "scanned_at": "2026-06-26T07:11:00+00:00",
            "symbols_scanned": 1,
            "pipeline_stages": [],
            "csp_diagnostics": {},
        },
        OPTIONS_SCANNER_REPORT_REL,
        ctx=ctx,
    )


class TestOptionsScannerReadPath:
    def test_get_payload_from_signal_parquet(self, tmp_path: Path) -> None:
        settings = _gcs_settings(tmp_path)
        ctx = StorageContext(backend="local", local_root=tmp_path)
        _seed_scanner_artifacts(ctx)

        loaded = get_options_scanner_payload(settings=settings, ctx=ctx)
        assert loaded is not None
        payload, layer = loaded
        assert layer == "signals"
        assert len(payload["csp_candidates"]) == 1
        assert payload["symbols_scanned"] == 1

    @pytest.mark.asyncio
    async def test_latest_endpoint_does_not_read_scans_db(
        self, tmp_path: Path
    ) -> None:
        from tyche.api.routes.scanner import get_latest_scan

        settings = _gcs_settings(tmp_path)
        ctx = StorageContext(backend="local", local_root=tmp_path)
        _seed_scanner_artifacts(ctx)

        with patch(
            "tyche.persistence.published_routes.storage_context_from_settings",
            return_value=ctx,
        ), patch(
            "tyche.persistence.scan_repository.load_latest",
            new_callable=AsyncMock,
        ) as mock_db:
            result = await get_latest_scan(settings=settings)

        assert result is not None
        assert result["scan_id"] == "scan-test"
        mock_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_post_scan_blocked_in_cloud_mode(self, tmp_path: Path) -> None:
        from tyche.api.routes.scanner import trigger_scan

        settings = _gcs_settings(tmp_path)

        with pytest.raises(HTTPException) as exc:
            await trigger_scan(
                top_n=10,
                symbols=None,
                force_refresh=False,
                enable_llm=None,
                target_expiration=None,
                available_capital=None,
                broker=MagicMock(),
                strategy=MagicMock(),
                analysis=None,
                earnings=None,
                universe=MagicMock(),
                conviction=MagicMock(),
                store=MagicMock(),
                meta_store=MagicMock(),
                allocator=MagicMock(),
                econ_calendar=MagicMock(),
                settings=settings,
            )

        assert exc.value.status_code == 409

    @pytest.mark.asyncio
    async def test_explore_blocked_in_cloud_mode(self, tmp_path: Path) -> None:
        from tyche.api.routes.scanner import explore_options

        settings = _gcs_settings(tmp_path)

        with pytest.raises(HTTPException) as exc:
            await explore_options(
                symbols="AAPL",
                available_capital=None,
                broker=MagicMock(),
                settings=settings,
            )

        assert exc.value.status_code == 409


class TestOptionsConvictionReadPath:
    def test_publish_options_conviction_from_stocks_parquet(
        self, tmp_path: Path
    ) -> None:
        settings = _gcs_settings(tmp_path)
        ctx = StorageContext(backend="local", local_root=tmp_path)
        write_parquet(
            pd.DataFrame([_sample_conviction_row()]),
            STOCKS_CONVICTION_REL,
            ctx=ctx,
        )

        result = publish_options_conviction(
            config=PublishConfig(
                data_dir=str(tmp_path),
                ctx=ctx,
                settings=settings,
            ),
            run_id="run-test",
            settings=settings,
        )

        assert result.status == "ok"
        assert result.row_count == 1

    def test_get_conviction_scan_from_parquet(self, tmp_path: Path) -> None:
        settings = _gcs_settings(tmp_path)
        ctx = StorageContext(backend="local", local_root=tmp_path)
        write_parquet(
            pd.DataFrame([_sample_conviction_row()]),
            STOCKS_CONVICTION_REL,
            ctx=ctx,
        )

        loaded = get_options_conviction_scan(settings=settings, ctx=ctx)
        assert loaded is not None
        scan, layer = loaded
        assert layer == "signals"
        assert scan.total_screened == 1
        assert scan.signals[0].ticker == "AAPL"

    @pytest.mark.asyncio
    async def test_scan_endpoint_does_not_read_conviction_db(
        self, tmp_path: Path
    ) -> None:
        from tyche.api.routes.conviction import scan_conviction

        settings = _gcs_settings(tmp_path)
        ctx = StorageContext(backend="local", local_root=tmp_path)
        write_parquet(
            pd.DataFrame([_sample_conviction_row()]),
            STOCKS_CONVICTION_REL,
            ctx=ctx,
        )
        meta = MagicMock()
        meta.exists = False

        with patch(
            "tyche.persistence.published_routes.storage_context_from_settings",
            return_value=ctx,
        ), patch(
            "tyche.persistence.conviction_repository.get_snapshots_for_date",
            new_callable=AsyncMock,
        ) as mock_db, patch(
            "tyche.market_data.data_store.OHLCVStore.read_all",
            side_effect=AssertionError("read_all must not be called"),
        ):
            resp = await scan_conviction(
                symbols=None,
                limit_per_path=100,
                force=False,
                meta_store=meta,
                settings=settings,
            )

        assert resp.total_screened == 1
        mock_db.assert_not_called()

    @pytest.mark.asyncio
    async def test_force_scan_blocked_in_cloud_mode(self, tmp_path: Path) -> None:
        from tyche.api.routes.conviction import scan_conviction

        settings = _gcs_settings(tmp_path)
        meta = MagicMock()

        with pytest.raises(HTTPException) as exc:
            await scan_conviction(
                symbols=None,
                limit_per_path=100,
                force=True,
                meta_store=meta,
                settings=settings,
            )

        assert exc.value.status_code == 409


class TestCoveredCallsCloudMode:
    @pytest.mark.asyncio
    async def test_analyze_batch_blocked_in_cloud_mode(self, tmp_path: Path) -> None:
        from tyche.api.routes.covered_calls import analyze_batch
        from tyche.schemas.cc_schemas import CCBatchRequest, CCPositionRequest

        settings = _gcs_settings(tmp_path)
        body = CCBatchRequest(
            positions=[CCPositionRequest(ticker="AAPL", shares=100, cost_basis=150.0)],
            target_dte=14,
        )

        with pytest.raises(HTTPException) as exc:
            await analyze_batch(
                body=body,
                settings=settings,
                broker=MagicMock(),
            )

        assert exc.value.status_code == 409
