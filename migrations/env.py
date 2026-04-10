"""Alembic environment: uses OWLCLAW_DATABASE_URL and owlclaw.db.Base."""

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection

# Import models so their tables are attached to Base.metadata for Alembic
from owlclaw.agent.memory.store_pgvector import MemoryEntryORM  # noqa: F401
from owlclaw.db import Base
from owlclaw.governance.ledger import LedgerRecord  # noqa: F401
from owlclaw.governance.quality_store import SkillQualitySnapshotORM  # noqa: F401
from owlclaw.owlhub.models import ReviewRecord, Skill, SkillStatistics, SkillVersion  # noqa: F401
from owlclaw.triggers.signal.persistence import AgentControlStateORM, PendingInstructionORM  # noqa: F401
from owlclaw.triggers.webhook.persistence.models import (  # noqa: F401
    WebhookEndpointModel,
    WebhookEventModel,
    WebhookExecutionModel,
    WebhookIdempotencyKeyModel,
    WebhookTransformationRuleModel,
)

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_sync_url() -> str:
    """Get sync PostgreSQL URL for Alembic (psycopg2)."""
    url = os.environ.get("OWLCLAW_DATABASE_URL") or config.get_main_option("sqlalchemy.url")
    if not url or not url.strip():
        raise RuntimeError(
            "Set OWLCLAW_DATABASE_URL or sqlalchemy.url in alembic.ini for migrations."
        )
    url = url.strip()
    if url.startswith("postgresql+asyncpg://"):
        return "postgresql+psycopg2://" + url[len("postgresql+asyncpg://") :]
    if url.startswith("postgresql://"):
        return "postgresql+psycopg2://" + url[len("postgresql://") :]
    if url.startswith("postgresql+psycopg2://"):
        return url
    raise RuntimeError("OWLCLAW_DATABASE_URL must be PostgreSQL.")


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (SQL only, no DB connection)."""
    url = _get_sync_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode (connect to DB)."""
    cfg = config.get_section(config.config_ini_section, {}) or {}
    cfg["sqlalchemy.url"] = _get_sync_url()
    connectable = context.config.attributes.get("connection", None)
    if connectable is None:
        from sqlalchemy import create_engine
        connectable = create_engine(
            cfg["sqlalchemy.url"],
            poolclass=pool.NullPool,
        )
    with connectable.connect() as connection:
        do_run_migrations(connection)


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
