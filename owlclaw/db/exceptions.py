"""Database-related exceptions for OwlClaw.

All exceptions avoid exposing sensitive data (e.g. passwords) in messages.
"""


class DatabaseError(Exception):
    """Base exception for database operations."""

    pass


class ConfigurationError(DatabaseError):
    """Raised when database configuration is invalid or missing."""

    pass


class DatabaseConnectionError(DatabaseError):
    """Raised when connection to the database server fails."""

    def __init__(self, host: str, port: int, message: str) -> None:
        self.host = host
        self.port = port
        super().__init__(f"Connection failed to {host}:{port} — {message}")


class AuthenticationError(DatabaseError):
    """Raised when database authentication fails. Does not expose passwords."""

    def __init__(self, user: str, database: str) -> None:
        self.user = user
        self.database = database
        super().__init__(
            f"Authentication failed for user '{user}' accessing database '{database}'"
        )


class PoolTimeoutError(DatabaseError):
    """Raised when the connection pool cannot provide a connection in time."""

    def __init__(self, pool_size: int, max_overflow: int, timeout: float) -> None:
        self.pool_size = pool_size
        self.max_overflow = max_overflow
        self.timeout = timeout
        super().__init__(
            f"Pool timeout: could not get connection within {timeout}s "
            f"(pool_size={pool_size}, max_overflow={max_overflow})"
        )
