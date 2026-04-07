"""Tests for the OCC option ticker parser."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tyche.market_data.occ_parser import (
    extract_underlying,
    parse_occ_columns,
    parse_occ_ticker,
)


class TestParseOccTicker:
    """Scalar parsing of individual OCC ticker strings."""

    def test_standard_3char_underlying(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:SPY230327P00390000")
        assert underlying == "SPY"
        assert exp == date(2023, 3, 27)
        assert opt_type == "P"
        assert strike == 390.0

    def test_call_option(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:IWM230327C00137000")
        assert underlying == "IWM"
        assert exp == date(2023, 3, 27)
        assert opt_type == "C"
        assert strike == 137.0

    def test_5char_underlying(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:NANOS230327C00400000")
        assert underlying == "NANOS"
        assert exp == date(2023, 3, 27)
        assert opt_type == "C"
        assert strike == 400.0

    def test_4char_underlying(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:AAPL250620P00190000")
        assert underlying == "AAPL"
        assert exp == date(2025, 6, 20)
        assert opt_type == "P"
        assert strike == 190.0

    def test_1char_underlying(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:X250620P00025000")
        assert underlying == "X"
        assert exp == date(2025, 6, 20)
        assert opt_type == "P"
        assert strike == 25.0

    def test_6char_underlying(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:GOOGLL250620C01500000")
        assert underlying == "GOOGLL"
        assert exp == date(2025, 6, 20)
        assert opt_type == "C"
        assert strike == 1500.0

    def test_fractional_strike(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:SPY250620P00417500")
        assert underlying == "SPY"
        assert strike == 417.5

    def test_sub_dollar_strike(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("O:SIRI250620P00000500")
        assert underlying == "SIRI"
        assert strike == 0.5

    def test_without_prefix(self) -> None:
        underlying, exp, opt_type, strike = parse_occ_ticker("SPY230327P00390000")
        assert underlying == "SPY"
        assert exp == date(2023, 3, 27)

    def test_too_short_raises(self) -> None:
        with pytest.raises(ValueError, match="too short"):
            parse_occ_ticker("O:SHORT")

    def test_invalid_option_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid option type"):
            parse_occ_ticker("O:SPY230327X00390000")

    def test_invalid_date_raises(self) -> None:
        with pytest.raises(ValueError, match="Invalid expiration"):
            parse_occ_ticker("O:SPY991327P00390000")

    def test_high_strike(self) -> None:
        _, _, _, strike = parse_occ_ticker("O:BRK.A250620C99999000")
        assert strike == 99999.0


class TestExtractUnderlying:
    """Vectorized underlying extraction."""

    def test_basic_series(self) -> None:
        tickers = pd.Series([
            "O:SPY230327P00390000",
            "O:AAPL250620P00190000",
            "O:X250620C00025000",
        ])
        result = extract_underlying(tickers)
        assert list(result) == ["SPY", "AAPL", "X"]

    def test_empty_series(self) -> None:
        result = extract_underlying(pd.Series([], dtype=str))
        assert len(result) == 0


class TestParseOccColumns:
    """Vectorized parsing that adds columns to a DataFrame."""

    def test_adds_all_columns(self) -> None:
        df = pd.DataFrame({
            "ticker": [
                "O:SPY230327P00390000",
                "O:AAPL250620C00190000",
                "O:NANOS230327C00400000",
            ]
        })
        result = parse_occ_columns(df)

        assert list(result["underlying"]) == ["SPY", "AAPL", "NANOS"]
        assert list(result["option_type"]) == ["P", "C", "C"]
        assert list(result["strike"]) == [390.0, 190.0, 400.0]

        assert result["expiration"].iloc[0] == date(2023, 3, 27)
        assert result["expiration"].iloc[1] == date(2025, 6, 20)

    def test_custom_ticker_column(self) -> None:
        df = pd.DataFrame({
            "my_ticker": ["O:SPY230327P00390000"],
        })
        result = parse_occ_columns(df, ticker_col="my_ticker")
        assert result["underlying"].iloc[0] == "SPY"

    def test_preserves_existing_columns(self) -> None:
        df = pd.DataFrame({
            "ticker": ["O:SPY230327P00390000"],
            "volume": [1000],
        })
        result = parse_occ_columns(df)
        assert "volume" in result.columns
        assert result["volume"].iloc[0] == 1000
