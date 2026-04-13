"""Tests for the Form 4 XML parser."""

from __future__ import annotations

from datetime import date

import pytest

from tyche.market_data.form4_parser import (
    InsiderTransaction,
    parse_form4_xml,
    transaction_to_dict,
)

SAMPLE_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <periodOfReport>2026-04-01</periodOfReport>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerName>Tim Cook</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isOfficer>1</isOfficer>
            <isDirector>0</isDirector>
            <isTenPercentOwner>0</isTenPercentOwner>
            <officerTitle>CEO</officerTitle>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionCoding>
                <transactionCode>S</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>50000</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>175.50</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>D</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>3265000</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
        <nonDerivativeTransaction>
            <transactionCoding>
                <transactionCode>P</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>10000</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>170.00</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>3275000</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""

MINIMAL_FORM4_XML = """<?xml version="1.0"?>
<ownershipDocument>
    <reportingOwner>
        <reportingOwnerId>
            <rptOwnerName>Minimal Owner</rptOwnerName>
        </reportingOwnerId>
        <reportingOwnerRelationship>
            <isDirector>1</isDirector>
        </reportingOwnerRelationship>
    </reportingOwner>
    <nonDerivativeTable>
        <nonDerivativeTransaction>
            <transactionCoding>
                <transactionCode>P</transactionCode>
            </transactionCoding>
            <transactionAmounts>
                <transactionShares>
                    <value>500</value>
                </transactionShares>
                <transactionPricePerShare>
                    <value>50.00</value>
                </transactionPricePerShare>
                <transactionAcquiredDisposedCode>
                    <value>A</value>
                </transactionAcquiredDisposedCode>
            </transactionAmounts>
            <postTransactionAmounts>
                <sharesOwnedFollowingTransaction>
                    <value>1500</value>
                </sharesOwnedFollowingTransaction>
            </postTransactionAmounts>
        </nonDerivativeTransaction>
    </nonDerivativeTable>
</ownershipDocument>"""


class TestForm4Parser:
    def test_parses_multiple_transactions(self):
        txs = parse_form4_xml(SAMPLE_FORM4_XML)
        assert len(txs) == 2

    def test_sale_transaction_fields(self):
        txs = parse_form4_xml(SAMPLE_FORM4_XML)
        sale = txs[0]
        assert sale.insider_name == "Tim Cook"
        assert sale.insider_title == "CEO"
        assert sale.is_officer is True
        assert sale.is_director is False
        assert sale.transaction_type == "S"
        assert sale.shares == 50000.0
        assert sale.price_per_share == 175.50
        assert sale.total_value == 50000.0 * 175.50
        assert sale.acquisition_or_disposition == "D"
        assert sale.shares_owned_after == 3265000.0
        assert sale.period_of_report == date(2026, 4, 1)

    def test_purchase_transaction_fields(self):
        txs = parse_form4_xml(SAMPLE_FORM4_XML)
        purchase = txs[1]
        assert purchase.transaction_type == "P"
        assert purchase.shares == 10000.0
        assert purchase.price_per_share == 170.0
        assert purchase.acquisition_or_disposition == "A"

    def test_minimal_xml(self):
        txs = parse_form4_xml(MINIMAL_FORM4_XML)
        assert len(txs) == 1
        tx = txs[0]
        assert tx.insider_name == "Minimal Owner"
        assert tx.is_director is True
        assert tx.is_officer is False
        assert tx.period_of_report is None

    def test_empty_string_returns_empty(self):
        assert parse_form4_xml("") == []

    def test_none_returns_empty(self):
        assert parse_form4_xml("") == []

    def test_malformed_xml_returns_empty(self):
        assert parse_form4_xml("<bad>xml<<") == []

    def test_no_transactions_returns_empty(self):
        xml = """<ownershipDocument>
            <reportingOwner>
                <reportingOwnerId><rptOwnerName>Test</rptOwnerName></reportingOwnerId>
            </reportingOwner>
        </ownershipDocument>"""
        assert parse_form4_xml(xml) == []


class TestTransactionToDict:
    def test_converts_to_dict(self):
        tx = InsiderTransaction(
            insider_name="Test User",
            insider_title="CFO",
            is_officer=True,
            is_director=False,
            is_ten_pct_owner=False,
            transaction_type="S",
            shares=1000.0,
            price_per_share=100.0,
            total_value=100000.0,
            shares_owned_after=5000.0,
            acquisition_or_disposition="D",
            period_of_report=date(2026, 4, 1),
        )

        d = transaction_to_dict(tx, "acc-001", "AAPL", "0000320193", "2026-04-05")
        assert d["ticker"] == "AAPL"
        assert d["accession_no"] == "acc-001"
        assert d["insider_name"] == "Test User"
        assert d["transaction_type"] == "S"
        assert d["shares"] == 1000.0
        assert d["period_of_report"] == date(2026, 4, 1)
