"""Shared test fixtures."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tyche.config import TycheSettings
from tyche.persistence.database import Base

# Import all models so metadata is populated
import tyche.models  # noqa: F401


@pytest.fixture
def settings() -> TycheSettings:
    """Test settings with safe defaults."""
    return TycheSettings(
        tradier_api_token="test-token",
        tradier_account_id="test-account",
        tradier_sandbox=True,
        gemini_api_key="test-gemini-key",
        database_url="sqlite+aiosqlite:///:memory:",
        preview_only_mode=True,
    )


@pytest.fixture
async def db_session() -> AsyncSession:
    """In-memory SQLite session for tests."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    session_factory = async_sessionmaker(
        bind=engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )

    async with session_factory() as session:
        yield session

    await engine.dispose()
