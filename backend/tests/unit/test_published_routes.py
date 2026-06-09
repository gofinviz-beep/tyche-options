"""Tests for published route repository (GCP-D)."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from tyche.config import TycheSettings
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.persistence.published_routes import (
    apply_alpha_scan_filters,
    get_stock_alpha_scan,
    load_published_route,
)
from tyche.schemas.alpha import AlphaFactorScores, AlphaScanResponse, AlphaSignalResponse
from tyche.storage import write_json
from tyche.storage.paths import StorageContext
from tyche.workflow.publish_signals import run_publish_signals, PublishConfig


@pytest.fixture
def settings(tmp_path: Path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="local",
        alpha_min_market_cap_millions=0,
        api_prefer_published_signals=True,
        api_allow_curated_fallback=False,
        published_max_age_minutes=180,
    )


@pytest.fixture
def local_ctx(tmp_path: Path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


class TestPublishedRoutes:
    def test_load_published_route(self, tmp_path: Path, settings: TycheSettings, local_ctx: StorageContext) -> None:
        store = AlphaSignalStore(data_dir=str(tmp_path), variant="sustained")
        store.write(
            [{"ticker": "NVDA", "alpha_score": 70.0, "signal": "buy", "horizon": "swing"}],
            as_of=date.today(),
        )
        run_publish_signals(
            PublishConfig(data_dir=str(tmp_path), ctx=local_ctx, settings=settings, strict=True),
        )

        env = load_published_route("stocks_alpha", settings=settings, ctx=local_ctx)
        assert env is not None
        assert env.status == "ok"
        assert env.route == "/stocks/alpha/"

    def test_get_stock_alpha_scan_prefers_published(
        self, tmp_path: Path, settings: TycheSettings, local_ctx: StorageContext,
    ) -> None:
        scan_payload = AlphaScanResponse(
            scanned_at="2026-06-07T00:00:00+00:00",
            as_of_date="2026-06-05",
            variant="sustained",
            total=1,
            signals=[
                AlphaSignalResponse(
                    ticker="TEST",
                    alpha_score=88.0,
                    signal="strong_buy",
                    horizon="swing",
                    factors=AlphaFactorScores(),
                ),
            ],
        )
        write_json(
            {
                "route": "/stocks/alpha/",
                "as_of": "2026-06-05",
                "generated_at": "2026-06-07T12:00:00+00:00",
                "run_id": "test",
                "row_count": 1,
                "source_paths": ["alpha_signals_sustained.parquet"],
                "status": "ok",
                "data": scan_payload.model_dump(mode="json"),
            },
            "published/routes/stocks_alpha.json",
            ctx=local_ctx,
        )

        loaded = get_stock_alpha_scan(settings=settings, ctx=local_ctx)
        assert loaded is not None
        scan, layer = loaded
        assert layer == "published"
        assert scan.signals[0].ticker == "TEST"

    def test_apply_alpha_scan_filters_market_cap(self) -> None:
        scan = AlphaScanResponse(
            scanned_at="2026-06-07T00:00:00+00:00",
            variant="sustained",
            total=2,
            signals=[
                AlphaSignalResponse(
                    ticker="BIG",
                    alpha_score=80.0,
                    signal="buy",
                    horizon="swing",
                    factors=AlphaFactorScores(),
                    market_cap=5_000_000_000.0,
                ),
                AlphaSignalResponse(
                    ticker="SMALL",
                    alpha_score=90.0,
                    signal="strong_buy",
                    horizon="swing",
                    factors=AlphaFactorScores(),
                    market_cap=500_000_000.0,
                ),
            ],
        )
        filtered = apply_alpha_scan_filters(scan, min_market_cap_millions=1000, limit=10)
        assert len(filtered.signals) == 1
        assert filtered.signals[0].ticker == "BIG"

    def test_apply_alpha_scan_filters(self) -> None:
        scan = AlphaScanResponse(
            scanned_at="2026-06-07T00:00:00+00:00",
            variant="sustained",
            total=2,
            signals=[
                AlphaSignalResponse(
                    ticker="A",
                    alpha_score=90.0,
                    signal="strong_buy",
                    horizon="swing",
                    factors=AlphaFactorScores(),
                ),
                AlphaSignalResponse(
                    ticker="B",
                    alpha_score=40.0,
                    signal="watch",
                    horizon="trend",
                    factors=AlphaFactorScores(),
                ),
            ],
        )
        filtered = apply_alpha_scan_filters(scan, signal="strong_buy", limit=10)
        assert len(filtered.signals) == 1
        assert filtered.signals[0].ticker == "A"
