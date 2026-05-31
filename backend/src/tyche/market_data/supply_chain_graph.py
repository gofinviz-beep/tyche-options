"""Curated supply-chain / ecosystem demand-propagation graph (D-GRAPH).

The Demand Conviction thesis: demand starts upstream (hyperscaler capex,
defense/space appropriations) and *cascades downstream* through the supply
chain. A hyperscaler raising capex is a leading indicator for its semi /
networking / optical / power / storage suppliers — often weeks before the
supplier's own fundamentals or price confirm it.

This module encodes a small, hand-curated directed graph of
``customer -> supplier`` edges for the two ecosystems that drive the current
basket (AI infrastructure + space/defense). Each edge carries a weight (how
strongly the customer's demand drives the supplier) and a relationship type.

It is the deterministic, inspectable D-GRAPH foundation. The GNN described in
[docs/gnn-architecture.md](../../../docs/gnn-architecture.md) slots in later as
a learned replacement for the fixed propagation weights — this module's
``edges`` / ``customers_of`` API is exactly the adjacency the GNN consumes, so
the feature pipeline does not change when the GNN is enabled.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class SupplyEdge:
    """A directed demand edge: ``customer``'s demand drives ``supplier``."""

    customer: str
    supplier: str
    weight: float  # 0..1 — how strongly the customer drives supplier demand
    relationship: str  # e.g. "capex", "foundry", "component", "procurement"
    ecosystem: str  # "ai_infra" | "space_defense"


# Curated edges. Hand-maintained from 10-K customer concentration disclosures,
# earnings-call supply-chain commentary, and known design wins. Weight reflects
# revenue concentration / how leading the customer's demand is for the supplier.
_EDGES: list[SupplyEdge] = [
    # ── AI infrastructure: hyperscaler capex -> accelerator / systems ──────
    *[
        SupplyEdge(c, s, w, "capex", "ai_infra")
        for c in ("MSFT", "GOOGL", "AMZN", "META", "ORCL")
        for s, w in (
            ("NVDA", 0.9), ("AMD", 0.6), ("AVGO", 0.7), ("ANET", 0.7),
            ("VRT", 0.6), ("DELL", 0.5), ("SMCI", 0.6), ("ARM", 0.4),
            ("STX", 0.4), ("WDC", 0.4),
        )
    ],
    # ── Accelerator vendors -> upstream semi-cap / memory / optical ───────
    *[
        SupplyEdge("NVDA", s, w, "component", "ai_infra")
        for s, w in (
            ("TSM", 0.9), ("ASML", 0.6), ("MU", 0.7), ("AMAT", 0.6),
            ("LRCX", 0.6), ("KLAC", 0.6), ("COHR", 0.6), ("LITE", 0.6),
            ("CIEN", 0.5), ("VRT", 0.6), ("ANET", 0.5), ("CLS", 0.6),
            ("SMCI", 0.6), ("APLD", 0.5),
        )
    ],
    *[
        SupplyEdge("AMD", s, w, "component", "ai_infra")
        for s, w in (("TSM", 0.7), ("AMAT", 0.4), ("LRCX", 0.4), ("KLAC", 0.4))
    ],
    SupplyEdge("AVGO", "TSM", 0.6, "foundry", "ai_infra"),
    # ── Foundry -> semi-cap / materials ───────────────────────────────────
    *[
        SupplyEdge("TSM", s, w, "semicap", "ai_infra")
        for s, w in (("ASML", 0.8), ("AMAT", 0.7), ("LRCX", 0.7), ("KLAC", 0.7), ("ENTG", 0.5))
    ],
    # ── Data-center power / cooling ───────────────────────────────────────
    *[
        SupplyEdge(c, s, w, "power", "ai_infra")
        for c in ("NVDA", "MSFT", "GOOGL", "AMZN", "META")
        for s, w in (("VRT", 0.5), ("ETN", 0.4), ("POWL", 0.4), ("GEV", 0.4))
    ],
    # ── Space / defense: appropriations & primes -> suppliers ─────────────
    *[
        SupplyEdge(c, s, w, "procurement", "space_defense")
        for c in ("LMT", "RTX", "NOC", "GD")
        for s, w in (("RKLB", 0.4), ("KTOS", 0.4), ("AVAV", 0.4))
    ],
    SupplyEdge("AMZN", "RKLB", 0.3, "procurement", "space_defense"),  # Kuiper launch demand
    SupplyEdge("ASTS", "RKLB", 0.3, "procurement", "space_defense"),
]


class SupplyChainGraph:
    """Inspectable customer->supplier demand graph with neighbour queries."""

    def __init__(self, edges: list[SupplyEdge] | None = None) -> None:
        self._edges = edges if edges is not None else list(_EDGES)
        # supplier -> [(customer, weight, ecosystem)]
        self._customers: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        # customer -> [(supplier, weight, ecosystem)]
        self._suppliers: dict[str, list[tuple[str, float, str]]] = defaultdict(list)
        for e in self._edges:
            self._customers[e.supplier.upper()].append(
                (e.customer.upper(), e.weight, e.ecosystem)
            )
            self._suppliers[e.customer.upper()].append(
                (e.supplier.upper(), e.weight, e.ecosystem)
            )

    @property
    def edges(self) -> list[SupplyEdge]:
        return list(self._edges)

    def customers_of(self, supplier: str) -> list[tuple[str, float]]:
        """Upstream customers whose demand leads *supplier* (ticker, weight)."""
        return [(c, w) for c, w, _ in self._customers.get(supplier.upper(), [])]

    def suppliers_of(self, customer: str) -> list[tuple[str, float]]:
        """Downstream suppliers driven by *customer*'s demand (ticker, weight)."""
        return [(s, w) for s, w, _ in self._suppliers.get(customer.upper(), [])]

    def all_tickers(self) -> set[str]:
        out: set[str] = set()
        for e in self._edges:
            out.add(e.customer.upper())
            out.add(e.supplier.upper())
        return out

    def has_customers(self, supplier: str) -> bool:
        return bool(self._customers.get(supplier.upper()))
