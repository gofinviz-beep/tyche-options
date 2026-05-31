"""Demand-catalyst and policy taxonomy for the Demand Conviction engine.

The v1 news classifier scores generic event types + an impact in [-1, 1]. The
Demand Conviction engine needs to know *what kind of demand signal* an event
represents (a design win and a buyback are both "positive" but mean very
different things for forward demand) and whether a policy tailwind/headwind
applies (export controls, CHIPS, defense budgets, tariffs).

This module is the single source of truth for those two enums plus their
demand polarity priors, so the classifier prompt, the catalyst feature
aggregation, and the regime scorer all agree.
"""

from __future__ import annotations

# Demand catalysts — events that change forward *demand* for a company's
# products/services. Polarity prior in [-1, 1]: how the catalyst typically
# moves true demand (the classifier still scores magnitude per event).
DEMAND_CATALYSTS: dict[str, float] = {
    "demand_acceleration": 1.0,  # bookings/backlog/orders accelerating
    "design_win": 0.9,  # locked into a customer's next-gen product
    "contract_award": 0.9,  # large multi-year contract / award
    "capacity_expansion": 0.6,  # new fab/plant/capacity (demand-led)
    "capex_guidance_up": 0.8,  # a *customer* raised capex (upstream demand)
    "supply_shortage": 0.7,  # pricing power from tight supply
    "price_increase": 0.5,  # ASPs rising
    "guidance_raise": 0.9,  # company raised its own forward guidance
    "partnership": 0.6,  # strategic partnership expanding TAM
    "new_product": 0.5,  # product launch opening new demand
    "buyback": 0.2,  # capital return (weakly demand-relevant)
    "guidance_cut": -0.9,  # forward guidance lowered
    "demand_deceleration": -1.0,  # orders/bookings slowing
    "capex_guidance_down": -0.8,  # a customer cut capex (demand headwind)
    "none": 0.0,
}

# Policy / regulatory tags — macro tailwinds or headwinds that propagate to a
# sector or ecosystem (not company-specific). Polarity prior in [-1, 1].
POLICY_TAGS: dict[str, float] = {
    "chips_act": 0.8,  # semiconductor subsidies / onshoring
    "defense_budget": 0.7,  # defense/space appropriations up
    "subsidy": 0.6,  # IRA / energy / industrial subsidy
    "tariff_protection": 0.4,  # tariffs that protect a domestic player
    "deregulation": 0.4,
    "export_controls": -0.5,  # can cut demand (China bans) — context-dependent
    "tariff_cost": -0.5,  # tariffs that raise input costs
    "antitrust": -0.6,
    "regulatory_crackdown": -0.7,
    "none": 0.0,
}

DEMAND_CATALYST_NAMES: tuple[str, ...] = tuple(DEMAND_CATALYSTS.keys())
POLICY_TAG_NAMES: tuple[str, ...] = tuple(POLICY_TAGS.keys())


def demand_polarity(catalyst: str) -> float:
    """Polarity prior for a demand catalyst (0.0 for unknown/none)."""
    return DEMAND_CATALYSTS.get((catalyst or "none").lower(), 0.0)


def policy_polarity(tag: str) -> float:
    """Polarity prior for a policy tag (0.0 for unknown/none)."""
    return POLICY_TAGS.get((tag or "none").lower(), 0.0)


def signed_catalyst_impact(catalyst: str, impact_score: float) -> float:
    """Combine the event's signed impact with the catalyst's demand polarity.

    A high-magnitude event with a strong positive demand catalyst yields a
    strong positive demand signal; a positive-impact buyback yields a weak one.
    Returns a value in roughly [-1, 1].
    """
    pol = demand_polarity(catalyst)
    if pol == 0.0:
        return 0.0
    # Use the magnitude of the event impact, signed by the catalyst polarity.
    return max(-1.0, min(1.0, abs(impact_score) * pol))


def signed_policy_impact(tag: str, impact_score: float) -> float:
    """Combine the event's signed impact with the policy tag's polarity."""
    pol = policy_polarity(tag)
    if pol == 0.0:
        return 0.0
    return max(-1.0, min(1.0, abs(impact_score) * pol))
