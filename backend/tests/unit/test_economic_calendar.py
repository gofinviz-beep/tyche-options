"""Tests for the economic/macro catalyst calendar."""

from __future__ import annotations

from datetime import date

import pytest

from tyche.market_data.economic_calendar import (
    EconomicCalendar,
    EconomicEvent,
)


class TestEconomicCalendar:
    def test_loads_default_events(self) -> None:
        cal = EconomicCalendar()
        # Should have FOMC + CPI + jobs events for 2026
        all_events = cal.get_events_in_range(date(2026, 1, 1), date(2026, 12, 31))
        assert len(all_events) > 30  # 16 FOMC + 12 CPI + 12 jobs

    def test_fomc_dates_in_range(self) -> None:
        cal = EconomicCalendar()
        jan_events = cal.get_events_in_range(date(2026, 1, 1), date(2026, 1, 31))
        fomc = [e for e in jan_events if e.event_type == "fomc"]
        assert len(fomc) == 2  # Jan 28-29 FOMC

    def test_has_high_impact_event(self) -> None:
        cal = EconomicCalendar()
        # FOMC Jan 28-29
        assert cal.has_high_impact_event(date(2026, 1, 27), date(2026, 1, 30))

    def test_no_event_in_quiet_window(self) -> None:
        cal = EconomicCalendar(fomc_dates=[], cpi_dates=[], jobs_dates=[])
        assert not cal.has_high_impact_event(date(2026, 1, 1), date(2026, 12, 31))

    def test_manual_event_added(self) -> None:
        cal = EconomicCalendar(fomc_dates=[], cpi_dates=[], jobs_dates=[])
        cal.add_event(EconomicEvent(
            event_date=date(2026, 4, 7),
            event_type="custom",
            description="OPEC+ production meeting",
            impact="high",
        ))
        events = cal.get_events_in_range(date(2026, 4, 6), date(2026, 4, 8))
        assert len(events) == 1
        assert events[0].event_type == "custom"

    def test_get_macro_events_for_dte(self) -> None:
        cal = EconomicCalendar()
        events = cal.get_macro_events_for_dte(
            scan_date=date(2026, 1, 27),
            dte=5,
        )
        # Should include FOMC Jan 28-29 within 5-day window
        fomc = [e for e in events if e.event_type == "fomc"]
        assert len(fomc) >= 1

    def test_custom_date_lists(self) -> None:
        cal = EconomicCalendar(
            fomc_dates=[date(2026, 6, 1)],
            cpi_dates=[date(2026, 6, 10)],
            jobs_dates=[date(2026, 6, 5)],
        )
        events = cal.get_events_in_range(date(2026, 6, 1), date(2026, 6, 10))
        assert len(events) == 3
        types = {e.event_type for e in events}
        assert types == {"fomc", "cpi", "jobs"}

    def test_events_sorted_by_date(self) -> None:
        cal = EconomicCalendar()
        events = cal.get_events_in_range(date(2026, 1, 1), date(2026, 12, 31))
        dates = [e.event_date for e in events]
        # Static events should be sorted (manual could be out of order but that's ok)
        static_events = [e for e in events if e.event_type != "custom"]
        static_dates = [e.event_date for e in static_events]
        assert static_dates == sorted(static_dates)

    def test_empty_range_returns_empty(self) -> None:
        cal = EconomicCalendar(fomc_dates=[], cpi_dates=[], jobs_dates=[])
        events = cal.get_events_in_range(date(2026, 1, 1), date(2026, 1, 2))
        assert events == []

    def test_event_dataclass_frozen(self) -> None:
        event = EconomicEvent(
            event_date=date(2026, 1, 28),
            event_type="fomc",
            description="FOMC rate decision",
        )
        with pytest.raises(AttributeError):
            event.event_type = "other"  # type: ignore[misc]
