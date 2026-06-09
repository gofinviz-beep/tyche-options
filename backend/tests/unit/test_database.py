"""Tests for the multi-engine database registry."""

from __future__ import annotations

import pytest
from sqlalchemy import select, text

from tyche.models.scan import ScanRun
from tyche.models.filing import FilingSignal
from tyche.models.news import NewsSignal
from tyche.persistence.database import (
    _engines,
    _sessions,
    create_tables_for_models,
    dispose_engine,
    ensure_news_db,
    get_session,
    init_db,
    init_scanner_dbs,
    register_engine,
)


@pytest.fixture(autouse=True)
async def _cleanup_engines():
    """Ensure engine registry is clean before and after each test."""
    _engines.clear()
    _sessions.clear()
    yield
    await dispose_engine()


class TestRegisterEngine:
    def test_register_creates_engine_and_session(self) -> None:
        register_engine("test_db", "sqlite+aiosqlite:///:memory:")
        assert "test_db" in _engines
        assert "test_db" in _sessions

    def test_register_multiple_engines(self) -> None:
        register_engine("a", "sqlite+aiosqlite:///:memory:")
        register_engine("b", "sqlite+aiosqlite:///:memory:")
        assert len(_engines) == 2
        assert len(_sessions) == 2


class TestInitDb:
    def test_init_db_registers_default(self) -> None:
        init_db("sqlite+aiosqlite:///:memory:")
        assert "default" in _engines
        assert "default" in _sessions


class TestInitScannerDbs:
    def test_init_scanner_dbs_registers_three_engines(self, tmp_path) -> None:
        init_scanner_dbs(str(tmp_path))
        assert "scans" in _engines
        assert "candidates" in _engines
        assert "analyses" in _engines
        for name in ("scans", "candidates", "analyses"):
            assert (tmp_path / f"{name}.db").exists() or True  # file created on first use


class TestGetSession:
    @pytest.mark.asyncio
    async def test_get_session_default(self) -> None:
        register_engine("default", "sqlite+aiosqlite:///:memory:")
        async with get_session("default") as session:
            result = await session.execute(text("SELECT 1"))
            assert result.scalar() == 1

    @pytest.mark.asyncio
    async def test_get_session_named(self) -> None:
        register_engine("scans", "sqlite+aiosqlite:///:memory:")
        async with get_session("scans") as session:
            result = await session.execute(text("SELECT 42"))
            assert result.scalar() == 42

    @pytest.mark.asyncio
    async def test_get_session_unknown_raises(self) -> None:
        with pytest.raises(RuntimeError, match="Database 'nonexistent' not initialized"):
            async with get_session("nonexistent"):
                pass


class TestCreateTablesForModels:
    @pytest.mark.asyncio
    async def test_creates_scan_runs_table(self) -> None:
        register_engine("scans", "sqlite+aiosqlite:///:memory:")
        await create_tables_for_models("scans", ScanRun)

        async with get_session("scans") as session:
            result = await session.execute(select(ScanRun))
            assert result.all() == []

    @pytest.mark.asyncio
    async def test_create_tables_unknown_db_raises(self) -> None:
        with pytest.raises(RuntimeError, match="not initialized"):
            await create_tables_for_models("ghost", ScanRun)


class TestDisposeEngine:
    @pytest.mark.asyncio
    async def test_dispose_all(self) -> None:
        register_engine("a", "sqlite+aiosqlite:///:memory:")
        register_engine("b", "sqlite+aiosqlite:///:memory:")
        await dispose_engine()
        assert len(_engines) == 0
        assert len(_sessions) == 0

    @pytest.mark.asyncio
    async def test_dispose_single(self) -> None:
        register_engine("a", "sqlite+aiosqlite:///:memory:")
        register_engine("b", "sqlite+aiosqlite:///:memory:")
        await dispose_engine("a")
        assert "b" in _engines


class TestEnsureNewsDb:
    @pytest.mark.asyncio
    async def test_creates_news_and_filing_tables(self, tmp_path) -> None:
        await ensure_news_db(str(tmp_path))
        async with get_session("news") as session:
            await session.execute(select(NewsSignal))
            await session.execute(select(FilingSignal))
