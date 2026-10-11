"""add organization context tables and process scope

Revision ID: e7f8a9b0c1d2
Revises: d5e6f7a8b9c0
"""

from alembic import op
import sqlalchemy as sa
from pgvector.sqlalchemy import Vector


revision = "e7f8a9b0c1d2"
down_revision = "d5e6f7a8b9c0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Epic 27.
    op.create_table(
        "organizations",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("industry", sa.String(), nullable=True),
        sa.Column("size", sa.String(), nullable=True),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_table(
        "context_entities",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("code", sa.String(), nullable=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("data", sa.JSON(), nullable=False),
        sa.Column("applies_to", sa.JSON(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("citations", sa.JSON(), nullable=False),
        sa.Column("source", sa.String(), nullable=False),
        sa.Column("parent_id", sa.String(), sa.ForeignKey("context_entities.id", ondelete="SET NULL"), nullable=True),
        sa.Column(
            "supersedes_id", sa.String(), sa.ForeignKey("context_entities.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column("relaxes", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("owner_business_unit_id", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(), nullable=True),
    )
    op.create_index("ix_context_entities_kind_status", "context_entities", ["kind", "status"])
    op.create_index("ix_context_entities_parent_id", "context_entities", ["parent_id"])
    op.create_table(
        "context_entity_versions",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "entity_id", sa.String(), sa.ForeignKey("context_entities.id", ondelete="CASCADE"), nullable=False
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("changed_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("changed_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
        sa.UniqueConstraint("entity_id", "version"),
    )
    op.create_index("ix_context_entity_versions_entity_id", "context_entity_versions", ["entity_id"])
    op.create_table(
        "context_documents",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column("filename", sa.String(), nullable=False),
        sa.Column("content_type", sa.String(), nullable=False),
        sa.Column("size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column(
            "policy_entity_id",
            sa.String(),
            sa.ForeignKey("context_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("proposal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("content_hash", sa.String(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.Column("created_by", sa.String(), sa.ForeignKey("users.id", ondelete="SET NULL"), nullable=True),
    )
    op.create_table(
        "policy_passages",
        sa.Column("id", sa.String(), primary_key=True),
        sa.Column(
            "document_id", sa.String(), sa.ForeignKey("context_documents.id", ondelete="CASCADE"), nullable=True
        ),
        sa.Column(
            "policy_entity_id",
            sa.String(),
            sa.ForeignKey("context_entities.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("policy_version", sa.Integer(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("clause_ref", sa.String(), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("embedding", Vector(384), nullable=False),
        sa.Column("applies_to", sa.JSON(), nullable=False),
        sa.Column("current", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_policy_passages_document_id", "policy_passages", ["document_id"])
    op.create_index("ix_policy_passages_policy_entity_id", "policy_passages", ["policy_entity_id"])

    # US27.2: additive scope on processes; defaults = organization-wide.
    op.add_column(
        "processes",
        sa.Column(
            "business_unit_id",
            sa.String(),
            sa.ForeignKey("context_entities.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.add_column("processes", sa.Column("jurisdictions", sa.JSON(), nullable=False, server_default="[]"))
    op.add_column("processes", sa.Column("process_type", sa.String(), nullable=True))
    op.add_column("processes", sa.Column("data_classes", sa.JSON(), nullable=False, server_default="[]"))


def downgrade() -> None:
    op.drop_column("processes", "data_classes")
    op.drop_column("processes", "process_type")
    op.drop_column("processes", "jurisdictions")
    op.drop_column("processes", "business_unit_id")
    op.drop_table("policy_passages")
    op.drop_table("context_documents")
    op.drop_table("context_entity_versions")
    op.drop_table("context_entities")
    op.drop_table("organizations")
