"""Economic / macro catalyst calendar.

Provides awareness of major market-moving events beyond earnings:
- FOMC rate decisions (published a year in advance by the Fed)
- CPI / PPI / jobs reports (BLS monthly schedule)
- Manual overrides for ad-hoc catalysts (OPEC, SEC filings, etc.)

Used by the scanner pipeline to flag tickers with macro events in the
DTE window. Unlike earnings (per-ticker), these affect all equities.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import structlog

logger = structlog.get_logger()


@dataclass(frozen=True)
class EconomicEvent:
    """A single scheduled macro event."""

    event_date: date
    event_type: str  # fomc, cpi, ppi, jobs, custom
    description: str
    impact: str = "high"  # high, medium, low


# Fed publishes FOMC meeting dates a year in advance.
# 2026 dates from https://www.federalreserve.gov/monetarypolicy/fomccalendars.htm
_FOMC_2026 = [
    date(2026, 1, 28),
    date(2026, 1, 29),
    date(2026, 3, 18),
    date(2026, 3, 19),
    date(2026, 5, 6),
    date(2026, 5, 7),
    date(2026, 6, 17),
    date(2026, 6, 18),
    date(2026, 7, 29),
    date(2026, 7, 30),
    date(2026, 9, 16),
    date(2026, 9, 17),
    date(2026, 10, 28),
    date(2026, 10, 29),
    date(2026, 12, 9),
    date(2026, 12, 10),
]

# CPI is typically released ~2nd Tuesday of each month.
# PPI is typically released the day before CPI.
# Jobs report (NFP) is typically the first Friday.
# 2026 dates are approximate based on BLS schedule patterns.
_CPI_2026 = [
    date(2026, 1, 14),
    date(2026, 2, 11),
    date(2026, 3, 11),
    date(2026, 4, 14),
    date(2026, 5, 12),
    date(2026, 6, 10),
    date(2026, 7, 14),
    date(2026, 8, 12),
    date(2026, 9, 15),
    date(2026, 10, 13),
    date(2026, 11, 10),
    date(2026, 12, 15),
]

_JOBS_2026 = [
    date(2026, 1, 9),
    date(2026, 2, 6),
    date(2026, 3, 6),
    date(2026, 4, 3),
    date(2026, 5, 8),
    date(2026, 6, 5),
    date(2026, 7, 2),
    date(2026, 8, 7),
    date(2026, 9, 4),
    date(2026, 10, 2),
    date(2026, 11, 6),
    date(2026, 12, 4),
]


class EconomicCalendar:
    """Provides macro event awareness for the CSP scanner pipeline.

    Events are loaded from static yearly calendars (FOMC, CPI, jobs)
    plus optional manual overrides for ad-hoc catalysts.
    """

    def __init__(
        self,
        fomc_dates: list[date] | None = None,
        cpi_dates: list[date] | None = None,
        jobs_dates: list[date] | None = None,
    ) -> None:
        self._events: list[EconomicEvent] = []
        self._manual_events: list[EconomicEvent] = []

        for d in (_FOMC_2026 if fomc_dates is None else fomc_dates):
            self._events.append(EconomicEvent(
                event_date=d,
                event_type="fomc",
                description="FOMC rate decision",
                impact="high",
            ))

        for d in (_CPI_2026 if cpi_dates is None else cpi_dates):
            self._events.append(EconomicEvent(
                event_date=d,
                event_type="cpi",
                description="CPI inflation report",
                impact="high",
            ))

        for d in (_JOBS_2026 if jobs_dates is None else jobs_dates):
            self._events.append(EconomicEvent(
                event_date=d,
                event_type="jobs",
                description="Non-farm payrolls report",
                impact="high",
            ))

        self._events.sort(key=lambda e: e.event_date)
        logger.info(
            "economic_calendar_loaded",
            total_events=len(self._events),
        )

    def add_event(self, event: EconomicEvent) -> None:
        """Add a manual one-off catalyst (e.g. OPEC meeting, SEC filing)."""
        self._manual_events.append(event)

    def get_events_in_range(
        self,
        start: date,
        end: date,
    ) -> list[EconomicEvent]:
        """Return all events between start and end (inclusive)."""
        all_events = self._events + self._manual_events
        return [
            e for e in all_events
            if start <= e.event_date <= end
        ]

    def has_high_impact_event(
        self,
        start: date,
        end: date,
    ) -> bool:
        """Check if any high-impact event falls in the date range."""
        events = self.get_events_in_range(start, end)
        return any(e.impact == "high" for e in events)

    def get_macro_events_for_dte(
        self,
        scan_date: date | None = None,
        dte: int = 14,
    ) -> list[EconomicEvent]:
        """Get all macro events within the DTE window from scan_date."""
        today = scan_date or date.today()
        end = today + timedelta(days=dte)
        return self.get_events_in_range(today, end)
