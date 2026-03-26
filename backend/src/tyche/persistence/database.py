"""SQLAlchemy async engine and session factory."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def init_db(database_url: str) -> None:
    """Initialize the async engine and session factory.

    Args:
        database_url: SQLAlchemy-compatible async database URL.
    """
    global _engine, _session_factory  # noqa: PLW0603
    _engine = create_async_engine(
        database_url,
        echo=False,
        pool_pre_ping=True,
    )
    _session_factory = async_sessionmaker(
        bind=_engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_session_dep() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for FastAPI dependency injection."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        yield session


@asynccontextmanager
async def get_session() -> AsyncGenerator[AsyncSession, None]:
    """Provide an async session as a context manager for direct use."""
    if _session_factory is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _session_factory() as session:
        yield session


async def create_tables() -> None:
    """Create all tables (dev/test convenience — use Alembic in production)."""
    if _engine is None:
        raise RuntimeError("Database not initialized. Call init_db() first.")
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    """Dispose the engine on shutdown."""
    if _engine is not None:
        await _engine.dispose()
