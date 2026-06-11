"""Tests for published route repository (GCP-D)."""

from __future__ import annotations

from datetime import date, datetime, timezone
from math import nan
from pathlib import Path

import pytest

from tyche.config import TycheSettings
from tyche.market_data.alpha_store import AlphaSignalStore
from tyche.persistence.published_routes import (
    apply_alpha_scan_filters,
    get_intelligence_news_rows,
    get_stock_alpha_scan,
    load_published_route,
)
from tyche.schemas.alpha import AlphaFactorScores, AlphaScanResponse, AlphaSignalResponse
from tyche.schemas.news import NewsSignalResponse
from tyche.storage import read_json, write_json
from tyche.storage.json_io import sanitize_for_json, sanitize_json_records
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


class TestIntelligenceNewsPublished:
    def test_sanitize_json_records_converts_nan_datetimes_to_none(self) -> None:
        rows = [
            {
                "ticker": "AAPL",
                "news_impact_score": -0.2,
                "negative_count_24h": 1,
                "positive_count_24h": 0,
                "total_count_24h": 1,
                "dominant_event_type": nan,
                "last_negative_at": datetime(2026, 6, 10, tzinfo=timezone.utc),
                "last_positive_at": nan,
                "has_risk": True,
                "updated_at": datetime(2026, 6, 11, tzinfo=timezone.utc),
            },
        ]
        cleaned = sanitize_json_records(rows)
        assert cleaned[0]["last_positive_at"] is None
        assert cleaned[0]["dominant_event_type"] is None
        NewsSignalResponse.model_validate(cleaned[0])

    def test_get_intelligence_news_rows_sanitizes_published_nan(
        self, tmp_path: Path, settings: TycheSettings, local_ctx: StorageContext,
    ) -> None:
        # Simulate legacy published JSON that contains non-standard NaN tokens.
        route_path = tmp_path / "published/routes/intelligence_news.json"
        route_path.parent.mkdir(parents=True, exist_ok=True)
        route_path.write_text(
            """{
  "route": "/news/signals",
  "as_of": "2026-06-11",
  "generated_at": "2026-06-11T12:00:00+00:00",
  "run_id": "test",
  "row_count": 1,
  "source_paths": ["news_signals.parquet"],
  "status": "ok",
  "data": {
    "signals": [{
      "ticker": "AAPL",
      "news_impact_score": -0.2,
      "negative_count_24h": 1,
      "positive_count_24h": 0,
      "total_count_24h": 1,
      "dominant_event_type": null,
      "last_negative_at": "2026-06-10T12:00:00+00:00",
      "last_positive_at": NaN,
      "has_risk": true,
      "updated_at": "2026-06-11T12:00:00+00:00"
    }],
    "total": 1
  }
}""",
            encoding="utf-8",
        )

        loaded = get_intelligence_news_rows(settings=settings, ctx=local_ctx)
        assert loaded is not None
        rows, layer = loaded
        assert layer == "published"
        assert len(rows) == 1
        assert rows[0].last_positive_at is None

    def test_write_json_strips_nan_from_payload(
        self, tmp_path: Path, local_ctx: StorageContext,
    ) -> None:
        write_json(
            {"score": nan, "nested": {"when": nan}},
            "sanitized.json",
            ctx=local_ctx,
        )
        loaded = read_json("sanitized.json", ctx=local_ctx)
        assert loaded == {"score": None, "nested": {"when": None}}
        assert sanitize_for_json(float("nan")) is None
