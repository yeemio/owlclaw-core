"""Async engine creation and lifecycle for OwlClaw."""

import os
from ssl import create_default_context
from typing import Any
from urllib.parse import urlparse

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from owlclaw.db.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DatabaseConnectionError,
)

_engines: dict[tuple[str, str], AsyncEngine] = {}


def _extract_connection_fields(url: str) -> tuple[str, int, str, str]:
    parsed = urlparse(url)
    host = parsed.hostname or "localhost"
    port = int(parsed.port or 5432)
    user = parsed.username or "unknown"
    database = parsed.path.lstrip("/") or "unknown"
    return host, port, user, database


def _map_connection_exception(exc: Exception, url: str) -> DatabaseConnectionError | AuthenticationError:
    host, port, user, database = _extract_connection_fields(url)
    message = str(exc).lower()
    if "auth" in message or "password" in message:
        return AuthenticationError(user=user, database=database)
    return DatabaseConnectionError(host=host, port=port, message=str(exc))


def _normalize_ssl_mode(ssl_mode: str | None) -> str:
    if ssl_mode is not None:
        mode = ssl_mode.strip().lower()
        if not mode:
            raise ConfigurationError("ssl_mode must not be blank when explicitly provided")
        return mode
    raw_mode = os.environ.get("OWLCLAW_DB_SSL_MODE")
    return (raw_mode or "").strip().lower()


def _resolve_ssl_connect_args(ssl_mode: str | None) -> dict[str, Any]:
    mode = _normalize_ssl_mode(ssl_mode)
    if not mode:
        return {}
    if mode == "disable":
        return {"ssl": False}
    if mode in {"allow", "prefer", "require"}:
        return {"ssl": True}
    if mode in {"verify-ca", "verify-full"}:
        return {"ssl": create_default_context()}
    raise ConfigurationError(
        "ssl_mode must be one of: disable, allow, prefer, require, verify-ca, verify-full"
    )


def _normalize_url(url: str) -> str:
    """Ensure URL uses postgresql+asyncpg driver."""
    u = url.strip()
    if u.startswith("postgresql://"):
        return "postgresql+asyncpg://" + u[len("postgresql://") :]
    if u.startswith("postgresql+asyncpg://"):
        return u
    raise ConfigurationError(
        "Database URL must be PostgreSQL (postgresql:// or postgresql+asyncpg://)."
    )


def _get_url(database_url: str | None) -> str:
    """Resolve database URL from argument or environment."""
    if database_url is not None and database_url != "":
        return _normalize_url(database_url)
    url = os.environ.get("OWLCLAW_DATABASE_URL")
    if not url or not url.strip():
        raise ConfigurationError(
            "Database URL not set. Set OWLCLAW_DATABASE_URL or pass database_url."
        )
    return _normalize_url(url)


def create_engine(
    database_url: str | None = None,
    *,
    pool_size: int = 20,
    max_overflow: int = 10,
    pool_timeout: float = 30.0,
    pool_recycle: int = 1800,
    pool_pre_ping: bool = True,
    echo: bool = False,
    ssl_mode: str | None = None,
) -> AsyncEngine:
    """Create an async database engine.

    Args:
        database_url: PostgreSQL URL (postgresql+asyncpg://...).
            If None, uses OWLCLAW_DATABASE_URL.
        pool_size: Connection pool size.
        max_overflow: Extra connections beyond pool_size when busy.
        pool_timeout: Seconds to wait for a connection.
        pool_recycle: Seconds after which connections are recycled.
        pool_pre_ping: Ping connections before use.
        echo: Log SQL (for development).
        ssl_mode: SSL mode. Supports disable/allow/prefer/require/verify-ca/verify-full.

    Returns:
        Configured AsyncEngine.

    Raises:
        ConfigurationError: URL missing or invalid.
    """
    url = _get_url(database_url)
    connect_args = _resolve_ssl_connect_args(ssl_mode)
    kwargs: dict[str, Any] = dict(
        pool_size=pool_size,
        max_overflow=max_overflow,
        pool_timeout=pool_timeout,
        pool_recycle=pool_recycle,
        pool_pre_ping=pool_pre_ping,
        echo=echo,
    )
    if connect_args:
        kwargs["connect_args"] = connect_args
    try:
        return create_async_engine(url, **kwargs)
    except ConfigurationError:
        raise
    except (OperationalError, InterfaceError) as exc:
        raise _map_connection_exception(exc, url) from exc


def get_engine(database_url: str | None = None, ssl_mode: str | None = None) -> AsyncEngine:
    """Get or create a cached engine for the given URL.

    Same URL returns the same engine instance.

    Args:
        database_url: Optional URL; if None, uses OWLCLAW_DATABASE_URL.
        ssl_mode: Optional SSL mode override.

    Returns:
        AsyncEngine instance.

    Raises:
        ConfigurationError: URL missing or invalid.
    """
    url = _get_url(database_url)
    mode = _normalize_ssl_mode(ssl_mode)
    cache_key = (url, mode)
    if cache_key not in _engines:
        _engines[cache_key] = create_engine(url, ssl_mode=mode or None)
    return _engines[cache_key]


async def dispose_engine(database_url: str | None = None) -> None:
    """Dispose engine and close all connections.

    Args:
        database_url: Which engine to dispose; if None, disposes all.
    """
    if database_url is None:
        for key in list(_engines.keys()):
            await _engines[key].dispose()
            del _engines[key]
        return
    url = _get_url(database_url)
    keys = [cache_key for cache_key in _engines.keys() if cache_key[0] == url]
    for key in keys:
        await _engines[key].dispose()
        del _engines[key]
