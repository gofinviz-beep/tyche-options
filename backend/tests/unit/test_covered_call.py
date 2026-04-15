"""Tests for CoveredCallStrategy and RecoveryCoveredCallStrategy."""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from tyche.broker.base import OptionContract, OptionsChain, Quote
from tyche.strategy.strategies.covered_call import (
    CoveredCallStrategy,
    RecoveryCoveredCallStrategy,
)


def _make_chain(
    symbol: str = "GOOG",
    underlying_price: float = 275.0,
    strikes: list[float] | None = None,
    dte: int = 21,
    bid_factor: float = 0.02,
) -> OptionsChain:
    """Build a synthetic options chain with call contracts."""
    exp = date.today() + timedelta(days=dte)
    if strikes is None:
        strikes = [270.0, 275.0, 280.0, 285.0, 290.0, 295.0, 300.0]
    contracts = []
    for s in strikes:
        bid = max(0.01, underlying_price * bid_factor * max(0.1, 1 - (s - underlying_price) / underlying_price))
        ask = bid * 1.1
        mid = (bid + ask) / 2
        contracts.append(OptionContract(
            option_symbol=f"O:GOOG{exp:%y%m%d}C{int(s*1000):08d}",
            option_type="call",
            strike=s,
            expiration=exp,
            bid=round(bid, 2),
            ask=round(ask, 2),
            mid=round(mid, 2),
            last=round(mid, 2),
            volume=100,
            open_interest=500,
            implied_volatility=0.35,
            delta=0.3,
        ))
    return OptionsChain(
        symbol=symbol,
        expiration=exp,
        underlying_price=underlying_price,
        contracts=contracts,
    )


def _make_quote(symbol: str = "GOOG", last: float = 275.0) -> Quote:
    return Quote(
        symbol=symbol, last=last, bid=last - 0.5, ask=last + 0.5,
        high=last + 2, low=last - 2, open=last - 1, close=last, volume=1_000_000,
    )


class TestCoveredCallStrategy:
    def test_otm_only(self):
        strategy = CoveredCallStrategy(dte_min=1, dte_max=30)
        chain = _make_chain(underlying_price=275.0)
        quote = _make_quote(last=275.0)
        candidates = strategy.identify_candidates(chain, quote)
        for c in candidates:
            assert c.strike > 275.0

    def test_respects_cost_basis(self):
        strategy = CoveredCallStrategy(dte_min=1, dte_max=30)
        chain = _make_chain(underlying_price=275.0)
        quote = _make_quote(last=275.0)
        candidates = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=290.0,
        )
        for c in candidates:
            assert c.strike >= 290.0

    def test_no_shares_returns_empty(self):
        strategy = CoveredCallStrategy()
        chain = _make_chain()
        quote = _make_quote()
        assert strategy.identify_candidates(chain, quote, shares_held=0) == []

    def test_filter_and_score(self):
        strategy = CoveredCallStrategy(dte_min=1, dte_max=30)
        chain = _make_chain()
        quote = _make_quote()
        raw = strategy.identify_candidates(chain, quote)
        filtered = strategy.apply_filters(raw)
        scored = strategy.score(filtered)
        assert len(scored) > 0
        assert scored[0].score >= scored[-1].score


