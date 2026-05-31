"""Policy / capex tailwind calendar for the Demand Conviction engine.

Unlike the macro EconomicCalendar (FOMC/CPI/jobs — point-in-time events that
affect all equities), policy tailwinds are *structural, multi-quarter demand
drivers* tied to a theme/sector/ecosystem: the CHIPS Act onshoring cycle, the
AI-data-center capex supercycle, defense/space appropriations, IRA energy
subsidies, tariff regimes.

These are curated (a small, slowly-changing set maintained by hand from policy
announcements + hyperscaler capex guidance) and expose a per-ticker/per-sector
"policy tailwind" score as of a date, with a polarity from the catalyst
taxonomy. Capex-guidance entries capture upstream demand (a hyperscaler raising
capex is a tailwind for its semi/networking/power suppliers — the propagation
itself is modelled in Phase 3's supply-chain graph).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

import structlog

from tyche.analysis.catalyst_taxonomy import policy_polarity

logger = structlog.get_logger()


@dataclass(frozen=True)
class PolicyTailwind:
    """A structural policy/capex tailwind active over a window."""

    name: str
    policy_tag: str  # key in catalyst_taxonomy.POLICY_TAGS
    start: date
    end: date  # window over which the tailwind is considered active
    strength: float  # 0..1 base intensity of the tailwind
    sectors: tuple[str, ...] = ()  # GICS sector names it applies to
    tickers: tuple[str, ...] = ()  # explicit beneficiary tickers (optional)
    note: str = ""

    def active_on(self, as_of: date) -> bool:
        return self.start <= as_of <= self.end


# Curated tailwinds. Maintained by hand; refresh when policy/capex guidance
# changes. Strength is the base intensity; the polarity comes from the tag.
_TAILWINDS: list[PolicyTailwind] = [
    PolicyTailwind(
        name="AI data-center capex supercycle",
        policy_tag="subsidy",  # demand-side; not literally a subsidy but a structural tailwind
        start=date(2024, 1, 1),
        end=date(2027, 12, 31),
        strength=1.0,
        sectors=("Information Technology",),
        tickers=(
            "NVDA", "AMD", "MU", "AVGO", "TSM", "ASML", "LRCX", "AMAT", "KLAC",
            "ANET", "CIEN", "LITE", "COHR", "VRT", "SMCI", "DELL", "MRVL",
            "APLD", "STX", "WDC", "SNDK",
        ),
        note="Hyperscaler capex (MSFT/GOOG/META/AMZN) cascading to semis, "
        "networking, optical, power, storage.",
    ),
    PolicyTailwind(
        name="CHIPS Act semiconductor onshoring",
        policy_tag="chips_act",
        start=date(2023, 1, 1),
        end=date(2027, 12, 31),
        strength=0.8,
        sectors=("Information Technology",),
        tickers=("INTC", "TSM", "MU", "GFS", "AMAT", "LRCX", "KLAC", "ASML"),
        note="Federal subsidies for domestic fab capacity.",
    ),
    PolicyTailwind(
        name="Defense & space appropriations",
        policy_tag="defense_budget",
        start=date(2024, 1, 1),
        end=date(2027, 12, 31),
        strength=0.7,
        sectors=("Industrials",),
        tickers=("RKLB", "ASTS", "PL", "LMT", "RTX", "LHX", "KTOS", "AVAV"),
        note="Rising defense/space budgets; commercial space procurement.",
    ),
    PolicyTailwind(
        name="IRA clean-energy / hydrogen subsidies",
        policy_tag="subsidy",
        start=date(2023, 1, 1),
        end=date(2026, 12, 31),
        strength=0.5,
        sectors=("Energy", "Utilities", "Industrials"),
        tickers=("PLUG", "BE", "FSLR", "ENPH", "BLDP"),
        note="Production/investment tax credits for clean energy + hydrogen.",
    ),
]


class PolicyEventCalendar:
    """Lookup of structural policy/capex tailwinds by ticker/sector + date."""

    def __init__(self, tailwinds: list[PolicyTailwind] | None = None) -> None:
        self._tailwinds = tailwinds if tailwinds is not None else list(_TAILWINDS)

    @property
    def tailwinds(self) -> list[PolicyTailwind]:
        """The curated tailwinds (read-only view, for vectorised scoring)."""
        return list(self._tailwinds)

    def active_tailwinds(
        self,
        as_of: date,
        ticker: str | None = None,
        sector: str | None = None,
    ) -> list[PolicyTailwind]:
        """Tailwinds active on *as_of* matching the ticker or sector."""
        out: list[PolicyTailwind] = []
        tkr = ticker.upper() if ticker else None
        for tw in self._tailwinds:
            if not tw.active_on(as_of):
                continue
            if tkr and tkr in tw.tickers:
                out.append(tw)
            elif sector and sector in tw.sectors:
                out.append(tw)
        return out

    def policy_score(
        self,
        as_of: date,
        ticker: str | None = None,
        sector: str | None = None,
    ) -> float:
        """Net policy tailwind score in roughly [-1, 1] for a ticker/sector.

        Explicit ticker matches weight full; sector-only matches weight half
        (less specific). Combined as the strongest signed contribution so a
        single strong tailwind isn't diluted by weaker ones.
        """
        tkr = ticker.upper() if ticker else None
        best = 0.0
        for tw in self._tailwinds:
            if not tw.active_on(as_of):
                continue
            pol = policy_polarity(tw.policy_tag)
            if pol == 0.0:
                continue
            if tkr and tkr in tw.tickers:
                contrib = pol * tw.strength
            elif sector and sector in tw.sectors:
                contrib = pol * tw.strength * 0.5
            else:
                continue
            if abs(contrib) > abs(best):
                best = contrib
        return max(-1.0, min(1.0, best))
