"""Tests for the filing signal builder."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from tyche.market_data.filing_signals import (
    _detect_cluster_sell,
    compute_filing_signal,
)
from tyche.market_data.filing_store import FILING_8K_SCHEMA, INSIDER_TX_SCHEMA


def _empty_8k() -> pd.DataFrame:
    return pd.DataFrame(columns=[f.name for f in FILING_8K_SCHEMA])


def _empty_tx() -> pd.DataFrame:
    return pd.DataFrame(columns=[f.name for f in INSIDER_TX_SCHEMA])


class TestComputeFilingSignal:
    def test_empty_data(self):
        signal = compute_filing_signal("AAPL", _empty_8k(), _empty_tx())
        assert signal["ticker"] == "AAPL"
        assert signal["eightk_count_30d"] == 0
        assert signal["insider_buy_count_30d"] == 0
        assert signal["insider_sell_count_30d"] == 0
        assert signal["insider_cluster_sell"] is False
        assert signal["last_8k_at"] is None
        assert signal["last_insider_tx_at"] is None

    def test_8k_counts(self):
        now = datetime.now(tz=timezone.utc)
        filings = pd.DataFrame(
            [
                {
                    "accession_no": "acc1",
                    "ticker": "AAPL",
                    "filed_at": now - timedelta(days=5),
                    "event_type": "financial_results",
                    "sentiment": "positive",
                    "impact_score": 0.6,
                    "cik": "001",
                    "form_type": "8-K",
                    "description": "",
                    "filing_url": "",
                    "items_reported": "",
                    "content_summary": "",
                    "classified_at": now,
                },
                {
                    "accession_no": "acc2",
                    "ticker": "AAPL",
                    "filed_at": now - timedelta(days=10),
                    "event_type": "executive",
                    "sentiment": "negative",
                    "impact_score": -0.4,
                    "cik": "001",
                    "form_type": "8-K",
                    "description": "",
                    "filing_url": "",
                    "items_reported": "",
                    "content_summary": "",
                    "classified_at": now,
                },
            ]
        )

        signal = compute_filing_signal("AAPL", filings, _empty_tx())
        assert signal["eightk_count_30d"] == 2
        assert signal["last_8k_sentiment"] == "positive"
        assert signal["last_8k_impact"] == 0.6

    def test_insider_net_shares(self):
        now = datetime.now(tz=timezone.utc)
        txs = pd.DataFrame(
            [
                {
                    "accession_no": "t1",
                    "ticker": "AAPL",
                    "cik": "001",
                    "filed_at": now - timedelta(days=2),
                    "insider_name": "John",
                    "transaction_type": "P",
                    "shares": 5000.0,
                    "insider_title": "CEO",
                    "is_officer": True,
                    "is_director": False,
                    "is_ten_pct_owner": False,
                    "price_per_share": 150.0,
                    "total_value": 750000.0,
                    "shares_owned_after": 50000.0,
                    "acquisition_or_disposition": "A",
                    "period_of_report": None,
                },
                {
                    "accession_no": "t2",
                    "ticker": "AAPL",
                    "cik": "001",
                    "filed_at": now - timedelta(days=1),
                    "insider_name": "Jane",
                    "transaction_type": "S",
                    "shares": 2000.0,
                    "insider_title": "CFO",
                    "is_officer": True,
                    "is_director": False,
                    "is_ten_pct_owner": False,
                    "price_per_share": 160.0,
                    "total_value": 320000.0,
                    "shares_owned_after": 30000.0,
                    "acquisition_or_disposition": "D",
                    "period_of_report": None,
                },
            ]
        )

        signal = compute_filing_signal("AAPL", _empty_8k(), txs)
        assert signal["insider_buy_count_30d"] == 1
        assert signal["insider_sell_count_30d"] == 1
        assert signal["insider_net_shares_30d"] == 3000.0
        assert signal["last_insider_tx_at"] is not None


class TestClusterSellDetection:
    def test_no_sales(self):
        assert _detect_cluster_sell(_empty_tx()) is False

    def test_below_threshold(self):
        now = datetime.now(tz=timezone.utc)
        sells = pd.DataFrame(
            [
                {"insider_name": "A", "filed_at": now, "transaction_type": "S", "shares": 100},
                {"insider_name": "B", "filed_at": now, "transaction_type": "S", "shares": 200},
            ]
        )
        assert _detect_cluster_sell(sells) is False

    def test_cluster_detected(self):
        now = datetime.now(tz=timezone.utc)
        sells = pd.DataFrame(
            [
                {"insider_name": "A", "filed_at": now, "transaction_type": "S", "shares": 100},
                {"insider_name": "B", "filed_at": now + timedelta(days=1), "transaction_type": "S", "shares": 200},
                {"insider_name": "C", "filed_at": now + timedelta(days=3), "transaction_type": "S", "shares": 300},
            ]
        )
        assert _detect_cluster_sell(sells) is True

    def test_cluster_outside_window(self):
        now = datetime.now(tz=timezone.utc)
        sells = pd.DataFrame(
            [
                {"insider_name": "A", "filed_at": now, "transaction_type": "S", "shares": 100},
                {"insider_name": "B", "filed_at": now + timedelta(days=4), "transaction_type": "S", "shares": 200},
                {"insider_name": "C", "filed_at": now + timedelta(days=10), "transaction_type": "S", "shares": 300},
            ]
        )
        assert _detect_cluster_sell(sells) is False

    def test_same_insider_not_counted(self):
        now = datetime.now(tz=timezone.utc)
        sells = pd.DataFrame(
            [
                {"insider_name": "A", "filed_at": now, "transaction_type": "S", "shares": 100},
                {"insider_name": "A", "filed_at": now + timedelta(days=1), "transaction_type": "S", "shares": 200},
                {"insider_name": "A", "filed_at": now + timedelta(days=2), "transaction_type": "S", "shares": 300},
            ]
        )
        assert _detect_cluster_sell(sells) is False
