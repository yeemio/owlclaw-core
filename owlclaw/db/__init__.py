"""OwlClaw database layer: Base, engine, session, exceptions."""

from owlclaw.db.base import Base
from owlclaw.db.engine import create_engine, dispose_engine, get_engine
from owlclaw.db.exceptions import (
    AuthenticationError,
    ConfigurationError,
    DatabaseConnectionError,
    DatabaseError,
    PoolTimeoutError,
)
from owlclaw.db.session import create_session_factory, get_session

__all__ = [
    "Base",
    "create_engine",
    "get_engine",
    "dispose_engine",
    "create_session_factory",
    "get_session",
    "DatabaseError",
    "ConfigurationError",
    "DatabaseConnectionError",
    "AuthenticationError",
    "PoolTimeoutError",
]
