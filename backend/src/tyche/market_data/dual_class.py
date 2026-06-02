"""Dual-class share tickers — one Finnhub fetch, many listed symbols.

Company-level fundamentals and estimates are identical across share classes.
Finnhub often publishes under the voting / primary SEC symbol (e.g. GOOGL not
GOOG). Map each class to a canonical symbol for API fetch, then persist rows
under the universe ticker requested by the caller.
"""

from __future__ import annotations

# (canonical voting / primary SEC symbol, all share-class tickers in the group)
_DUAL_CLASS_GROUPS: tuple[tuple[str, frozenset[str]], ...] = (
    ("GOOGL", frozenset({"GOOG", "GOOGL"})),
    ("BRK.A", frozenset({"BRK.A", "BRK.B"})),
    ("NWS", frozenset({"NWS", "NWSA"})),
    ("FOXA", frozenset({"FOX", "FOXA"})),
    ("UAA", frozenset({"UA", "UAA"})),
    ("LEN", frozenset({"LEN", "LEN.B"})),
    ("HEI.A", frozenset({"HEI", "HEI.A"})),
    ("FWONA", frozenset({"FWONA", "FWONK"})),
    ("BATRA", frozenset({"BATRA", "BATRK"})),
    ("LBRDA", frozenset({"LBRDA", "LBRDK"})),
    ("LBTYA", frozenset({"LBTYA", "LBTYK"})),
    ("LSXMA", frozenset({"LSXMA", "LSXMK"})),
)

_CANONICAL: dict[str, str] = {}
_PEERS: dict[str, frozenset[str]] = {}
for _canon, _syms in _DUAL_CLASS_GROUPS:
    _PEERS[_canon] = _syms
    for _s in _syms:
        _CANONICAL[_s.upper()] = _canon


def canonical_finnhub_symbol(ticker: str) -> str:
    """Return the preferred (voting / primary SEC) symbol for Finnhub API calls."""
    return _CANONICAL.get(ticker.upper(), ticker.upper())


def finnhub_symbol_candidates(ticker: str) -> list[str]:
    """Symbols to try for Finnhub fundamentals/estimates, canonical first."""
    t = ticker.upper()
    canon = canonical_finnhub_symbol(t)
    peers = _PEERS.get(canon, frozenset({canon}))
    ordered: list[str] = [canon]
    if t != canon:
        ordered.append(t)
    for sym in sorted(peers):
        if sym not in ordered:
            ordered.append(sym)
    return ordered


def shares_dual_class(ticker: str) -> bool:
    """True when *ticker* belongs to a known multi-class group."""
    return ticker.upper() in _CANONICAL
