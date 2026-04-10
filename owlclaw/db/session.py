"""Async session factory and context manager for OwlClaw."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from owlclaw.db.engine import _map_connection_exception, get_engine

_session_factory_cache: dict[int, async_sessionmaker[AsyncSession]] = {}


def create_session_factory(
    engine: AsyncEngine,
) -> async_sessionmaker[AsyncSession]:
    """Create or reuse an async session factory bound to the engine."""
    key = id(engine)
    cached = _session_factory_cache.get(key)
    if cached is not None:
        return cached
    factory = async_sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
        autocommit=False,
        autoflush=False,
    )
    _session_factory_cache[key] = factory
    return factory


@asynccontextmanager
async def get_session(
    engine: AsyncEngine | None = None,
) -> AsyncIterator[AsyncSession]:
    """Async context manager for a database session.

    Commits on success, rolls back on exception, closes on exit.
    """
    resolved_engine = engine if engine is not None else get_engine()
    factory = create_session_factory(resolved_engine)
    async with factory() as session:
        try:
            yield session
        except Exception:
            try:
                await session.rollback()
            except Exception as rollback_exc:
                raise _map_connection_exception(
                    rollback_exc, str(resolved_engine.url)
                ) from rollback_exc
            raise
        else:
            try:
                await session.commit()
            except Exception as commit_exc:
                raise _map_connection_exception(
                    commit_exc, str(resolved_engine.url)
                ) from commit_exc
