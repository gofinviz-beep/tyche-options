"""Tests for OCC option symbol builder and parser."""

from __future__ import annotations

from datetime import date

import pytest

from tyche.broker.tradier.symbols import (
    build_occ_symbol,
    normalize_option_type,
    parse_occ_symbol,
)


@pytest.mark.parametrize(
    "underlying,expiration,option_type,strike,expected",
    [
        ("PL", date(2026, 3, 20), "put", 23.0, "PL260320P00023000"),
        ("PL", date(2026, 3, 13), "put", 24.0, "PL260313P00024000"),
        ("AAPL", date(2026, 1, 17), "call", 195.0, "AAPL260117C00195000"),
        ("MSFT", date(2026, 6, 19), "put", 420.50, "MSFT260619P00420500"),
        ("TSLA", date(2026, 12, 18), "call", 250.0, "TSLA261218C00250000"),
    ],
)
def test_build_occ_symbol(
    underlying: str,
    expiration: date,
    option_type: str,
    strike: float,
    expected: str,
) -> None:
    result = build_occ_symbol(underlying, expiration, option_type, strike)
    assert result == expected


@pytest.mark.parametrize(
    "occ_symbol,expected_underlying,expected_type,expected_strike",
    [
        ("PL260320P00023000", "PL", "put", 23.0),
        ("PL260313P00024000", "PL", "put", 24.0),
        ("AAPL260117C00195000", "AAPL", "call", 195.0),
        ("MSFT260619P00420500", "MSFT", "put", 420.5),
    ],
)
def test_parse_occ_symbol(
    occ_symbol: str,
    expected_underlying: str,
    expected_type: str,
    expected_strike: float,
) -> None:
    result = parse_occ_symbol(occ_symbol)
    assert result["underlying"] == expected_underlying
    assert result["option_type"] == expected_type
    assert result["strike"] == expected_strike


def test_roundtrip() -> None:
    """Build -> parse -> build should be identity."""
    original = build_occ_symbol("PL", date(2026, 3, 20), "put", 23.0)
    parsed = parse_occ_symbol(original)
    rebuilt = build_occ_symbol(
        parsed["underlying"],  # type: ignore[arg-type]
        parsed["expiration"],  # type: ignore[arg-type]
        parsed["option_type"],  # type: ignore[arg-type]
        parsed["strike"],  # type: ignore[arg-type]
    )
    assert original == rebuilt


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("P", "put"),
        ("p", "put"),
        ("put", "put"),
        ("C", "call"),
        ("c", "call"),
        ("call", "call"),
    ],
)
def test_normalize_option_type(raw: str, expected: str) -> None:
    assert normalize_option_type(raw) == expected
