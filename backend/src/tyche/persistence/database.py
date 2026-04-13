"""SQLAlchemy async engine and session factory with multi-database support.

Supports a registry of named engines for distributed SQLite files.
Each domain (scans, candidates, analyses, intents) can use its own DB file,
reducing blast radius and mapping cleanly to PostgreSQL schemas on migration.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from pathlib import Path

import structlog
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

logger = structlog.get_logger()


class Base(DeclarativeBase):
    """Shared declarative base for all ORM models."""


_engines: dict[str, AsyncEngine] = {}
_sessions: dict[str, async_sessionmaker[AsyncSession]] = {}


def register_engine(name: str, database_url: str) -> None:
    """Register a named async engine and session factory.

    Args:
        name: Logical name (e.g. "default", "scans", "candidates", "analyses").
        database_url: SQLAlchemy-compatible async database URL.
    """
    engine = create_async_engine(database_url, echo=False, pool_pre_ping=True)
    _engines[name] = engine
    _sessions[name] = async_sessionmaker(
        bind=engine, class_=AsyncSession, expire_on_commit=False
    )
    logger.debug("db_engine_registered", name=name, url=database_url)


def init_db(database_url: str) -> None:
    """Initialize the default engine (backward-compatible entrypoint)."""
    register_engine("default", database_url)


def init_scanner_dbs(db_dir: str) -> None:
    """Register scanner-domain engines (scans, candidates, analyses).

    Creates the DB directory if it doesn't exist. Each domain gets its own
    SQLite file under the given directory.
    """
    db_path = Path(db_dir)
    db_path.mkdir(parents=True, exist_ok=True)

    for name in ("scans", "candidates", "analyses"):
        file_path = db_path / f"{name}.db"
        url = f"sqlite+aiosqlite:///{file_path}"
        register_engine(name, url)


def init_conviction_db(db_dir: str) -> None:
    """Register the conviction-domain engine (conviction snapshots + transitions).

    Stores daily EMA conviction snapshots and state transition events in a
    dedicated SQLite file under the given directory.
    """
    db_path = Path(db_dir)
    db_path.mkdir(parents=True, exist_ok=True)
    file_path = db_path / "conviction.db"
    register_engine("conviction", f"sqlite+aiosqlite:///{file_path}")


def init_news_db(db_dir: str) -> None:
    """Register the news-domain engine (news signals).

    Stores aggregate per-ticker news impact signals in a dedicated SQLite file.
    """
    db_path = Path(db_dir)
    db_path.mkdir(parents=True, exist_ok=True)
    file_path = db_path / "news.db"
    register_engine("news", f"sqlite+aiosqlite:///{file_path}")


def init_backtest_db(db_dir: str) -> None:
    """Register the backtest-domain engine (pullback events + profiles).

    Stores historical pullback backtest results in a dedicated SQLite file.
    """
    db_path = Path(db_dir)
    db_path.mkdir(parents=True, exist_ok=True)
    file_path = db_path / "backtest.db"
    register_engine("backtest", f"sqlite+aiosqlite:///{file_path}")


@asynccontextmanager
async def get_session(db: str = "default") -> AsyncGenerator[AsyncSession, None]:
    """Provide an async session as a context manager.

    Args:
        db: Name of the registered engine to use.
    """
    factory = _sessions.get(db)
    if factory is None:
        raise RuntimeError(
            f"Database '{db}' not initialized. "
            f"Available: {list(_sessions.keys())}"
        )
    async with factory() as session:
        yield session


async def get_session_dep() -> AsyncGenerator[AsyncSession, None]:
    """Yield an async session for FastAPI dependency injection (default DB)."""
    async with get_session("default") as session:
        yield session


async def create_tables(db: str = "default") -> None:
    """Create all tables for the given engine (dev convenience — use Alembic in prod)."""
    engine = _engines.get(db)
    if engine is None:
        raise RuntimeError(f"Database '{db}' not initialized.")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def create_tables_for_models(db: str, *model_classes: type) -> None:
    """Create tables only for specific model classes on the given engine."""
    engine = _engines.get(db)
    if engine is None:
        raise RuntimeError(f"Database '{db}' not initialized.")

    tables = [cls.__table__ for cls in model_classes if hasattr(cls, "__table__")]
    if tables:
        async with engine.begin() as conn:
            await conn.run_sync(
                lambda sync_conn: Base.metadata.create_all(
                    sync_conn, tables=tables
                )
            )


async def check_db_health() -> bool:
    """Run a lightweight query against the default engine to verify connectivity."""
    engine = _engines.get("default")
    if engine is None:
        return False
    try:
        from sqlalchemy import text

        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
        return True
    except Exception:
        logger.warning("db_health_check_failed", exc_info=True)
        return False


async def dispose_engine(db: str | None = None) -> None:
    """Dispose one or all engines on shutdown."""
    if db:
        engine = _engines.get(db)
        if engine:
            await engine.dispose()
    else:
        for engine in _engines.values():
            await engine.dispose()
        _engines.clear()
        _sessions.clear()
