"""Add memory_entries table for Agent LTM (pgvector).

Revision ID: 003_memory
Revises: 002_ledger
Create Date: 2026-02-22

"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "003_memory"
down_revision: str | None = "002_ledger"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "memory_entries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("agent_id", sa.String(255), nullable=False),
        sa.Column(
            "tenant_id",
            sa.String(255),
            nullable=False,
            server_default="default",
        ),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(1536), nullable=True),
        sa.Column("tags", JSONB, nullable=False, server_default=sa.text("'[]'::jsonb")),
        sa.Column(
            "security_level",
            sa.String(20),
            nullable=False,
            server_default="internal",
        ),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("accessed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("access_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("archived", sa.Boolean(), nullable=False, server_default=sa.text("false")),
    )
    op.execute("COMMENT ON TABLE memory_entries IS 'Agent long-term memory entries (vector + metadata).'")
    op.create_check_constraint(
        "ck_memory_entries_content_length",
        "memory_entries",
        "char_length(content) <= 2000",
    )
    op.create_check_constraint(
        "ck_memory_entries_security_level",
        "memory_entries",
        "security_level IN ('public','internal','confidential','restricted')",
    )
    op.create_index(
        "idx_memory_embedding",
        "memory_entries",
        ["embedding"],
        postgresql_using="hnsw",
        postgresql_with={"m": 16, "ef_construction": 64},
        postgresql_ops={"embedding": "vector_cosine_ops"},
    )
    op.create_index("idx_memory_agent", "memory_entries", ["agent_id", "tenant_id"])
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_memory_created "
        "ON memory_entries (created_at DESC)"
    )
    op.create_index("idx_memory_tags", "memory_entries", ["tags"], postgresql_using="gin")
    op.create_index(
        "idx_memory_archived",
        "memory_entries",
        ["archived"],
        postgresql_where=sa.text("NOT archived"),
    )


def downgrade() -> None:
    op.drop_index("idx_memory_archived", "memory_entries")
    op.drop_index("idx_memory_tags", "memory_entries", postgresql_using="gin")
    op.drop_index("idx_memory_created", "memory_entries")
    op.drop_index("idx_memory_agent", "memory_entries")
    op.drop_index("idx_memory_embedding", "memory_entries", postgresql_using="hnsw")
    op.drop_constraint("ck_memory_entries_security_level", "memory_entries", type_="check")
    op.drop_constraint("ck_memory_entries_content_length", "memory_entries", type_="check")
    op.drop_table("memory_entries")
