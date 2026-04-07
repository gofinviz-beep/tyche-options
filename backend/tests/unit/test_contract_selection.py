"""Tests for the ATM contract selection logic in the ingestion script."""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import pytest

# Import the selection function from the ingestion script
import sys
sys.path.insert(0, "scripts")
from ingest_options_history import _select_atm_contracts


def _make_contracts(
    expirations: list[str],
    strikes: list[float],
    underlying: str = "TEST",
) -> list[dict]:
    """Generate a grid of contract dicts."""
    contracts = []
    for exp in expirations:
        for strike in strikes:
            strike_str = f"{int(strike * 1000):08d}"
            exp_compact = exp.replace("-", "")[2:]
            contracts.append(
                {
                    "ticker": f"O:{underlying}{exp_compact}P{strike_str}",
                    "underlying_ticker": underlying,
                    "contract_type": "put",
                    "expiration_date": exp,
                    "strike_price": strike,
                    "exercise_style": "american",
                }
            )
    return contracts


def _make_ohlcv(start: date, days: int, base_price: float = 100.0) -> pd.DataFrame:
    """Generate synthetic OHLCV data."""
    dates = [start + timedelta(days=i) for i in range(days)]
    return pd.DataFrame(
        {
            "date": dates,
            "close": [base_price + i * 0.5 for i in range(days)],
        }
    )


class TestSelectAtmContracts:
    def test_selects_closest_strike(self) -> None:
        contracts = _make_contracts(
            expirations=["2025-02-07"],
            strikes=[90.0, 95.0, 100.0, 105.0, 110.0],
        )
        ohlcv = _make_ohlcv(date(2025, 1, 6), 5, base_price=102.0)

        selected = _select_atm_contracts(contracts, ohlcv, target_dte=30)
        assert len(selected) > 0

        tickers = list(selected.keys())
        strikes = [selected[t]["strike_price"] for t in tickers]
        assert 100.0 in strikes or 105.0 in strikes

    def test_selects_correct_expiration(self) -> None:
        contracts = _make_contracts(
            expirations=["2025-01-20", "2025-02-07", "2025-03-21"],
            strikes=[100.0],
        )
        ohlcv = _make_ohlcv(date(2025, 1, 6), 3, base_price=100.0)

        selected = _select_atm_contracts(contracts, ohlcv, target_dte=30)
        assert len(selected) == 1

        contract = list(selected.values())[0]
        assert contract["expiration_date"] == "2025-02-07"

    def test_empty_contracts_returns_empty(self) -> None:
        ohlcv = _make_ohlcv(date(2025, 1, 6), 5)
        selected = _select_atm_contracts([], ohlcv)
        assert selected == {}

    def test_empty_ohlcv_returns_empty(self) -> None:
        contracts = _make_contracts(["2025-02-07"], [100.0])
        empty_df = pd.DataFrame(columns=["date", "close"])
        selected = _select_atm_contracts(contracts, empty_df)
        assert selected == {}

    def test_deduplicates_contracts(self) -> None:
        contracts = _make_contracts(
            expirations=["2025-02-07"],
            strikes=[100.0],
        )
        ohlcv = _make_ohlcv(date(2025, 1, 6), 10, base_price=100.0)

        selected = _select_atm_contracts(contracts, ohlcv, target_dte=30)
        assert len(selected) == 1

    def test_multiple_trading_days_different_atm(self) -> None:
        """Price moves enough that a different strike becomes ATM."""
        contracts = _make_contracts(
            expirations=["2025-02-07"],
            strikes=[95.0, 100.0, 105.0, 110.0],
        )
        ohlcv = pd.DataFrame(
            {
                "date": [date(2025, 1, 6), date(2025, 1, 7), date(2025, 1, 8)],
                "close": [96.0, 104.0, 109.0],
            }
        )

        selected = _select_atm_contracts(contracts, ohlcv, target_dte=30)
        selected_strikes = {c["strike_price"] for c in selected.values()}
        assert len(selected_strikes) >= 2

    def test_skips_expired_contracts(self) -> None:
        """Contracts that expired before the trading day should not be selected."""
        contracts = _make_contracts(
            expirations=["2025-01-03"],
            strikes=[100.0],
        )
        ohlcv = _make_ohlcv(date(2025, 1, 6), 5, base_price=100.0)

        selected = _select_atm_contracts(contracts, ohlcv, target_dte=30)
        assert len(selected) == 0


class TestPolygonClientMethods:
    """Verify the new PolygonClient methods exist and have correct signatures."""

    @pytest.mark.asyncio
    async def test_list_options_contracts_signature(self) -> None:
        from tyche.market_data.polygon import PolygonClient

        client = PolygonClient(api_key="test", rate_limit_rpm=100)
        assert hasattr(client, "list_options_contracts")
        assert hasattr(client, "get_option_aggs")

    @pytest.mark.asyncio
    async def test_get_option_aggs_signature(self) -> None:
        from tyche.market_data.polygon import PolygonClient

        import inspect
        client = PolygonClient(api_key="test", rate_limit_rpm=100)
        sig = inspect.signature(client.get_option_aggs)
        params = list(sig.parameters.keys())
        assert "option_ticker" in params
        assert "from_date" in params
        assert "to_date" in params