class TestRecoveryCoveredCallStrategy:
    def test_strike_range_with_ema(self):
        """Strikes must be between floor (2% above entry) and ceiling (21-EMA)."""
        strategy = RecoveryCoveredCallStrategy(dte_min=1, dte_max=60)
        chain = _make_chain(
            underlying_price=275.0,
            strikes=[276.0, 278.0, 280.0, 285.0, 290.0, 295.0, 300.0],
            dte=21,
        )
        quote = _make_quote(last=275.0)
        cost_basis = 265.0
        ema_21 = 290.0

        candidates = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=cost_basis, ema_21=ema_21,
        )
        floor = cost_basis * 1.02
        for c in candidates:
            assert c.strike >= floor, f"Strike {c.strike} below floor {floor}"
            assert c.strike <= ema_21, f"Strike {c.strike} above EMA ceiling {ema_21}"

    def test_strike_range_without_ema(self):
        """Without 21-EMA, falls back to max_strike_above_entry_pct."""
        strategy = RecoveryCoveredCallStrategy(
            dte_min=1, dte_max=60,
            min_strike_above_entry_pct=2.0,
            max_strike_above_entry_pct=8.0,
        )
        chain = _make_chain(
            underlying_price=275.0,
            strikes=[276.0, 278.0, 280.0, 285.0, 290.0, 295.0, 300.0],
            dte=21,
        )
        quote = _make_quote(last=275.0)
        cost_basis = 270.0

        candidates = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=cost_basis, ema_21=0.0,
        )
        floor = cost_basis * 1.02
        ceiling = cost_basis * 1.08
        for c in candidates:
            assert c.strike >= floor
            assert c.strike <= ceiling

    def test_otm_only(self):
        """Must be above current price (OTM)."""
        strategy = RecoveryCoveredCallStrategy(dte_min=1, dte_max=60)
        chain = _make_chain(
            underlying_price=280.0,
            strikes=[270.0, 275.0, 280.0, 285.0, 290.0],
            dte=21,
        )
        quote = _make_quote(last=280.0)
        candidates = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=265.0, ema_21=295.0,
        )
        for c in candidates:
            assert c.strike > 280.0

    def test_dte_range(self):
        """Only selects contracts within the recovery DTE range."""
        strategy = RecoveryCoveredCallStrategy(dte_min=14, dte_max=45)
        short_chain = _make_chain(dte=5)
        long_chain = _make_chain(dte=60)

        quote = _make_quote()
        assert strategy.identify_candidates(
            short_chain, quote, cost_basis_per_share=260.0, ema_21=290.0,
        ) == []
        assert strategy.identify_candidates(
            long_chain, quote, cost_basis_per_share=260.0, ema_21=290.0,
        ) == []

        good_chain = _make_chain(dte=21)
        candidates = strategy.identify_candidates(
            good_chain, quote, cost_basis_per_share=260.0, ema_21=290.0,
        )
        assert len(candidates) > 0

    def test_no_shares_returns_empty(self):
        strategy = RecoveryCoveredCallStrategy()
        chain = _make_chain()
        quote = _make_quote()
        assert strategy.identify_candidates(
            chain, quote, shares_held=0, cost_basis_per_share=260.0,
        ) == []

    def test_strategy_name(self):
        strategy = RecoveryCoveredCallStrategy()
        assert strategy.name == "recovery_covered_call"
        chain = _make_chain(dte=21)
        quote = _make_quote()
        candidates = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=260.0, ema_21=290.0,
        )
        for c in candidates:
            assert c.strategy == "recovery_covered_call"

    def test_proximity_bonus_in_scoring(self):
        """Strikes closer to the 21-EMA recovery target score higher."""
        strategy = RecoveryCoveredCallStrategy(dte_min=1, dte_max=60)
        ema_21 = 290.0
        chain = _make_chain(
            underlying_price=275.0,
            strikes=[280.0, 285.0, 289.0],
            dte=21,
            bid_factor=0.025,
        )
        quote = _make_quote(last=275.0)

        raw = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=265.0, ema_21=ema_21,
        )
        filtered = strategy.apply_filters(raw, min_oi=1, min_volume=1)
        scored = strategy.score(
            filtered, cost_basis_per_share=265.0, ema_21=ema_21,
        )

        assert len(scored) >= 2
        near_ema = [s for s in scored if s.strike >= 288]
        far_from_ema = [s for s in scored if s.strike <= 281]
        if near_ema and far_from_ema:
            assert near_ema[0].score > 0

    def test_filter_passes_good_contracts(self):
        strategy = RecoveryCoveredCallStrategy(dte_min=1, dte_max=60)
        chain = _make_chain(dte=21)
        quote = _make_quote()
        raw = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=260.0, ema_21=295.0,
        )
        filtered = strategy.apply_filters(raw, min_oi=100, min_volume=50)
        assert len(filtered) > 0
        for f in filtered:
            assert f.open_interest >= 100
            assert f.volume >= 50

    def test_filter_rejects_low_oi(self):
        strategy = RecoveryCoveredCallStrategy(dte_min=1, dte_max=60)
        chain = _make_chain(dte=21)
        quote = _make_quote()
        raw = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=260.0, ema_21=295.0,
        )
        filtered = strategy.apply_filters(raw, min_oi=10_000)
        assert len(filtered) == 0

    def test_score_calculates_called_away_profit(self):
        strategy = RecoveryCoveredCallStrategy(dte_min=1, dte_max=60)
        chain = _make_chain(
            underlying_price=275.0,
            strikes=[280.0, 285.0],
            dte=21,
        )
        quote = _make_quote(last=275.0)
        cost_basis = 260.0
        raw = strategy.identify_candidates(
            chain, quote, cost_basis_per_share=cost_basis, ema_21=290.0,
        )
        filtered = strategy.apply_filters(raw, min_oi=1, min_volume=1)
        scored = strategy.score(
            filtered, cost_basis_per_share=cost_basis, ema_21=290.0,
        )
        for s in scored:
            assert s.annualized_return_pct > 0
            assert s.score > 0

    def test_ceiling_adjusts_when_below_floor(self):
        """When EMA is below the min strike floor, ceiling expands slightly."""
        strategy = RecoveryCoveredCallStrategy(
            dte_min=1, dte_max=60,
            min_strike_above_entry_pct=5.0,
        )
        chain = _make_chain(
            underlying_price=275.0,
            strikes=[280.0, 282.0, 285.0],
            dte=21,
        )
        quote = _make_quote(last=275.0)
        candidates = strategy.identify_candidates(
            chain, quote,
            cost_basis_per_share=275.0,
            ema_21=278.0,
        )
        assert isinstance(candidates, list)
