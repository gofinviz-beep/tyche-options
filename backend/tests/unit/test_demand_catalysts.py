"""Tests for Phase 2 demand-catalyst + policy + regime-router additions."""

from __future__ import annotations

from datetime import date

import pandas as pd
import pytest

from tyche.analysis.catalyst_taxonomy import (
    DEMAND_CATALYST_NAMES,
    POLICY_TAG_NAMES,
    demand_polarity,
    policy_polarity,
    signed_catalyst_impact,
    signed_policy_impact,
)
from tyche.market_data.catalyst_store import (
    CatalystSignalStore,
    records_from_classification,
)
from tyche.market_data.policy_calendar import PolicyEventCalendar
from tyche.market_data.supply_chain_graph import SupplyChainGraph
from tyche.ml.features import (
    CATALYST_FEATURE_COLS,
    GRAPH_FEATURE_COLS,
    add_catalyst_features,
    add_graph_features,
)
from tyche.strategy.alpha_engine import (
    REGIME_NARRATIVE,
    REGIME_REVENUE,
    AlphaScoreEngine,
)


class TestTaxonomy:
    def test_polarities_signed(self):
        assert demand_polarity("design_win") > 0
        assert demand_polarity("guidance_cut") < 0
        assert demand_polarity("none") == 0.0
        assert demand_polarity("unknown_thing") == 0.0
        assert policy_polarity("chips_act") > 0
        assert policy_polarity("antitrust") < 0
        assert policy_polarity("none") == 0.0

    def test_signed_impact_uses_magnitude_and_polarity(self):
        # A negative-impact "design_win" still reads positive (polarity dominates
        # sign; magnitude scales it).
        assert signed_catalyst_impact("design_win", 0.8) == pytest.approx(0.72)
        assert signed_catalyst_impact("guidance_cut", 0.5) == pytest.approx(-0.45)
        assert signed_catalyst_impact("none", 0.9) == 0.0
        assert signed_policy_impact("export_controls", 0.6) == pytest.approx(-0.30)
        assert signed_policy_impact("none", 0.9) == 0.0

    def test_none_present_in_names(self):
        assert "none" in DEMAND_CATALYST_NAMES
        assert "none" in POLICY_TAG_NAMES


class TestRecordsFromClassification:
    def test_emits_demand_and_policy_rows(self):
        rows = records_from_classification(
            ticker="mu",
            event_date=date(2026, 1, 5),
            demand_catalyst="design_win",
            policy_tag="chips_act",
            impact_score=0.9,
            source="news",
            ref_id="a1",
        )
        assert len(rows) == 2
        kinds = {r["kind"] for r in rows}
        assert kinds == {"demand", "policy"}
        assert all(r["ticker"] == "MU" for r in rows)

    def test_none_tags_emit_nothing(self):
        rows = records_from_classification(
            ticker="MU",
            event_date=date(2026, 1, 5),
            demand_catalyst="none",
            policy_tag="none",
            impact_score=0.9,
            source="news",
            ref_id="a1",
        )
        assert rows == []


class TestCatalystStore:
    def test_round_trip_and_aggregate(self, tmp_path):
        store = CatalystSignalStore(data_dir=str(tmp_path))
        rows = []
        rows += records_from_classification(
            "MU", date(2026, 1, 1), "design_win", "none", 0.8, "news", "a1"
        )
        rows += records_from_classification(
            "MU", date(2026, 1, 20), "demand_acceleration", "chips_act", 0.9, "news", "a2"
        )
        store.write_records("MU", pd.DataFrame(rows))

        agg = store.aggregate("MU", as_of=date(2026, 1, 25))
        assert agg["cat_demand_score"] > 0
        assert agg["cat_policy_score"] > 0
        assert agg["cat_count_90d"] == 2.0
        assert agg["cat_recency_days"] == 5.0  # latest demand event was 1/20

    def test_point_in_time_excludes_future(self, tmp_path):
        store = CatalystSignalStore(data_dir=str(tmp_path))
        rows = records_from_classification(
            "MU", date(2026, 2, 1), "design_win", "none", 0.8, "news", "a1"
        )
        store.write_records("MU", pd.DataFrame(rows))
        # as_of before the event: no signal leaks back.
        agg = store.aggregate("MU", as_of=date(2026, 1, 15))
        assert agg["cat_demand_score"] == 0.0
        assert agg["cat_count_90d"] == 0.0

    def test_dedup_on_ref_id(self, tmp_path):
        store = CatalystSignalStore(data_dir=str(tmp_path))
        rows = records_from_classification(
            "MU", date(2026, 1, 1), "design_win", "none", 0.8, "news", "a1"
        )
        store.write_records("MU", pd.DataFrame(rows))
        store.write_records("MU", pd.DataFrame(rows))  # same ref_id again
        df = store.read_ticker("MU")
        assert len(df) == 1

    def test_empty_aggregate(self, tmp_path):
        store = CatalystSignalStore(data_dir=str(tmp_path))
        agg = store.aggregate("ZZZ", as_of=date(2026, 1, 1))
        assert agg["cat_demand_score"] == 0.0
        assert agg["cat_count_90d"] == 0.0


