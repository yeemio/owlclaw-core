"""Add OwlHub core service tables.

Revision ID: 006_owlhub_core
Revises: 005_signal_state
Create Date: 2026-02-25

"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

revision: str = "006_owlhub_core"
down_revision: str | None = "005_signal_state"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pgcrypto")

    op.create_table(
        "skills",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("publisher", sa.String(120), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("license", sa.String(64), nullable=False),
        sa.Column("repository", sa.String(255), nullable=True),
        sa.Column("homepage", sa.String(255), nullable=True),
        sa.Column("state", sa.String(16), nullable=False, server_default="released"),
        sa.Column("tags", ARRAY(sa.String(64)), nullable=False, server_default=sa.text("'{}'::varchar[]")),
        sa.Column("taken_down", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("takedown_reason", sa.Text(), nullable=True),
        sa.Column("takedown_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "publisher", "name", name="uq_skills_tenant_publisher_name"),
    )
    op.create_index("idx_skills_tenant_name", "skills", ["tenant_id", "name"])
    op.create_index("idx_skills_tenant_publisher", "skills", ["tenant_id", "publisher"])
    op.create_index("idx_skills_tags_gin", "skills", ["tags"], postgresql_using="gin")
    op.create_index(op.f("ix_skills_tenant_id"), "skills", ["tenant_id"])

    op.create_table(
        "skill_versions",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("state", sa.String(16), nullable=False, server_default="released"),
        sa.Column("download_url", sa.String(500), nullable=True),
        sa.Column("checksum", sa.String(128), nullable=True),
        sa.Column("metadata_json", JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "skill_id", "version", name="uq_skill_versions_tenant_skill_version"),
    )
    op.create_index("idx_skill_versions_tenant_skill", "skill_versions", ["tenant_id", "skill_id"])
    op.create_index("idx_skill_versions_tenant_version", "skill_versions", ["tenant_id", "version"])
    op.create_index(op.f("ix_skill_versions_tenant_id"), "skill_versions", ["tenant_id"])

    op.create_table(
        "skill_statistics",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("total_downloads", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("total_installs", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("downloads_last_30d", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("last_download_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_install_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("tenant_id", "skill_id", name="uq_skill_statistics_tenant_skill"),
    )
    op.create_index("idx_skill_statistics_tenant_skill", "skill_statistics", ["tenant_id", "skill_id"])
    op.create_index(op.f("ix_skill_statistics_tenant_id"), "skill_statistics", ["tenant_id"])

    op.create_table(
        "review_records",
        sa.Column("id", UUID(as_uuid=True), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("tenant_id", sa.String(64), nullable=False, server_default="default"),
        sa.Column("skill_id", UUID(as_uuid=True), sa.ForeignKey("skills.id", ondelete="CASCADE"), nullable=True),
        sa.Column("publisher", sa.String(120), nullable=False),
        sa.Column("skill_name", sa.String(120), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="pending"),
        sa.Column("comments", sa.Text(), nullable=False, server_default=""),
        sa.Column("reviewer", sa.String(120), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("idx_review_records_tenant_publisher", "review_records", ["tenant_id", "publisher"])
    op.create_index("idx_review_records_tenant_status", "review_records", ["tenant_id", "status"])
    op.create_index("idx_review_records_tenant_submitted", "review_records", ["tenant_id", "submitted_at"])
    op.create_index(op.f("ix_review_records_tenant_id"), "review_records", ["tenant_id"])


def downgrade() -> None:
    op.drop_index(op.f("ix_review_records_tenant_id"), table_name="review_records")
    op.drop_index("idx_review_records_tenant_submitted", table_name="review_records")
    op.drop_index("idx_review_records_tenant_status", table_name="review_records")
    op.drop_index("idx_review_records_tenant_publisher", table_name="review_records")
    op.drop_table("review_records")

    op.drop_index(op.f("ix_skill_statistics_tenant_id"), table_name="skill_statistics")
    op.drop_index("idx_skill_statistics_tenant_skill", table_name="skill_statistics")
    op.drop_table("skill_statistics")

    op.drop_index(op.f("ix_skill_versions_tenant_id"), table_name="skill_versions")
    op.drop_index("idx_skill_versions_tenant_version", table_name="skill_versions")
    op.drop_index("idx_skill_versions_tenant_skill", table_name="skill_versions")
    op.drop_table("skill_versions")

    op.drop_index(op.f("ix_skills_tenant_id"), table_name="skills")
    op.drop_index("idx_skills_tags_gin", table_name="skills")
    op.drop_index("idx_skills_tenant_publisher", table_name="skills")
    op.drop_index("idx_skills_tenant_name", table_name="skills")
    op.drop_table("skills")
