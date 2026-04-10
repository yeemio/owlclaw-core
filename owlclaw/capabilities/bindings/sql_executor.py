"""SQL binding executor implementation."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from owlclaw.capabilities.bindings.credential import CredentialResolver
from owlclaw.capabilities.bindings.executor import BindingExecutor
from owlclaw.capabilities.bindings.schema import BindingConfig, SQLBindingConfig

PARAM_PATTERN = re.compile(r":([A-Za-z_][A-Za-z0-9_]*)")
LINE_COMMENT_PATTERN = re.compile(r"(--[^\n]*|#[^\n]*)")
BLOCK_COMMENT_PATTERN = re.compile(r"/\*.*?\*/", re.DOTALL)
DANGEROUS_SQL_KEYWORDS = re.compile(
    r"\b(insert|update|delete|alter|drop|create|truncate|grant|revoke|merge|replace|call|do)\b",
    re.IGNORECASE,
)


class SessionFactoryProtocol(Protocol):
    """Protocol for async session factory used by SQL executor."""

    def __call__(self) -> AsyncSession: ...


class SQLBindingExecutor(BindingExecutor):
    """Execute SQL bindings with parameterized queries only."""

    def __init__(
        self,
        credential_resolver: CredentialResolver | None = None,
        session_factory_builder: Callable[[str], async_sessionmaker[AsyncSession]] | None = None,
    ) -> None:
        self._credential_resolver = credential_resolver or CredentialResolver()
        self._session_factory_builder = session_factory_builder or self._default_session_factory_builder

    async def execute(self, config: BindingConfig, parameters: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(config, SQLBindingConfig):
            raise TypeError("SQLBindingExecutor requires SQLBindingConfig")
        query = config.query.strip()
        if self._has_string_interpolation(query):
            raise ValueError("SQL binding query must use parameterized placeholders (:param), not string interpolation")

        is_select = self._is_select_query(query)
        if config.read_only and not is_select:
            raise PermissionError("SQL binding is read-only; write query is not allowed")

        bound_parameters = self._build_bound_parameters(query, config.parameter_mapping, parameters)
        if config.mode == "shadow" and not is_select:
            return {
                "status": "shadow",
                "mode": "shadow",
                "executed": False,
            }

        connection = self._credential_resolver.resolve(config.connection)
        session_factory = self._session_factory_builder(connection)
        async with session_factory() as session:
            result = await session.execute(text(query), bound_parameters)
            if is_select:
                columns = list(result.keys())
                rows = result.fetchall()
                truncated = len(rows) > int(config.max_rows)
                rows = rows[: int(config.max_rows)]
                data = [dict(zip(columns, row, strict=False)) for row in rows]
                if config.mode == "shadow":
                    return {
                        "status": "shadow",
                        "mode": "shadow",
                        "executed": True,
                        "row_count": len(data),
                        "truncated": truncated,
                        "column_count": len(columns),
                    }
                return {
                    "status": "ok",
                    "mode": config.mode,
                    "data": data,
                    "row_count": len(data),
                    "truncated": truncated,
                }

            await session.commit()
            affected_rows = getattr(result, "rowcount", None)
            return {
                "status": "ok",
                "mode": config.mode,
                "affected_rows": int(affected_rows or 0),
            }

    def validate_config(self, config: dict[str, Any]) -> list[str]:
        errors: list[str] = []
        if not str(config.get("connection", "")).strip():
            errors.append("SQL binding requires 'connection' field")
        query = str(config.get("query", "")).strip()
        if not query:
            errors.append("SQL binding requires 'query' field")
            return errors
        if self._has_string_interpolation(query):
            errors.append("SQL binding query must use parameterized placeholders (:param), not string interpolation")
        if ":" in query and not isinstance(config.get("parameter_mapping", {}), dict):
            errors.append("SQL binding with parameterized query requires 'parameter_mapping' field")
        return errors

    @property
    def supported_modes(self) -> list[str]:
        return ["active", "shadow"]

    @staticmethod
    def _default_session_factory_builder(connection: str) -> async_sessionmaker[AsyncSession]:
        engine = create_async_engine(connection)
        return async_sessionmaker(engine, expire_on_commit=False)

    @staticmethod
    def _has_string_interpolation(query: str) -> bool:
        lowered = query.lower()
        return "%s" in query or "%(" in query or "f'" in lowered or 'f"' in lowered

    @staticmethod
    def _is_select_query(query: str) -> bool:
        normalized = SQLBindingExecutor._normalize_query_for_readonly_check(query)
        if not normalized:
            return False

        # Fail-close for multi-statement SQL to avoid comment/semicolon bypass.
        stripped = normalized.rstrip(";").strip()
        if ";" in stripped:
            return False

        lowered = stripped.lower()
        if not (lowered.startswith("select") or lowered.startswith("with")):
            return False
        if DANGEROUS_SQL_KEYWORDS.search(lowered):
            return False
        return True

    @staticmethod
    def _normalize_query_for_readonly_check(query: str) -> str:
        without_block_comments = BLOCK_COMMENT_PATTERN.sub(" ", query)
        without_line_comments = LINE_COMMENT_PATTERN.sub(" ", without_block_comments)
        return re.sub(r"\s+", " ", without_line_comments).strip()

    @staticmethod
    def _build_bound_parameters(
        query: str,
        parameter_mapping: dict[str, str],
        parameters: dict[str, Any],
    ) -> dict[str, Any]:
        placeholders = set(PARAM_PATTERN.findall(query))
        if not placeholders:
            return {}
        bound: dict[str, Any] = {}
        for placeholder in placeholders:
            source_name = parameter_mapping.get(placeholder, placeholder)
            if source_name not in parameters:
                raise KeyError(f"Missing parameter '{source_name}' for SQL placeholder :{placeholder}")
            bound[placeholder] = parameters[source_name]
        return bound
