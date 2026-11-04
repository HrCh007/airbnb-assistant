"""Async SQLAlchemy engine over SQLite (aiosqlite).

SQLite runs in-process and is stored in the data volume. Foreign keys are
enforced (they are off by default in SQLite) and WAL mode allows concurrent
reads while a write is in progress.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.core.config import get_settings


class Base(DeclarativeBase):
    pass


_engine: AsyncEngine | None = None
_sessionmaker: async_sessionmaker[AsyncSession] | None = None


def _sqlite_pragmas(dbapi_conn, _record) -> None:
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA busy_timeout=5000")
    cur.close()


def get_engine() -> AsyncEngine:
    global _engine, _sessionmaker
    if _engine is None:
        url = get_settings().resolved_database_url
        _engine = create_async_engine(url, connect_args={"timeout": 30} if url.startswith("sqlite") else {})
        if url.startswith("sqlite"):
            event.listen(_engine.sync_engine, "connect", _sqlite_pragmas)
        _sessionmaker = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_sessionmaker() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _sessionmaker is not None
    return _sessionmaker


async def get_db() -> AsyncIterator[AsyncSession]:
    async with get_sessionmaker()() as session:
        yield session


async def init_db() -> None:
    """Create tables if they don't exist (idempotent)."""
    import app.models  # noqa: F401  (register models)

    async with get_engine().begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _sessionmaker
    if _engine is not None:
        await _engine.dispose()
    _engine = None
    _sessionmaker = None
