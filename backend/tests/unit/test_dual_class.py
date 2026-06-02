"""Dual-class Finnhub symbol resolution."""

from tyche.market_data.dual_class import (
    canonical_finnhub_symbol,
    finnhub_symbol_candidates,
    shares_dual_class,
)


def test_goog_maps_to_googl():
    assert canonical_finnhub_symbol("GOOG") == "GOOGL"
    assert finnhub_symbol_candidates("GOOG") == ["GOOGL", "GOOG"]
    assert shares_dual_class("GOOG") is True


def test_googl_stays_canonical():
    assert canonical_finnhub_symbol("GOOGL") == "GOOGL"
    assert finnhub_symbol_candidates("GOOGL") == ["GOOGL", "GOOG"]


def test_unknown_ticker_unchanged():
    assert canonical_finnhub_symbol("PL") == "PL"
    assert finnhub_symbol_candidates("PL") == ["PL"]
    assert shares_dual_class("PL") is False
