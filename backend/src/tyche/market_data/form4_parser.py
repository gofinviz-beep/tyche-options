"""Parser for SEC Form 4 XML documents.

Form 4 filings have a well-defined XML schema. No LLM needed — the parser
extracts insider identity, transaction details (buy/sell/award), shares,
price, and post-transaction holdings directly from the XML elements.

SEC Form 4 XML namespace: ``http://www.sec.gov/cgi-bin/browse-edgar?action=getcompany``
(though many filings omit the namespace entirely).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

import structlog

logger = structlog.get_logger()

_TX_CODE_MAP = {
    "P": "Purchase",
    "S": "Sale",
    "A": "Award/Grant",
    "D": "Disposition to issuer",
    "F": "Tax withholding",
    "I": "Discretionary",
    "M": "Exercise/Conversion",
    "C": "Conversion",
    "E": "Expiration",
    "G": "Gift",
    "L": "Small acquisition",
    "W": "Will/inheritance",
    "Z": "Trust deposit/withdrawal",
    "J": "Other",
    "K": "Equity swap",
    "U": "Tender of security",
}


@dataclass(frozen=True)
class InsiderTransaction:
    """A single non-derivative transaction from a Form 4 filing."""

    insider_name: str
    insider_title: str
    is_officer: bool
    is_director: bool
    is_ten_pct_owner: bool
    transaction_type: str
    shares: float
    price_per_share: float
    total_value: float
    shares_owned_after: float
    acquisition_or_disposition: str
    period_of_report: date | None


def _text(el: ET.Element | None, tag: str, default: str = "") -> str:
    """Safe extraction of element text, handling missing elements."""
    if el is None:
        return default
    child = el.find(tag)
    if child is None or child.text is None:
        return default
    return child.text.strip()


def _float(el: ET.Element | None, tag: str, default: float = 0.0) -> float:
    raw = _text(el, tag)
    if not raw:
        return default
    try:
        return float(raw)
    except (ValueError, TypeError):
        return default


def _bool_flag(el: ET.Element | None, tag: str) -> bool:
    val = _text(el, tag)
    return val in ("1", "true", "True")


def _parse_date(text: str) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def parse_form4_xml(xml_content: str) -> list[InsiderTransaction]:
    """Parse a Form 4 XML document into a list of InsiderTransactions.

    Handles both namespaced and non-namespaced Form 4 XML.
    Returns an empty list if the XML is malformed or contains no transactions.
    """
    if not xml_content or not xml_content.strip():
        return []

    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as exc:
        logger.warning("form4_xml_parse_error", error=str(exc))
        return []

    ns = ""
    if root.tag.startswith("{"):
        ns = root.tag.split("}")[0] + "}"

    # -- Reporting owner identity --
    owner_el = root.find(f"{ns}reportingOwner")
    if owner_el is None:
        owners = root.findall(f"{ns}reportingOwner")
        owner_el = owners[0] if owners else None

    owner_id = owner_el.find(f"{ns}reportingOwnerId") if owner_el else None
    owner_rel = owner_el.find(f"{ns}reportingOwnerRelationship") if owner_el else None

    insider_name = _text(owner_id, f"{ns}rptOwnerName")
    insider_title = _text(owner_rel, f"{ns}officerTitle")
    is_officer = _bool_flag(owner_rel, f"{ns}isOfficer")
    is_director = _bool_flag(owner_rel, f"{ns}isDirector")
    is_ten_pct = _bool_flag(owner_rel, f"{ns}isTenPercentOwner")

    period_raw = _text(root, f"{ns}periodOfReport")
    period = _parse_date(period_raw)

    transactions: list[InsiderTransaction] = []

    # -- Non-derivative transactions (common stock buys/sells) --
    nd_table = root.find(f"{ns}nonDerivativeTable")
    if nd_table is not None:
        for tx_el in nd_table.findall(f"{ns}nonDerivativeTransaction"):
            coding = tx_el.find(f"{ns}transactionCoding")
            amounts = tx_el.find(f"{ns}transactionAmounts")
            post = tx_el.find(f"{ns}postTransactionAmounts")

            tx_code = _text(coding, f"{ns}transactionCode", "P")
            shares = _float(amounts, f"{ns}transactionShares/{ns}value")
            price = _float(amounts, f"{ns}transactionPricePerShare/{ns}value")
            acq_disp = _text(
                amounts,
                f"{ns}transactionAcquiredDisposedCode/{ns}value",
                "A",
            )
            shares_after = _float(
                post, f"{ns}sharesOwnedFollowingTransaction/{ns}value"
            )

            transactions.append(
                InsiderTransaction(
                    insider_name=insider_name,
                    insider_title=insider_title,
                    is_officer=is_officer,
                    is_director=is_director,
                    is_ten_pct_owner=is_ten_pct,
                    transaction_type=tx_code,
                    shares=shares,
                    price_per_share=price,
                    total_value=round(shares * price, 2),
                    shares_owned_after=shares_after,
                    acquisition_or_disposition=acq_disp,
                    period_of_report=period,
                )
            )

    if not transactions:
        logger.debug("form4_no_transactions", insider=insider_name)

    return transactions


def transaction_to_dict(
    tx: InsiderTransaction,
    accession_no: str,
    ticker: str,
    cik: str,
    filed_at: str,
) -> dict:
    """Convert an InsiderTransaction to a dict for the InsiderTxStore."""
    return {
        "accession_no": accession_no,
        "ticker": ticker.upper(),
        "cik": cik,
        "filed_at": filed_at,
        "period_of_report": tx.period_of_report,
        "insider_name": tx.insider_name,
        "insider_title": tx.insider_title,
        "is_officer": tx.is_officer,
        "is_director": tx.is_director,
        "is_ten_pct_owner": tx.is_ten_pct_owner,
        "transaction_type": tx.transaction_type,
        "shares": tx.shares,
        "price_per_share": tx.price_per_share,
        "total_value": tx.total_value,
        "shares_owned_after": tx.shares_owned_after,
        "acquisition_or_disposition": tx.acquisition_or_disposition,
    }