class TestPolicyCalendar:
    def test_ticker_tailwind_positive(self):
        cal = PolicyEventCalendar()
        score = cal.policy_score(date(2026, 1, 1), ticker="MU")
        assert score > 0

    def test_sector_tailwind_weaker_than_ticker(self):
        cal = PolicyEventCalendar()
        tkr = cal.policy_score(date(2026, 1, 1), ticker="NVDA")
        sec = cal.policy_score(date(2026, 1, 1), sector="Information Technology")
        assert tkr >= sec > 0

    def test_no_match_zero(self):
        cal = PolicyEventCalendar()
        assert cal.policy_score(date(2026, 1, 1), ticker="ZZZZ") == 0.0

    def test_outside_window_zero(self):
        cal = PolicyEventCalendar()
        assert cal.policy_score(date(2000, 1, 1), ticker="MU") == 0.0


class TestCatalystFeatures:
    def test_columns_added_and_default_zero(self):
        df = pd.DataFrame(
            {"ticker": ["MU", "ZZZ"], "date": [date(2026, 1, 10), date(2026, 1, 10)]}
        )
        out = add_catalyst_features(df, catalyst_store=None, policy_calendar=None)
        for col in CATALYST_FEATURE_COLS:
            assert col in out.columns
        assert (out["cat_demand_score"] == 0.0).all()
        assert (out["cat_policy_score"] == 0.0).all()

    def test_blends_store_and_policy(self, tmp_path):
        store = CatalystSignalStore(data_dir=str(tmp_path))
        rows = records_from_classification(
            "MU", date(2026, 1, 1), "design_win", "none", 0.9, "news", "a1"
        )
        store.write_records("MU", pd.DataFrame(rows))

        df = pd.DataFrame({"ticker": ["MU"], "date": [date(2026, 1, 10)]})
        out = add_catalyst_features(
            df,
            catalyst_store=store,
            policy_calendar=PolicyEventCalendar(),
            sectors={"MU": "Information Technology"},
        )
        assert out["cat_demand_score"].iloc[0] > 0
        # policy comes from the AI/CHIPS tailwinds for MU.
        assert out["cat_policy_score"].iloc[0] > 0


class TestSupplyChainGraph:
    def test_customers_and_suppliers(self):
        g = SupplyChainGraph()
        # NVDA is a supplier to the hyperscalers (capex drives NVDA demand).
        nvda_customers = dict(g.customers_of("NVDA"))
        assert "MSFT" in nvda_customers and nvda_customers["MSFT"] > 0
        # NVDA is a customer of TSM (NVDA demand drives TSM foundry).
        nvda_suppliers = dict(g.suppliers_of("NVDA"))
        assert "TSM" in nvda_suppliers

    def test_unknown_ticker_empty(self):
        g = SupplyChainGraph()
        assert g.customers_of("ZZZZ") == []
        assert g.has_customers("ZZZZ") is False

    def test_all_tickers_nonempty(self):
        assert len(SupplyChainGraph().all_tickers()) > 10


class TestGraphFeatures:
    def test_columns_added_default_zero_without_graph(self):
        df = pd.DataFrame({"ticker": ["TSM"], "date": [date(2026, 1, 10)],
                           "return_63d": [0.2]})
        out = add_graph_features(df, graph=None)
        for c in GRAPH_FEATURE_COLS:
            assert c in out.columns
        assert (out["graph_demand_propagation"] == 0.0).all()

    def test_customer_momentum_propagates_to_supplier(self):
        # NVDA (customer of TSM) has strong momentum + catalyst on this date;
        # TSM should pick up demand propagation from it.
        g = SupplyChainGraph()
        df = pd.DataFrame(
            {
                "ticker": ["NVDA", "TSM"],
                "date": [date(2026, 1, 10), date(2026, 1, 10)],
                "return_63d": [0.40, -0.05],
                "cat_demand_score": [0.8, 0.0],
                "e_eps_revision_90d": [0.10, 0.0],
            }
        )
        out = add_graph_features(df, graph=g)
        tsm = out[out["ticker"] == "TSM"].iloc[0]
        assert tsm["graph_customer_count"] >= 1
        assert tsm["graph_customer_mom"] > 0.0
        assert tsm["graph_demand_propagation"] > 0.0

    def test_no_leakage_across_dates(self):
        # Customer's signal on a later date must not propagate to an earlier one.
        g = SupplyChainGraph()
        df = pd.DataFrame(
            {
                "ticker": ["NVDA", "TSM"],
                "date": [date(2026, 2, 1), date(2026, 1, 1)],
                "return_63d": [0.40, -0.05],
                "cat_demand_score": [0.8, 0.0],
                "e_eps_revision_90d": [0.10, 0.0],
            }
        )
        out = add_graph_features(df, graph=g)
        tsm = out[out["ticker"] == "TSM"].iloc[0]
        # No NVDA row on TSM's date -> no propagation.
        assert tsm["graph_customer_count"] == 0.0
        assert tsm["graph_demand_propagation"] == 0.0


