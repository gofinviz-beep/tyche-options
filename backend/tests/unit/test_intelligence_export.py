"""Tests for Parquet-only intelligence signal export."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tyche.config import TycheSettings
from tyche.market_data.news_store import NewsArticleStore
from tyche.ops.intelligence_export import (
    NEWS_CHECKPOINT_REL,
    NEWS_SIGNALS_REL,
    export_news_signals_from_parquet,
)
from tyche.storage import read_parquet
from tyche.storage.paths import StorageContext


def _article(ticker: str, article_id: str, impact: float) -> dict:
    return {
        "article_id": article_id,
        "source": "polygon",
        "ticker": ticker,
        "published_at": datetime.now(tz=timezone.utc) - timedelta(hours=2),
        "title": f"News {article_id}",
        "url": f"https://example.com/{article_id}",
        "author": "Author",
        "summary": "Summary",
        "event_type": "earnings",
        "sentiment": "negative",
        "impact_score": impact,
        "relevance": "primary",
        "classified_at": datetime.now(tz=timezone.utc),
    }


@pytest.fixture
def settings(tmp_path) -> TycheSettings:
    return TycheSettings(
        tradier_api_token="t",
        tradier_account_id="a",
        gemini_api_key="g",
        data_dir=str(tmp_path),
        data_backend="gcs",
        gcs_bucket="test-bucket",
        news_risk_threshold=-0.3,
    )


@pytest.fixture
def local_ctx(tmp_path) -> StorageContext:
    return StorageContext(backend="local", local_root=tmp_path)


@pytest.mark.asyncio
async def test_export_news_signals_batched_checkpoint(
    tmp_path,
    settings: TycheSettings,
    local_ctx: StorageContext,
    monkeypatch,
) -> None:
    monkeypatch.setenv("TYCHE_DATA_BACKEND", "gcs")
    monkeypatch.setenv("TYCHE_GCS_BUCKET", "test-bucket")

    store = NewsArticleStore(data_dir=str(tmp_path), ctx=local_ctx)
    tickers = [f"T{i}" for i in range(5)]
    for ticker in tickers:
        store.write_articles(ticker, [_article(ticker, f"{ticker}-a1", -0.5)])

    summary = await export_news_signals_from_parquet(
        store=store,
        tickers=tickers,
        settings=settings,
        ctx=local_ctx,
        batch_size=2,
    )

    assert summary["rows"] == 5
    assert summary["batches_written"] >= 2

    final_df = read_parquet(NEWS_SIGNALS_REL, ctx=local_ctx)
    checkpoint_df = read_parquet(NEWS_CHECKPOINT_REL, ctx=local_ctx)
    assert final_df is not None and len(final_df) == 5
    assert checkpoint_df is not None and len(checkpoint_df) == 5
