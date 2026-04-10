"""Settings provider implementation for console backend."""

from __future__ import annotations

import os
import platform
from datetime import datetime, timezone
from importlib import metadata as importlib_metadata
from typing import Any

from sqlalchemy import text

from owlclaw.db import get_engine
from owlclaw.db.session import create_session_factory

SENSITIVE_KEYWORDS: tuple[str, ...] = ("token", "secret", "password", "key")


class DefaultSettingsProvider:
    """Read runtime settings and system metadata for console."""

    async def get_settings(self, tenant_id: str) -> dict[str, Any]:
        _ = tenant_id
        runtime_env = {
            "OWLCLAW_CONSOLE_TOKEN": os.getenv("OWLCLAW_CONSOLE_TOKEN"),
            "OWLCLAW_CONSOLE_CORS_ORIGINS": os.getenv("OWLCLAW_CONSOLE_CORS_ORIGINS"),
            "OWLCLAW_DATABASE_URL": os.getenv("OWLCLAW_DATABASE_URL"),
            "HATCHET_SERVER_URL": os.getenv("HATCHET_SERVER_URL"),
            "LANGFUSE_HOST": os.getenv("LANGFUSE_HOST"),
        }
        masked_env = {key: self._mask_if_sensitive(key, value) for key, value in runtime_env.items()}
        return {
            "runtime": {
                "console_enabled": True,
                "env": masked_env,
            },
            "mcp": {
                "enabled": bool(os.getenv("OWLCLAW_MCP_ENABLED", "").strip()),
                "connected_clients": [],
            },
            "database": {
                "migration_version": await self._get_alembic_version(),
            },
            "owlhub": {
                "connected": bool(os.getenv("OWLHUB_API_URL", "").strip()),
                "installed_skills": [],
            },
        }

    async def get_system_info(self) -> dict[str, Any]:
        return {
            "version": self._get_package_version(),
            "build_time": datetime.now(timezone.utc).isoformat(),
            "commit_hash": os.getenv("OWLCLAW_COMMIT_HASH", "unknown"),
            "platform": platform.platform(),
            "python_version": platform.python_version(),
        }

    async def _get_alembic_version(self) -> str | None:
        try:
            engine = get_engine()
            session_factory = create_session_factory(engine)
            async with session_factory() as session:
                result = await session.execute(text("SELECT version_num FROM alembic_version LIMIT 1"))
                row = result.first()
                if row is None:
                    return None
                return str(row[0])
        except Exception:
            return None

    def _get_package_version(self) -> str:
        try:
            return importlib_metadata.version("owlclaw")
        except importlib_metadata.PackageNotFoundError:
            return "unknown"

    def _mask_if_sensitive(self, key: str, value: str | None) -> str | None:
        if value is None:
            return None
        normalized_key = key.strip().lower()
        if any(keyword in normalized_key for keyword in SENSITIVE_KEYWORDS):
            return "***"
        return value
