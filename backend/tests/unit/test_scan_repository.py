"""Tests for scan_repository — save, load, history, cleanup round-trips.

All tests use in-memory SQLite engines registered in a fixture, so they're
fast and isolated from production data.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone

import pytest

from tyche.conviction.engine import ConvictionSignal
from tyche.models.scan import LLMAnalysisRecord, ScanCandidate, ScanRun
from tyche.persistence.database import (
    _engines,
    _sessions,
    create_tables_for_models,
    dispose_engine,
    register_engine,
)
from tyche.persistence.scan_repository import (
    cleanup_old_scans,
    load_history,
    load_latest,
    load_scan,
    save_scan,
)
from tyche.schemas.analysis import CSPAnalysis
from tyche.strategy.strategies.base import ScoredCandidate
from tyche.workflow.morning_scan import MorningScanResult, PipelineStage


@pytest.fixture(autouse=True)
async def scanner_dbs():
    """Set up in-memory scanner DBs for each test."""
    _engines.clear()
    _sessions.clear()
    for name in ("scans", "candidates", "analyses"):
        register_engine(name, "sqlite+aiosqlite:///:memory:")
    await create_tables_for_models("scans", ScanRun)
    await create_tables_for_models("candidates", ScanCandidate)
    await create_tables_for_models("analyses", LLMAnalysisRecord)
    yield
    await dispose_engine()


def _make_result(
    *,
    n_candidates: int = 3,
    n_analyses: int = 1,
    scanned_at: datetime | None = None,
) -> MorningScanResult:
    """Build a realistic MorningScanResult for testing."""
    result = MorningScanResult()
    result.scan_id = str(uuid.uuid4())
    result.scanned_at = scanned_at or datetime.now(timezone.utc)
    result.symbols_scanned = 100
    result.pipeline_stages = [
        PipelineStage("Fundamental Screen", 100, 80, detail="Price/volume"),
        PipelineStage("EMA Conviction", 80, 20, detail="8/21 EMA"),
    ]
    result.errors = ["Test warning"]

    for i in range(n_candidates):
        cand = ScoredCandidate(
            symbol=f"TICK{i}",
            option_symbol=f"TICK{i}260410P00050000",
            option_type="put",
            strike=50.0 + i,
            expiration=date(2026, 4, 10),
            dte=12,
            bid=2.50,
            ask=2.80,
            mid=2.65,
            volume=500,
            open_interest=2000,
            implied_volatility=0.35,
            underlying_price=52.0,
            strategy="csp",
            premium_per_contract=250.0,
            collateral_required=5000.0,
            annualized_return_pct=18.5,
            score=7.5 + i * 0.5,
            delta=-0.30,
            theta=-0.05,
        )
        result.csp_candidates.append(cand)

    for i in range(n_analyses):
        analysis = CSPAnalysis(
            ticker=f"TICK{i}",
            assignment_comfort="high",
            assignment_comfort_reasoning="Strong fundamentals",
            thesis="Bullish trend with EMA support",
            recommended_strike=50.0,
            recommended_expiration="2026-04-10",
            target_premium=2.50,
            annualized_return_pct=18.5,
            invalidation="Break below 48",
            confidence="high",
            risks=["Market downturn", "Earnings miss"],
            would_you_hold_if_assigned="Yes, solid company",
            suggested_contracts=5,
            collateral_required=25000.0,
            allocation_mode="diversified",
        )
        result.csp_analyses.append(analysis)

    return result


class TestSaveScan:
    @pytest.mark.asyncio
    async def test_save_returns_scan_id(self) -> None:
        result = _make_result()
        scan_id = await save_scan(result)
        assert scan_id == result.scan_id

    @pytest.mark.asyncio
    async def test_save_persists_candidates(self) -> None:
        result = _make_result(n_candidates=5)
        await save_scan(result)
        loaded = await load_scan(result.scan_id)
        assert loaded is not None
        assert len(loaded["csp_candidates"]) == 5

    @pytest.mark.asyncio
    async def test_save_persists_analyses(self) -> None:
        result = _make_result(n_analyses=2)
        await save_scan(result)
        loaded = await load_scan(result.scan_id)
        assert loaded is not None
        assert len(loaded["llm_analyses"]) == 2
        assert loaded["llm_analyses"][0]["confidence"] == "high"
        assert loaded["llm_analyses"][0]["risks"] == ["Market downturn", "Earnings miss"]

    @pytest.mark.asyncio
    async def test_save_persists_pipeline_stages(self) -> None:
        result = _make_result()
        await save_scan(result)
        loaded = await load_scan(result.scan_id)
        assert loaded is not None
        assert len(loaded["pipeline_stages"]) == 2
        assert loaded["pipeline_stages"][0]["name"] == "Fundamental Screen"
        assert loaded["pipeline_stages"][1]["dropped"] == 60

    @pytest.mark.asyncio
    async def test_save_with_intents_created(self) -> None:
        result = _make_result()
        await save_scan(result, intents_created=3)
        loaded = await load_scan(result.scan_id)
        assert loaded is not None
        assert loaded["intents_created"] == 3

    @pytest.mark.asyncio
    async def test_save_with_config_snapshot(self) -> None:
        result = _make_result()
        config = {"top_n": 100, "strike_range_pct": 15.0}
        await save_scan(result, config_snapshot=config)
        loaded = await load_scan(result.scan_id)
        assert loaded is not None

    @pytest.mark.asyncio
    async def test_save_with_no_candidates(self) -> None:
        result = _make_result(n_candidates=0, n_analyses=0)
        scan_id = await save_scan(result)
        loaded = await load_scan(scan_id)
        assert loaded is not None
        assert loaded["csp_candidates"] == []
        assert loaded["llm_analyses"] == []


class TestLoadLatest:
    @pytest.mark.asyncio
    async def test_load_latest_empty(self) -> None:
        result = await load_latest()
        assert result is None

    @pytest.mark.asyncio
    async def test_load_latest_returns_most_recent(self) -> None:
        old = _make_result()
        old.scanned_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        new = _make_result()
        new.scanned_at = datetime(2026, 3, 15, tzinfo=timezone.utc)

        await save_scan(old)
        await save_scan(new)

        latest = await load_latest()
        assert latest is not None
        assert latest["scan_id"] == new.scan_id


class TestLoadScan:
    @pytest.mark.asyncio
    async def test_load_nonexistent(self) -> None:
        result = await load_scan("nonexistent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_load_existing(self) -> None:
        result = _make_result()
        await save_scan(result)
        loaded = await load_scan(result.scan_id)
        assert loaded is not None
        assert loaded["symbols_scanned"] == 100
        assert loaded["errors"] == ["Test warning"]


class TestLoadHistory:
    @pytest.mark.asyncio
    async def test_history_empty(self) -> None:
        history = await load_history()
        assert history == []

    @pytest.mark.asyncio
    async def test_history_returns_summaries(self) -> None:
        for i in range(3):
            r = _make_result()
            r.scanned_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
            await save_scan(r, intents_created=i)

        history = await load_history(limit=5)
        assert len(history) == 3
        assert history[0]["symbols_scanned"] == 100
        assert "csp_candidate_count" in history[0]
        assert "errors_count" in history[0]

    @pytest.mark.asyncio
    async def test_history_respects_limit(self) -> None:
        for i in range(5):
            r = _make_result()
            r.scanned_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
            await save_scan(r)

        history = await load_history(limit=2)
        assert len(history) == 2

    @pytest.mark.asyncio
    async def test_history_ordered_newest_first(self) -> None:
        for i in range(3):
            r = _make_result()
            r.scanned_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
            await save_scan(r)

        history = await load_history()
        dates = [h["scanned_at"] for h in history]
        assert dates == sorted(dates, reverse=True)


class TestCleanupOldScans:
    @pytest.mark.asyncio
    async def test_no_cleanup_needed(self) -> None:
        for _ in range(3):
            await save_scan(_make_result())
        deleted = await cleanup_old_scans(retention_count=5)
        assert deleted == 0

    @pytest.mark.asyncio
    async def test_cleanup_removes_oldest(self) -> None:
        scan_ids = []
        for i in range(7):
            r = _make_result(n_candidates=2, n_analyses=1)
            r.scanned_at = datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=i)
            await save_scan(r)
            scan_ids.append(r.scan_id)

        deleted = await cleanup_old_scans(retention_count=3)
        assert deleted == 4

        history = await load_history(limit=10)
        assert len(history) == 3
        remaining_ids = {h["scan_id"] for h in history}
        for old_id in scan_ids[:4]:
            assert old_id not in remaining_ids

    @pytest.mark.asyncio
    async def test_cleanup_removes_candidates_and_analyses(self) -> None:
        old = _make_result(n_candidates=3, n_analyses=2)
        old.scanned_at = datetime(2025, 1, 1, tzinfo=timezone.utc)
        await save_scan(old)

        new = _make_result(n_candidates=1, n_analyses=1)
        new.scanned_at = datetime(2026, 6, 1, tzinfo=timezone.utc)
        await save_scan(new)

        await cleanup_old_scans(retention_count=1)

        # Old scan should be gone
        assert await load_scan(old.scan_id) is None

        # New scan should still have its data
        loaded = await load_scan(new.scan_id)
        assert loaded is not None
        assert len(loaded["csp_candidates"]) == 1
        assert len(loaded["llm_analyses"]) == 1


class TestRoundTrip:
    """End-to-end: save → load_latest → verify field fidelity."""

    @pytest.mark.asyncio
    async def test_full_round_trip(self) -> None:
        result = _make_result(n_candidates=3, n_analyses=2)
        result.errors = ["Warning 1", "Warning 2"]
        await save_scan(
            result,
            intents_created=5,
            trigger="scheduled",
            config_snapshot={"top_n": 50, "strike_range_pct": 15.0},
        )

        loaded = await load_latest()
        assert loaded is not None
        assert loaded["scan_id"] == result.scan_id
        assert loaded["symbols_scanned"] == 100
        assert loaded["intents_created"] == 5
        assert loaded["errors"] == ["Warning 1", "Warning 2"]
        assert len(loaded["pipeline_stages"]) == 2
        assert len(loaded["csp_candidates"]) == 3
        assert len(loaded["llm_analyses"]) == 2

        for cand in loaded["csp_candidates"]:
            assert "symbol" in cand
            assert "strike" in cand
            assert "score" in cand
            assert cand["bid"] == 2.50

        for anal in loaded["llm_analyses"]:
            assert "ticker" in anal
            assert "thesis" in anal
            assert anal["confidence"] == "high"
            assert isinstance(anal["risks"], list)
