"""Tests for Filing8KStore and InsiderTxStore."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from tyche.market_data.filing_store import Filing8KStore, InsiderTxStore


def _make_8k(acc: str, ticker: str = "AAPL", days_ago: int = 1) -> dict:
    return {
        "accession_no": acc,
        "cik": "0000320193",
        "filed_at": datetime.now(tz=timezone.utc) - timedelta(days=days_ago),
        "form_type": "8-K",
        "description": f"Test 8-K {acc}",
        "filing_url": f"https://sec.gov/{acc}",
        "items_reported": "2.02",
        "content_summary": "Test filing content.",
    }


def _make_tx(
    acc: str,
    name: str = "John CEO",
    tx_type: str = "P",
    ticker: str = "AAPL",
    days_ago: int = 1,
    shares: float = 1000.0,
) -> dict:
    return {
        "accession_no": acc,
        "cik": "0000320193",
        "filed_at": datetime.now(tz=timezone.utc) - timedelta(days=days_ago),
        "period_of_report": (date.today() - timedelta(days=days_ago)),
        "insider_name": name,
        "insider_title": "CEO",
        "is_officer": True,
        "is_director": False,
        "is_ten_pct_owner": False,
        "transaction_type": tx_type,
        "shares": shares,
        "price_per_share": 150.0,
        "total_value": shares * 150.0,
        "shares_owned_after": 50000.0,
        "acquisition_or_disposition": "A" if tx_type == "P" else "D",
    }


class TestFiling8KStore:
    @pytest.fixture
    def store(self, tmp_path):
        return Filing8KStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert store.list_tickers() == []
        df = store.read_filings("AAPL")
        assert df.empty

    def test_write_and_read(self, store):
        filings = [_make_8k("acc1"), _make_8k("acc2")]
        count = store.write_filings("AAPL", filings)
        assert count == 2

        df = store.read_filings("AAPL")
        assert len(df) == 2
        assert "AAPL" in store.list_tickers()

    def test_dedup_on_accession(self, store):
        store.write_filings("AAPL", [_make_8k("acc1")])
        store.write_filings("AAPL", [_make_8k("acc1"), _make_8k("acc2")])
        df = store.read_filings("AAPL")
        assert len(df) == 2

    def test_read_recent(self, store):
        old = _make_8k("old", days_ago=45)
        recent = _make_8k("new", days_ago=1)
        store.write_filings("AAPL", [old, recent])

        df = store.read_recent("AAPL", days=30)
        assert len(df) == 1
        assert df.iloc[0]["accession_no"] == "new"

    def test_read_unclassified(self, store):
        filings = [_make_8k("acc1"), _make_8k("acc2")]
        store.write_filings("AAPL", filings)

        df = store.read_unclassified("AAPL")
        assert len(df) == 2

    def test_bulk_update_classifications(self, store):
        store.write_filings("AAPL", [_make_8k("acc1"), _make_8k("acc2")])

        classifications = {
            "acc1": {
                "event_type": "financial_results",
                "sentiment": "positive",
                "impact_score": 0.7,
            },
        }
        updated = store.bulk_update_classifications("AAPL", classifications)
        assert updated == 1

        df = store.read_unclassified("AAPL")
        assert len(df) == 1
        assert df.iloc[0]["accession_no"] == "acc2"

    def test_write_empty_list(self, store):
        assert store.write_filings("AAPL", []) == 0

    def test_nonexistent_ticker_update(self, store):
        updated = store.bulk_update_classifications("ZZZZ", {"acc1": {}})
        assert updated == 0


class TestInsiderTxStore:
    @pytest.fixture
    def store(self, tmp_path):
        return InsiderTxStore(data_dir=str(tmp_path))

    def test_empty_store(self, store):
        assert store.list_tickers() == []
        df = store.read_transactions("AAPL")
        assert df.empty

    def test_write_and_read(self, store):
        txs = [_make_tx("acc1", "John", "P"), _make_tx("acc2", "Jane", "S")]
        count = store.write_transactions("AAPL", txs)
        assert count == 2

        df = store.read_transactions("AAPL")
        assert len(df) == 2

    def test_dedup_on_triple_key(self, store):
        tx1 = _make_tx("acc1", "John", "P")
        tx2 = _make_tx("acc1", "John", "P")
        store.write_transactions("AAPL", [tx1])
        store.write_transactions("AAPL", [tx2])
        df = store.read_transactions("AAPL")
        assert len(df) == 1

    def test_different_insiders_same_accession(self, store):
        tx1 = _make_tx("acc1", "John", "P")
        tx2 = _make_tx("acc1", "Jane", "S")
        store.write_transactions("AAPL", [tx1, tx2])
        df = store.read_transactions("AAPL")
        assert len(df) == 2

    def test_read_recent(self, store):
        old = _make_tx("old", "John", "P", days_ago=45)
        recent = _make_tx("new", "Jane", "S", days_ago=1)
        store.write_transactions("AAPL", [old, recent])

        df = store.read_recent("AAPL", days=30)
        assert len(df) == 1
        assert df.iloc[0]["insider_name"] == "Jane"

    def test_write_empty_list(self, store):
        assert store.write_transactions("AAPL", []) == 0
