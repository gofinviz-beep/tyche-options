"""Tests for the NewsArticleStore."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from tyche.market_data.news_store import NewsArticleStore


def _make_article(article_id: str, ticker: str = "AAPL", hours_ago: int = 1) -> dict:
    return {
        "article_id": article_id,
        "source": "polygon",
        "ticker": ticker,
        "published_at": datetime.now(tz=timezone.utc) - timedelta(hours=hours_ago),
        "title": f"Test article {article_id}",
        "url": f"https://example.com/{article_id}",
        "author": "Test Author",
        "summary": "Test summary.",
    }


class TestNewsArticleStore:

    @pytest.fixture
    def store(self, tmp_path):
        return NewsArticleStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert store.list_tickers() == []
        df = store.read_articles("AAPL")
        assert df.empty

    def test_write_and_read(self, store):
        articles = [_make_article("a1"), _make_article("a2")]
        count = store.write_articles("AAPL", articles)
        assert count == 2

        df = store.read_articles("AAPL")
        assert len(df) == 2
        assert "AAPL" in store.list_tickers()

    def test_dedup_on_article_id(self, store):
        store.write_articles("AAPL", [_make_article("a1")])
        store.write_articles("AAPL", [_make_article("a1"), _make_article("a2")])
        df = store.read_articles("AAPL")
        assert len(df) == 2

    def test_read_recent(self, store):
        old = _make_article("old", hours_ago=72)
        recent = _make_article("new", hours_ago=1)
        store.write_articles("AAPL", [old, recent])

        df = store.read_recent("AAPL", hours=48)
        assert len(df) == 1
        assert df.iloc[0]["article_id"] == "new"

    def test_read_unclassified(self, store):
        articles = [_make_article("a1"), _make_article("a2")]
        store.write_articles("AAPL", articles)

        df = store.read_unclassified("AAPL")
        assert len(df) == 2

        store.update_classification("AAPL", "a1", {
            "event_type": "earnings",
            "sentiment": "positive",
            "impact_score": 0.5,
            "relevance": "primary",
        })

        df = store.read_unclassified("AAPL")
        assert len(df) == 1
        assert df.iloc[0]["article_id"] == "a2"

    def test_update_classification(self, store):
        store.write_articles("AAPL", [_make_article("a1")])

        updated = store.update_classification("AAPL", "a1", {
            "event_type": "regulatory",
            "sentiment": "negative",
            "impact_score": -0.7,
            "relevance": "primary",
        })
        assert updated is True

        df = store.read_articles("AAPL")
        row = df[df["article_id"] == "a1"].iloc[0]
        assert row["event_type"] == "regulatory"
        assert row["sentiment"] == "negative"
        assert row["impact_score"] == pytest.approx(-0.7)

    def test_update_classification_missing_article(self, store):
        store.write_articles("AAPL", [_make_article("a1")])
        updated = store.update_classification("AAPL", "nonexistent", {
            "event_type": "earnings",
        })
        assert updated is False

    def test_update_classification_missing_ticker(self, store):
        updated = store.update_classification("MSFT", "a1", {
            "event_type": "earnings",
        })
        assert updated is False

    def test_bulk_update_classifications(self, store):
        articles = [_make_article("a1"), _make_article("a2"), _make_article("a3")]
        store.write_articles("AAPL", articles)

        classifications = {
            "a1": {"event_type": "earnings", "sentiment": "positive", "impact_score": 0.5},
            "a2": {"event_type": "legal", "sentiment": "negative", "impact_score": -0.3},
            "missing": {"event_type": "other", "sentiment": "neutral", "impact_score": 0.0},
        }
        updated = store.bulk_update_classifications("AAPL", classifications)
        assert updated == 2

        df = store.read_articles("AAPL")
        a1 = df[df["article_id"] == "a1"].iloc[0]
        assert a1["event_type"] == "earnings"
        a3 = df[df["article_id"] == "a3"].iloc[0]
        assert a3["event_type"] is None or a3["event_type"] == "" or (isinstance(a3["event_type"], float) and a3["event_type"] != a3["event_type"])

    def test_write_empty_list(self, store):
        assert store.write_articles("AAPL", []) == 0

    def test_multiple_tickers(self, store):
        store.write_articles("AAPL", [_make_article("a1", ticker="AAPL")])
        store.write_articles("MSFT", [_make_article("a2", ticker="MSFT")])

        tickers = store.list_tickers()
        assert "AAPL" in tickers
        assert "MSFT" in tickers

    def test_read_all_recent(self, store):
        store.write_articles("AAPL", [_make_article("a1", ticker="AAPL", hours_ago=1)])
        store.write_articles("MSFT", [_make_article("a2", ticker="MSFT", hours_ago=1)])

        df = store.read_all_recent(hours=48)
        assert len(df) == 2

    def test_read_articles_with_since(self, store):
        old = _make_article("old", hours_ago=72)
        new = _make_article("new", hours_ago=1)
        store.write_articles("AAPL", [old, new])

        since = datetime.now(tz=timezone.utc) - timedelta(hours=24)
        df = store.read_articles("AAPL", since=since)
        assert len(df) == 1

    def test_read_articles_since_naive_datetime(self, store):
        store.write_articles("AAPL", [_make_article("a1", hours_ago=1)])
        since = datetime.now() - timedelta(hours=2)
        df = store.read_articles("AAPL", since=since)
        assert len(df) == 1