class TestRegimeRouter:
    def _row(self, **overrides) -> pd.DataFrame:
        base = {
            "ticker": "TEST",
            "close": 100.0,
            "return_63d": 0.1,
            "return_126d": 0.25,
            "return_252d": 0.4,
            "rs_63d": 0.05,
            "rs_126d": 0.15,
            "ema_stack_score": 3,
            "slope_accel": 0.1,
            "price_to_200ema_pct": 12.0,
            "pct_off_52w_high": -3.0,
            "breakout_20d": 1,
            "breakout_63d": 1,
            "volume_thrust_ratio": 1.5,
        }
        base.update(overrides)
        return pd.DataFrame([base])

    def test_no_fundamentals_routes_narrative(self):
        engine = AlphaScoreEngine()
        sig = engine.score_from_features(self._row())[0]
        assert sig.regime == REGIME_NARRATIVE
        # No demand data -> multiplier is exactly 1.0 (v1-identical).
        assert sig.demand_multiplier == pytest.approx(1.0)

    def test_recent_fundamentals_routes_revenue(self):
        engine = AlphaScoreEngine()
        row = self._row(f_quarters_since_filing=1.0, f_rev_growth_yoy=0.3)
        sig = engine.score_from_features(row)[0]
        assert sig.regime == REGIME_REVENUE

    def test_stale_fundamentals_routes_narrative(self):
        engine = AlphaScoreEngine()
        row = self._row(f_quarters_since_filing=12.0, f_rev_growth_yoy=0.3)
        sig = engine.score_from_features(row)[0]
        assert sig.regime == REGIME_NARRATIVE

    def test_strong_revenue_demand_boosts_score(self):
        engine = AlphaScoreEngine()
        base = engine.score_from_features(self._row())[0].alpha_score
        row = self._row(
            f_quarters_since_filing=1.0,
            f_rev_growth_yoy=0.6,
            f_rev_accel=0.2,
            f_gross_margin_trend=0.05,
            f_eps_growth_yoy=0.6,
            f_fcf_positive=1.0,
            e_eps_revision_90d=0.12,
            e_rec_score=0.8,
            e_price_target_upside=0.5,
        )
        boosted = engine.score_from_features(row)[0]
        assert boosted.demand_multiplier > 1.0
        assert boosted.alpha_score >= base

    def test_catalyst_drives_narrative_demand(self):
        engine = AlphaScoreEngine()
        row = self._row(cat_demand_score=0.9, cat_policy_score=0.6)
        sig = engine.score_from_features(row)[0]
        assert sig.regime == REGIME_NARRATIVE
        assert sig.demand.catalyst == pytest.approx(0.9)
        assert sig.demand_multiplier > 1.0

    def test_negative_demand_penalizes(self):
        engine = AlphaScoreEngine()
        row = self._row(cat_demand_score=-0.9, cat_policy_score=-0.5)
        sig = engine.score_from_features(row)[0]
        assert sig.demand_multiplier < 1.0

    def test_graph_propagation_boosts_demand(self):
        engine = AlphaScoreEngine()
        base = engine.score_from_features(self._row())[0].demand_multiplier
        row = self._row(graph_demand_propagation=0.9)
        sig = engine.score_from_features(row)[0]
        assert sig.demand_multiplier > base

    def test_to_dict_includes_regime_and_demand(self):
        engine = AlphaScoreEngine()
        d = engine.score_from_features(self._row(cat_demand_score=0.5))[0].to_dict()
        assert d["regime"] in (REGIME_NARRATIVE, REGIME_REVENUE)
        assert "demand" in d and "catalyst" in d["demand"]
        assert "demand_multiplier" in d


class TestAlphaStoreDemandRoundTrip:
    def test_demand_fields_round_trip(self, tmp_path):
        from tyche.market_data.alpha_store import AlphaSignalStore

        engine = AlphaScoreEngine()
        row = pd.DataFrame(
            [
                {
                    "ticker": "NVDA",
                    "close": 100.0,
                    "return_63d": 0.1,
                    "return_126d": 0.25,
                    "ema_stack_score": 3,
                    "cat_demand_score": 0.7,
                    "cat_policy_score": 0.5,
                    "graph_demand_propagation": 0.6,
                }
            ]
        )
        sig = engine.score_from_features(row)[0]
        store = AlphaSignalStore(data_dir=str(tmp_path))
        store.write([sig.to_dict()], as_of=date(2026, 1, 15))

        records, as_of, _ = store.read_latest()
        assert as_of == "2026-01-15"
        rec = records[0]
        # Nested demand re-nests; demand_multiplier stays top-level (no collision).
        assert "demand" in rec and rec["demand"]["catalyst"] == pytest.approx(0.7)
        assert rec["demand_multiplier"] is not None
        assert rec["regime"] in (REGIME_NARRATIVE, REGIME_REVENUE)
        assert rec["factors"]["momentum"] is not None
